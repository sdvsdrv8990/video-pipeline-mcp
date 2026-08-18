"""
core/providers/img/diffusers_local.py — картинка локальной моделью (diffusers), без сети и ключей.

## Назначение
Локальная картинка замыкает цепочку «строка канала → адаптер → файл → приёмка → расход» на этой
машине: пока рисует только облако, вся цепочка непроверяема.

## Границы
- ВСЕ параметры вызова — столбцы строки; чего в строке нет, не передаётся вовсе, и работает
  дефолт модели. Числа в коде вредны: `guidance=0` верно для дистиллированных, портит обычные;
- где лежат веса, знает реестр по описи поставленного; нет весов — `LOCAL_MODEL_MISSING`, а не
  молчаливая загрузка гигабайтов посреди вызова инструмента;
- устройство выбирает проба железа, перекрывает столбец `device`; отказ переноса на карту —
  ошибка, а не тихий откат на процессор: ждавший карту обязан узнать, что её не дали.
"""

from pathlib import Path

from ..adapters import MediaOutcome, MediaRequest
from ..resolver import ProviderError


class DiffusersLocalIMG:
    """Синхронный адаптер: промпт → png на диске."""

    def __init__(self, registry):
        self.registry = registry
        self.models_dir = Path(registry.models_dir)

    @property
    def _gpu_rules(self) -> dict:
        return (self.registry.config.get("local") or {}).get("gpu") or {}

    def _where(self, params: dict) -> dict:
        """Где считать: решает проба железа (или столбец строки), а не этот файл."""
        from ..hardware import compute_device
        return compute_device(self._gpu_rules, str(params.get("device") or "").strip())

    def _load_kwargs(self, params: dict, entry: dict) -> dict:
        """Параметры ПОДЪЁМА модели по объявленной карте «столбец строки → параметр библиотеки».

        Новый рычаг библиотеки добавляется строкой в декларации, а не веткой здесь. `variant`
        (какие файлы весов лежат на диске — fp16 или полные) берётся из ОПИСИ: его записал
        установщик, и требовать его от ИИ значило бы ронять вызов из-за незаполненного столбца.
        """
        out = {}
        for column, param in (self._gpu_rules.get("load_from_row") or {}).items():
            value = params.get(column)
            if value not in (None, ""):
                out[str(param)] = value
        if entry.get("variant") and "variant" not in out:
            out["variant"] = entry["variant"]
        return out

    def _pipeline(self, model_path: Path, load: dict, where: dict, place: dict):
        """Пайплайн живёт в пуле процесса: его подъём — десятки секунд и гигабайты.

        Прежде он кэшировался внутри адаптера, а тот создаётся заново на КАЖДЫЙ вызов — то есть
        кэш не переживал вызов и создавал лишь видимость переиспользования (замер S24: 1.4 с
        против 0.2 с на кадре sd-turbo).
        """
        key = f"diffusers|{model_path}#{sorted(load.items())}#{where['device']}#{where['dtype']}#{place['mode']}"

        def build():
            try:
                import torch
                from diffusers import AutoPipelineForText2Image
            except ImportError as e:
                raise ProviderError(
                    "LOCAL_INFERENCE_FAILED", f"Среда локальной генерации не установлена: {e}",
                    reason="Поставь зависимости группы local (diffusers, transformers) в окружение сервера.") from e
            dtype = getattr(torch, where["dtype"], None) if where["dtype"] else None
            try:
                # local_files_only: молча тянуть модель из интернета посреди вызова нельзя.
                pipe = AutoPipelineForText2Image.from_pretrained(
                    str(model_path), local_files_only=True, safety_checker=None, **load,
                    **({"torch_dtype": dtype} if dtype else {}))
            except Exception as e:                          # noqa: BLE001 — битые/неполные веса
                raise ProviderError(
                    "LOCAL_MODEL_MISSING", f"Веса модели не поднимаются: {e}",
                    reason="Каталог есть, но модель из него не читается — перекачай: "
                           "python scripts/models.py pull.") from e
            if where["device"] != "cpu" and "device_map" not in load:
                # Перенос и выгрузка исключают друг друга: у выгруженного пайплайна `.to()`
                # ругается сама библиотека. Поэтому здесь ровно одна ветка из трёх.
                # У методов выгрузки первый позиционный аргумент — `gpu_id`, а не устройство,
                # поэтому оно передаётся ИМЕНЕМ: позиционно получается 'cuda:cuda'.
                mover = {
                    "device": lambda dev: pipe.to(dev),
                    "model_offload": lambda dev: pipe.enable_model_cpu_offload(device=dev),
                    "sequential_offload": lambda dev: pipe.enable_sequential_cpu_offload(device=dev),
                }.get(place["mode"])
                if mover is None:
                    raise ProviderError(
                        "LOCAL_INFERENCE_FAILED",
                        f"Модель не влезает на '{where['device']}': {place['why']}.",
                        reason="Объяви в config/providers.yaml → local.gpu.oversize режим выгрузки "
                               "(model_offload — медленнее в разы, sequential_offload — в десятки, "
                               "но влезает почти всё) или возьми модель полегче: media_models "
                               "показывает вердикт по каждой.")
                try:
                    mover(where["device"])
                except Exception as e:                      # noqa: BLE001 — нет устройства/памяти
                    # Молча посчитать на процессоре нельзя: разница во времени — десятки раз, и
                    # тот, кто ждал карту, обязан узнать, что её не дали, вместе с причиной.
                    raise ProviderError(
                        "LOCAL_INFERENCE_FAILED",
                        f"Модель не размещается на '{where['device']}' ({place['mode']}): {e}",
                        reason=f"Проба железа выбрала это устройство ({where['why']}). Посмотри "
                               "media_models → hardware; чтобы считать на процессоре намеренно, "
                               "поставь device=cpu в строке провайдера.") from e
            return pipe

        # Сколько модель займёт — считает оценщик по описи; на карте пул уточнит это замером.
        from ..hardware import FitEstimator
        entry = self.registry.model_entry(model_path.name) or {}
        need = FitEstimator((self.registry.config.get("local") or {}).get("fit") or {}).bytes_for(
            entry.get("params_by_dtype") or {}, entry.get("params_total") or 0,
            as_dtype=where["dtype"])
        return self.registry.pool.get(key, build, need_bytes=need, device=where["device"])

    def generate(self, request: MediaRequest) -> MediaOutcome:
        """Нарисовать промпт в файл. Синхронно: результат сразу на диске."""
        if not (request.input or "").strip():
            raise ProviderError(
                "CONTENT_REJECTED", "Пустой промпт рисовать нечем.",
                reason="Передай описание кадра — повтор пустого запроса даст тот же отказ.")
        params = request.params
        from ..hardware import placement
        entry = self.registry.model_entry(params.get("model"))
        where = self._where(params)
        place = placement(self._gpu_rules, (self.registry.config.get("local") or {}).get("fit") or {},
                          entry, where)
        pipe = self._pipeline(self.registry.require_model(params.get("model"), must_be_dir=True),
                              self._load_kwargs(params, entry), where, place)
        request.target.parent.mkdir(parents=True, exist_ok=True)
        try:
            images = pipe(prompt=request.input, **self._call_kwargs(params)).images
        except Exception as e:                              # noqa: BLE001 — среда/память/веса
            raise ProviderError(
                "LOCAL_INFERENCE_FAILED", f"Локальная генерация не выполнена: {e}",
                reason="Проверь ресурсы машины (память) и веса модели; при повторе того же "
                       "входа результат будет тем же.") from e
        files: list[Path] = []
        for i, image in enumerate(images):
            # Второй и следующий варианты — соседними именами, чтобы не затирать первый.
            target = request.target if i == 0 else request.target.with_name(
                f"{request.target.stem}_{i + 1}{request.target.suffix}")
            image.save(target)
            files.append(target)
        return MediaOutcome(files=files, meta={
            "engine": "diffusers", "sync": True, "call": self._call_kwargs(params),
            "compute": {**where, "placement": place}})

    def _call_kwargs(self, params: dict) -> dict:
        """Столбцы строки → аргументы вызова. Чего в строке нет — не передаём: пусть решает модель."""
        kwargs: dict = {}
        width, height = self._size(params)
        if width and height:
            kwargs.update(width=width, height=height)
        steps = self._positive(params.get("steps"))
        if steps:
            kwargs["num_inference_steps"] = int(steps)
        count = self._positive(params.get("img_n"))
        if count:
            kwargs["num_images_per_prompt"] = int(count)
        guidance = params.get("guidance")
        if isinstance(guidance, (int, float)):
            # Ноль — осмысленное значение (пошаговые модели рисуют без подсказки), поэтому
            # проверяем ТИП, а не истинность: `if guidance:` выбросил бы ровно этот случай.
            kwargs["guidance_scale"] = float(guidance)
        return kwargs

    @staticmethod
    def _size(params: dict) -> tuple[int, int]:
        """`img_size` вида «512x512» из данных. Не назван или кривой — размер решает модель."""
        raw = str(params.get("img_size") or "").lower().replace(" ", "")
        w, _, h = raw.partition("x")
        return (int(w), int(h)) if w.isdigit() and h.isdigit() else (0, 0)

    @staticmethod
    def _positive(value) -> float:
        return float(value) if isinstance(value, (int, float)) and value > 0 else 0.0
