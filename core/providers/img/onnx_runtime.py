"""
core/providers/img/onnx_runtime.py — общее для картиночных моделей в формате ONNX.

## Почему ONNX, а не transformers
У фона и апскейла ходовые модели (RMBG-2.0, BiRefNet) архитектуры в transformers не имеют:
их подъём требует исполнить `.py` из репозитория (`trust_remote_code`). ONNX даёт ту же модель
графом, который кодом не является, и проходит `install.allow_suffixes` как есть.

## Предобработка — из файла модели
Нормализация, масштаб и размер входа лежат в `preprocessor_config.json` рядом с графом, его
пишет автор модели. Числа берутся оттуда: модель со своей нормализацией работает без правки кода.
"""

import json
from pathlib import Path

from ..resolver import ProviderError


class OnnxImage:
    """Сессия ONNX + предобработка по описанию автора модели."""

    def __init__(self, registry):
        self.registry = registry
        self._sessions: dict[str, object] = {}

    @property
    def rules(self) -> dict:
        return ((self.registry.config.get("local") or {}).get("onnx")) or {}

    def session(self, model_path: Path):
        """Сессия живёт до конца процесса: её подъём — секунды и сотни мегабайт."""
        key = str(model_path)
        if key in self._sessions:
            return self._sessions[key]
        try:
            import onnxruntime as ort
        except ImportError as e:
            raise ProviderError(
                "LOCAL_INFERENCE_FAILED", f"Среда локального инференса не установлена: {e}",
                reason="Поставь зависимости группы local (onnxruntime) в окружение сервера.") from e
        providers = [str(p) for p in (self.rules.get("providers") or ["CPUExecutionProvider"])]
        try:
            self._sessions[key] = ort.InferenceSession(str(model_path), providers=providers)
        except Exception as e:                              # noqa: BLE001 — битый или чужой граф
            raise ProviderError(
                "LOCAL_MODEL_MISSING", f"Граф модели не читается: {e}",
                reason="Файл есть, но onnxruntime его не поднимает — перекачай модель: "
                       "media_model_install по тому же имени.") from e
        return self._sessions[key]

    @staticmethod
    def preprocessing(model_path: Path) -> dict:
        """Как автор модели велел готовить вход. Нет файла — работают дефолты вызывающего."""
        path = model_path.with_name("preprocessor_config.json")
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def align(self, value: int) -> int:
        """Округлить сторону вверх до кратности, которую требует свёрточная пирамида."""
        step = int(self.rules.get("align") or 1)
        return value if step <= 1 else ((int(value) + step - 1) // step) * step

    def open_image(self, path: Path):
        """Прочитать картинку. Не картинка — отказ с причиной, а не падение внутри графа."""
        try:
            from PIL import Image
        except ImportError as e:
            raise ProviderError(
                "LOCAL_INFERENCE_FAILED", f"Среда работы с изображениями не установлена: {e}",
                reason="Поставь зависимости группы local (pillow) в окружение сервера.") from e
        try:
            image = Image.open(path)
            image.load()
        except Exception as e:                              # noqa: BLE001 — не картинка/битый файл
            raise ProviderError(
                "CONTENT_REJECTED", f"Файл не читается как изображение: {e}",
                reason="Передай путь к картинке внутри рабочей области — повтор того же файла "
                       "даст тот же отказ.") from e
        return image

    def to_tensor(self, image, prep: dict, size: tuple[int, int] | None = None):
        """Картинка → тензор NCHW по описанию автора модели: размер, масштаб, нормализация."""
        import numpy as np
        from PIL import Image

        if size:
            image = image.resize(size, Image.BICUBIC)
        arr = np.asarray(image.convert("RGB"), dtype=np.float32)
        if prep.get("do_rescale", True):
            arr = arr * float(prep.get("rescale_factor") or 1 / 255)
        if prep.get("do_normalize"):
            mean = np.array(prep.get("image_mean") or [0.5] * 3, dtype=np.float32)
            std = np.array(prep.get("image_std") or [0.5] * 3, dtype=np.float32)
            arr = (arr - mean) / std
        return arr.transpose(2, 0, 1)[None]

    def run(self, session, tensor):
        """Один проход графа. Имена входа и выхода спрашиваются у графа, а не пишутся здесь."""
        try:
            return session.run(None, {session.get_inputs()[0].name: tensor})[0]
        except Exception as e:                              # noqa: BLE001 — память/размер/битый граф
            raise ProviderError(
                "LOCAL_INFERENCE_FAILED", f"Локальный инференс не выполнен: {e}",
                reason="Проверь память машины и размер картинки; при повторе того же входа "
                       "результат будет тем же. Уменьшить нагрузку можно столбцом tile строки "
                       "провайдера — тогда картинка считается кусками.") from e

    def compute(self, session) -> dict:
        """Где считалось. Без этого «медленно» неотличимо от «сломано»."""
        return {"device": "cpu", "runtime": "onnxruntime",
                "providers": list(session.get_providers()),
                "why": "граф ONNX считается исполнителем из config/providers.yaml → local.onnx"}
