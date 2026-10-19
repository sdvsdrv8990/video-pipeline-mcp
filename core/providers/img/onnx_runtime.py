"""core/providers/img/onnx_runtime.py — общее для картиночных моделей в формате ONNX.

Почему ONNX, а не transformers: у фона и апскейла ходовые модели своей архитектуры в transformers
не имеют, и подъём требовал бы исполнить `.py` из чужого репозитория. ONNX даёт ту же модель
графом, который кодом не является, и проходит наш отсев форматов как есть. Нормализация, масштаб и
размер входа берутся из файла предобработки рядом с графом — его пишет автор модели, поэтому
модель со своей нормализацией работает без правки кода.
"""

import json
from pathlib import Path

from ..resolver import ProviderError


class OnnxImage:
    """Сессия ONNX + предобработка по описанию автора модели."""

    def __init__(self, registry):
        self.registry = registry

    @property
    def rules(self) -> dict:
        return ((self.registry.config.get("local") or {}).get("onnx")) or {}

    def ready(self, request) -> dict:
        """Готовность до вызова (MF-13): исходная картинка названа, веса модели готовы — путь и ступень проверки; иначе
        отказ кодом причины. Зовут дверь до задания и `generate` перед расчётом: диск мог смениться между ними."""
        self.source(request)
        return self.registry.ready(request.params.get("model"))

    @staticmethod
    def source(request) -> Path:
        """Исходная картинка запроса; не названа — отказ поля `input`."""
        if request.source is None:
            raise ProviderError("VALIDATION_ERROR", errors=[{"field": "input", "rule": "required"}])
        return request.source

    def session(self, model_path: Path):
        """Сессия живёт в пуле процесса: адаптер создаётся на каждый вызов, а модель — нет."""
        providers = [str(p) for p in (self.rules.get("providers") or ["CPUExecutionProvider"])]

        def load():
            try:
                import onnxruntime as ort
            except ImportError as e:
                raise ProviderError("LOCAL_INFERENCE_FAILED") from e
            try:
                return ort.InferenceSession(str(model_path), providers=providers)
            except Exception as e:  # битый или чужой граф; причина — по цепочке `from e` в журнал, не клиенту
                raise ProviderError("MODEL_UNLOADABLE", facts={"model": Path(model_path).name}) from e

        size = model_path.stat().st_size if model_path.is_file() else None
        return self.registry.pool.get(f"onnx|{model_path}|{','.join(providers)}", load,
                                      need_bytes=size, device="cpu")

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
            raise ProviderError("LOCAL_INFERENCE_FAILED") from e
        try:
            image = Image.open(path)
            image.load()
        except Exception as e:  # не картинка/битый файл
            raise ProviderError("VALIDATION_ERROR", errors=[{"field": "input", "rule": "not_image"}]) from e
        return image

    def to_tensor(self, image, prep: dict, size: tuple[int, int] | None = None):
        """Картинка → тензор NCHW по описанию автора модели: размер, масштаб, нормализация."""
        import numpy as np
        from PIL import Image

        if size:
            image = image.resize(size, Image.Resampling.BICUBIC)
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
        except Exception as e:  # память/размер/битый граф
            raise ProviderError("LOCAL_INFERENCE_FAILED") from e

    def compute(self, session) -> dict:
        """Где считалось. Без этого «медленно» неотличимо от «сломано»."""
        return {"device": "cpu", "runtime": "onnxruntime",
                "providers": list(session.get_providers()),
                "why": "граф ONNX считается исполнителем из config/providers.yaml → local.onnx"}
