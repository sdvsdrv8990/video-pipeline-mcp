"""
core/providers/img/diffusers_local.py — картинка локальной моделью (diffusers), без сети и ключей.

## Зачем локальный провайдер
Тот же довод, что и у локальной озвучки: пока картинку рисует только облако, цепочка «строка
канала → адаптер → файл → приёмка → расход» непроверяема. Локальная модель делает её настоящей.

## Что берётся из данных
Из строки провайдера: каталог модели (`model`), размер (`img_size`), число вариантов (`img_n`),
число шагов (`steps`). Имён моделей в коде нет — только перевод столбцов в API diffusers.

## Веса — зависимость
Каталог модели ищется внутри каталога весов (`local.models_dir`) и проходит containment: поле
`model` правит ИИ. Нет весов — `LOCAL_MODEL_MISSING`, а не молчаливая попытка скачать из сети:
загрузка гигабайтов посреди вызова инструмента — не то, чего ждёт клиент.
"""

from pathlib import Path

from core.paths import PathEscapeError, safe_resolve

from ..adapters import MediaOutcome, MediaRequest
from ..resolver import ProviderError

SUBDIR = "img"
DEFAULT_SIZE = 512


class DiffusersLocalIMG:
    """Синхронный адаптер: промпт → png на диске."""

    def __init__(self, models_dir: Path):
        self.models_dir = Path(models_dir)
        self._pipelines: dict[str, object] = {}

    def _model_path(self, params: dict) -> Path:
        name = str(params.get("model") or "").strip()
        if not name:
            raise ProviderError(
                "LOCAL_MODEL_MISSING", "В строке провайдера не указана модель картинок.",
                reason="Впиши каталог модели в столбец model листа провайдеров книги канала.",
                suggested_tool="table_update")
        root = self.models_dir / SUBDIR
        try:
            path = safe_resolve(name, root)
        except PathEscapeError as e:
            raise ProviderError(
                "LOCAL_MODEL_MISSING", f"Имя модели ведёт за пределы каталога весов: {name}",
                reason="Модель берётся только из каталога локальных весов — путь наружу не читается.",
            ) from e
        if not path.is_dir():
            raise ProviderError(
                "LOCAL_MODEL_MISSING", f"Нет весов локальной модели: {name}",
                reason=("Веса не лежат в git — вытяни их: python scripts/fetch_local_models.py. "
                        f"Ожидались в {root}. Либо переключи строку канала на другого провайдера."),
                suggested_tool="media_provider_status")
        return path

    def _pipeline(self, model_path: Path, variant: str = ""):
        """Пайплайн живёт до конца процесса: его подъём — десятки секунд и гигабайты."""
        key = f"{model_path}#{variant}"
        if key not in self._pipelines:
            try:
                from diffusers import AutoPipelineForText2Image
            except ImportError as e:
                raise ProviderError(
                    "LOCAL_INFERENCE_FAILED", f"Среда локальной генерации не установлена: {e}",
                    reason="Поставь зависимости группы local (diffusers, transformers) в окружение сервера.") from e
            try:
                # local_files_only: молча тянуть модель из интернета посреди вызова нельзя.
                # variant — какие файлы весов лежат в каталоге (fp16/fp32): это свойство
                # СКАЧАННОГО, поэтому приходит столбцом строки, а не жёстко стоит в коде.
                self._pipelines[key] = AutoPipelineForText2Image.from_pretrained(
                    str(model_path), local_files_only=True, safety_checker=None,
                    **({"variant": variant} if variant else {}))
            except Exception as e:                          # noqa: BLE001 — битые/неполные веса
                raise ProviderError(
                    "LOCAL_MODEL_MISSING", f"Веса модели не поднимаются: {e}",
                    reason="Каталог есть, но модель из него не читается — перекачай: "
                           "python scripts/fetch_local_models.py.") from e
        return self._pipelines[key]

    def generate(self, request: MediaRequest) -> MediaOutcome:
        """Нарисовать промпт в файл. Синхронно: результат сразу на диске."""
        if not (request.input or "").strip():
            raise ProviderError(
                "CONTENT_REJECTED", "Пустой промпт рисовать нечем.",
                reason="Передай описание кадра — повтор пустого запроса даст тот же отказ.")
        params = request.params
        pipe = self._pipeline(self._model_path(params), str(params.get("variant") or "").strip())
        width, height = self._size(params)
        steps = self._int(params.get("steps"), 1)
        count = max(1, self._int(params.get("img_n"), 1))
        request.target.parent.mkdir(parents=True, exist_ok=True)
        try:
            # guidance_scale=0: дистиллированные пошаговые модели рисуют без classifier-free guidance.
            images = pipe(prompt=request.input, num_inference_steps=steps, guidance_scale=0.0,
                          width=width, height=height, num_images_per_prompt=count).images
        except Exception as e:                              # noqa: BLE001 — среда/память/веса
            raise ProviderError(
                "LOCAL_INFERENCE_FAILED", f"Локальная генерация не выполнена: {e}",
                reason="Проверь ресурсы машины (память) и веса модели; при повторе того же "
                       "входа результат будет тем же.") from e
        files = []
        for i, image in enumerate(images):
            # Второй и следующий варианты — соседними именами, чтобы не затирать первый.
            target = request.target if i == 0 else request.target.with_name(
                f"{request.target.stem}_{i + 1}{request.target.suffix}")
            image.save(target)
            files.append(target)
        return MediaOutcome(files=files, meta={"engine": "diffusers", "sync": True, "steps": steps})

    def _size(self, params: dict) -> tuple[int, int]:
        """`img_size` вида «512x512» из данных; кривое значение не роняет вызов молча."""
        raw = str(params.get("img_size") or "").lower().replace(" ", "")
        if "x" in raw:
            w, _, h = raw.partition("x")
            if w.isdigit() and h.isdigit():
                return int(w), int(h)
        return DEFAULT_SIZE, DEFAULT_SIZE

    @staticmethod
    def _int(value, default: int) -> int:
        return int(value) if isinstance(value, (int, float)) and value > 0 else default
