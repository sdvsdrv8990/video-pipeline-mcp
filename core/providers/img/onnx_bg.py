"""
core/providers/img/onnx_bg.py — удаление фона локальной моделью (ONNX), без сети и ключей.

## Что делает
Картинка → маска переднего плана → PNG с прозрачным фоном. Модель отдаёт не картинку, а маску
(альфу), поэтому исходные пиксели остаются нетронутыми: вырезается фон, а не перерисовывается кадр.

## Что берётся из данных
Из строки провайдера: модель (`model`), размер входа (`input_size`), порог отсечения (`cutoff`),
оставлять ли фон вместо прозрачности (`bg_color`). Чего в строке нет — не применяется.
"""

from pathlib import Path

from ..adapters import MediaOutcome, MediaRequest
from ..resolver import ProviderError
from .onnx_runtime import OnnxImage


class OnnxBGRemoval(OnnxImage):
    """Синхронный адаптер: картинка → png с прозрачным фоном."""

    def generate(self, request: MediaRequest) -> MediaOutcome:
        if request.source is None:
            raise ProviderError(
                "CONTENT_REJECTED", "Удалять фон не у чего: не передан исходный файл.",
                reason="Для этого вида ресурса input — путь к картинке внутри рабочей области.")
        params = request.params
        model_path = self.registry.require_model(params.get("model"))
        session = self.session(model_path)
        prep = self.preprocessing(model_path)
        image = self.open_image(request.source)
        side = self._input_side(params, prep)
        mask = self.run(session, self.to_tensor(image, prep, (side, side)))
        return MediaOutcome(files=[self._compose(image, mask, params, request.target)],
                            meta={"engine": "onnx", "sync": True,
                                  "model_input": [side, side],
                                  "compute": self.compute(session)})

    def _input_side(self, params: dict, prep: dict) -> int:
        """Размер входа: столбец строки → описание автора модели → сторона исходника."""
        declared = params.get("input_size")
        if isinstance(declared, (int, float)) and declared > 0:
            return self.align(int(declared))
        size = prep.get("size") or {}
        for key in ("shortest_edge", "height", "width"):
            value = size.get(key) if isinstance(size, dict) else None
            if isinstance(value, (int, float)) and value > 0:
                return self.align(int(value))
        return self.align(1024)

    def _compose(self, image, mask, params: dict, target: Path) -> Path:
        """Маска → альфа-канал исходной картинки, в её собственном разрешении."""
        import numpy as np
        from PIL import Image

        alpha = np.asarray(mask, dtype=np.float32)
        while alpha.ndim > 2:                               # (1,1,H,W) и (1,H,W) — к плоской маске
            alpha = alpha[0]
        span = float(alpha.max() - alpha.min())
        # Часть моделей отдаёт логиты, часть — уже 0..1. Нормируем по факту, а не по вере в формат.
        alpha = (alpha - alpha.min()) / span if span > 0 else np.zeros_like(alpha)
        cutoff = params.get("cutoff")
        if isinstance(cutoff, (int, float)) and 0 < float(cutoff) < 1:
            # Резкая граница вместо полупрозрачной каймы: нужна для плашек и оверлеев.
            alpha = (alpha >= float(cutoff)).astype(np.float32)
        alpha_img = Image.fromarray((alpha * 255).astype(np.uint8), mode="L").resize(
            image.size, Image.Resampling.BILINEAR)
        cut = image.convert("RGB")
        colour = str(params.get("bg_color") or "").strip()
        if colour:
            # Фон нужен не всегда прозрачным: под плашку удобнее сразу залитый цветом.
            flat = Image.new("RGB", image.size, colour)
            out = Image.composite(cut, flat, alpha_img)
        else:
            out = cut.convert("RGBA")
            out.putalpha(alpha_img)
        target.parent.mkdir(parents=True, exist_ok=True)
        out.save(target)
        return target
