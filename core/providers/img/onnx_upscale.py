"""
core/providers/img/onnx_upscale.py — увеличение картинки локальной моделью (ONNX), без сети и ключей.

## Что делает
Картинка → та же картинка в большем разрешении. Во сколько раз — свойство САМОЙ модели (x2, x4),
поэтому кратность не спрашивается у данных, а измеряется по её выходу: строка канала не может
приказать модели то, чего та не умеет.

## Почему кусками
Апскейлер держит в памяти активации на всю площадь кадра, и она растёт квадратично: 1024×1024
на процессоре — это гигабайты и минуты. Столбец `tile` режет вход на куски с перекрытием
(`tile_overlap`), чтобы шов не был виден. Пусто — считаем картинку целиком.
"""

from pathlib import Path

from ..adapters import MediaOutcome, MediaRequest
from ..resolver import ProviderError
from .onnx_runtime import OnnxImage


class OnnxUpscale(OnnxImage):
    """Синхронный адаптер: картинка → увеличенная картинка на диске."""

    def generate(self, request: MediaRequest) -> MediaOutcome:
        if request.source is None:
            raise ProviderError(
                "CONTENT_REJECTED", "Увеличивать нечего: не передан исходный файл.",
                reason="Для этого вида ресурса input — путь к картинке внутри рабочей области.")
        params = request.params
        model_path = self.registry.require_model(params.get("model"))
        session = self.session(model_path)
        prep = self.preprocessing(model_path)
        image = self.open_image(request.source).convert("RGB")
        tile = self._positive(params.get("tile"))
        overlap = self._positive(params.get("tile_overlap")) or (tile // 8 if tile else 0)
        pieces = self._tiles(image.size, tile, overlap)
        scale, out = 0, None
        for box in pieces:
            crop = image.crop(box)
            side = (self.align(crop.width), self.align(crop.height))
            result = self.run(session, self.to_tensor(crop, prep, side if side != crop.size else None))
            piece, scale = self._to_image(result, prep, crop.size)
            if out is None:
                from PIL import Image
                out = Image.new("RGB", (image.width * scale, image.height * scale))
            out.paste(piece, (box[0] * scale, box[1] * scale))
        request.target.parent.mkdir(parents=True, exist_ok=True)
        out.save(request.target)
        return MediaOutcome(files=[request.target], meta={
            "engine": "onnx", "sync": True, "scale": scale, "tiles": len(pieces),
            "size": {"from": list(image.size), "to": list(out.size)},
            "compute": self.compute(session)})

    def _tiles(self, size: tuple[int, int], tile: int, overlap: int) -> list[tuple]:
        """Разбиение на куски с перекрытием. Ноль или картинка меньше куска — один кусок."""
        width, height = size
        if not tile or (width <= tile and height <= tile):
            return [(0, 0, width, height)]
        step = max(1, tile - overlap)
        boxes = []
        for top in range(0, height, step):
            for left in range(0, width, step):
                boxes.append((left, top, min(left + tile, width), min(top + tile, height)))
                if left + tile >= width:
                    break
            if top + tile >= height:
                break
        return boxes

    def _to_image(self, result, prep: dict, source_size: tuple[int, int]):
        """Выход графа → картинка и измеренная кратность увеличения."""
        import numpy as np
        from PIL import Image

        arr = np.asarray(result, dtype=np.float32)
        while arr.ndim > 3:
            arr = arr[0]
        arr = arr.transpose(1, 2, 0)                        # CHW → HWC
        if prep.get("do_normalize"):
            mean = np.array(prep.get("image_mean") or [0.5] * 3, dtype=np.float32)
            std = np.array(prep.get("image_std") or [0.5] * 3, dtype=np.float32)
            arr = arr * std + mean
        scale = max(1, round(arr.shape[1] / max(1, source_size[0])))
        arr = np.clip(arr / float(prep.get("rescale_factor") or 1 / 255), 0, 255).astype(np.uint8)
        image = Image.fromarray(arr)
        want = (source_size[0] * scale, source_size[1] * scale)
        # Выравнивание входа под кратность графа даёт лишние пиксели по краю — срезаем их,
        # иначе куски не сойдутся при склейке.
        return (image.crop((0, 0, *want)) if image.size != want else image), scale

    @staticmethod
    def _positive(value) -> int:
        return int(value) if isinstance(value, (int, float)) and value > 0 else 0
