"""core/providers/adapters.py — какой КОД исполняет провайдера, названного данными.

Имя провайдера приходит из книги канала, которую правит ИИ, а путь к модулю — из объявления:
иначе данные выбирали бы, какой Python импортировать. Импорт ограничен объявленным корнем —
одной опечатки в объявлении мало, чтобы поднять чужой модуль. Синхронный адаптер отдаёт файлы,
асинхронный — идентификатор задачи, и дальше его крутит цикл задач. Путь результата и пределы
ожидания приходят снаружи: адаптер их не выбирает. Реестр отдан целиком, чтобы веса адаптер
спрашивал, а не искал.
"""

import importlib
from typing import TYPE_CHECKING, ClassVar, TypeVar, overload

if TYPE_CHECKING:                       # рантайм-импорт остаётся ленивым (цикл модулей)
    from .pool import ModelPool
from dataclasses import dataclass, field
from pathlib import Path

from core.paths import Files

from core.capacity import Capacity
from core.declaration import Declaration
from .resolver import ProviderError

T = TypeVar("T")


@dataclass
class MediaRequest:
    """Один вызов провайдера: что исполнить, чем и куда положить."""

    input: str                      # текст озвучки, промпт картинки или путь к исходному файлу
    params: dict                    # вся строка провайдера минус служебные столбцы
    target: Path                    # абсолютный путь результата (containment уже пройден)
    models_dir: Path                # корень локальных весов (для локальных адаптеров)
    # Исходный файл для видов ресурса, которые ПЕРЕРАБАТЫВАЮТ готовое (фон, апскейл), а не
    # создают с нуля. Путь уже прошёл containment: адаптер получает готовое место, как и target.
    source: Path | None = None
    # Ключ доступа приходит сюда и уходит ТОЛЬКО в исходящий запрос. В ToolResult, факты,
    # журнал и сообщения об ошибках он не попадает — значение наружу не возвращается никогда.
    api_key: str = ""
    provider: str = ""              # под каким именем адаптер позвали: его описание берётся оттуда
    resource_type: str = ""         # вид ресурса: нужен тем, кто передаёт задачу дальше (раннер)
    # Корень рабочей области. Нужен только адаптерам, которые обмениваются ПУТЯМИ, а не байтами:
    # относительный путь не требует, чтобы у обеих сторон область была смонтирована одинаково.
    workspace: Path | None = None


@dataclass
class MediaOutcome:
    """Чем кончился вызов: файлы на диске (sync) или идентификатор задачи (async)."""

    files: list[Path] = field(default_factory=list)
    task_id: str = ""
    meta: dict = field(default_factory=dict)


class AdapterRegistry:
    """Имя провайдера → экземпляр адаптера, по объявленному соответствию."""

    def __init__(self, config_file: str | Path, project_root: str | Path):
        self.config_file = Path(config_file)
        self.project_root = Path(project_root)
        self._decl = Declaration(config_file, ProviderError, "провайдеров")

    @property
    def config(self) -> dict:
        return self._decl.data

    @property
    def models_dir(self) -> Path:
        """Каталог весов локальных моделей (вне git, наполняется scripts/models.py)."""
        local = self.config.get("local") or {}
        return self.project_root / str(local.get("models_dir", "vendor/models"))

    @property
    def pool(self) -> "ModelPool":
        """Живые модели процесса. Адаптер создаётся на каждый вызов, а поднятая модель — нет."""
        from .hardware import probe
        from .pool import ModelPool

        local = self.config.get("local") or {}
        return ModelPool(local.get("pool") or {}, probe(self.models_dir, local.get("gpu")))

    @property
    def catalog(self) -> list[dict]:
        """Известные локальные модели: объявленные в конфиге + поставленные скриптом (опись)."""
        from .catalog import ModelCatalog
        return ModelCatalog(self.config_file, self.models_dir).entries()

    def model_entry(self, name: str) -> dict:
        """Что опись знает об этой модели: вариант весов, размер, число параметров.

        Свойства СКАЧАННОГО живут в описи, а не в строке канала: требовать их от ИИ значит
        ронять вызов из-за незаполненного столбца (так и было с `variant`).
        """
        wanted = str(name or "").strip()
        return next((e for e in self.catalog if str(e.get("id")) == wanted), {})

    def model_path(self, name: str) -> Path:
        """Где лежат веса модели, названной строкой канала.

        Имя из данных — это `id` объявленной модели; тогда путь берётся из декларации. Если имени
        в каталоге нет, оно трактуется как путь относительно корня весов (модель положили руками).
        В обоих случаях путь проходит дверь каталога весов: строку канала правит ИИ, и `../../.env` в поле
        `model` иначе читало бы чужое. Путь наружу — отказ двери PATH_ESCAPE (`facts.path` — путь от корня), а не
        «весов нет»: у этой причины свой код.
        """
        wanted = str(name or "").strip()
        if not wanted:   # лист и строку книги допишет слой, который их знает (`with_facts` в media_generate)
            raise ProviderError("BOOK_ROW_INVALID", errors=[{"field": "model", "rule": "required"}])
        declared = {str(e.get("id")): str(e.get("path") or e.get("dest") or "") for e in self.catalog}
        rel = declared.get(wanted) or wanted
        return Files(self.models_dir).place(rel)

    def _entry(self, provider: str, resource_type: str = "") -> dict | None:
        """Объявление провайдера: строка-путь или запись с полями (`adapter`, `requires_key`)."""
        table = (self.config.get("adapters") or {}).get("by_provider") or {}
        for key in (f"{provider}:{resource_type}", provider):
            if key in table:
                value = table[key]
                return value if isinstance(value, dict) else {"adapter": str(value)}
        return None

    def requires_key(self, provider: str, resource_type: str = "") -> bool:
        """Нужен ли этому провайдеру ключ доступа. Объявляется сервером, не данными канала:
        строку канала правит ИИ, и «ключ не нужен» не должно быть его решением."""
        return bool((self._entry(provider, resource_type) or {}).get("requires_key"))

    def key_source(self, provider: str, resource_type: str = "") -> str:
        """Откуда берётся ключ. Пусто — из закрытого файла канала (его вносит владелец).

        У раннера ключ другой природы: это токен, который сервер сам выдал при запуске, а не
        секрет владельца. Спрашивать его у канала значило бы требовать от человека вписать то,
        чего он не выпускал.
        """
        return str((self._entry(provider, resource_type) or {}).get("key_source") or "")

    def place(self, provider: str, resource_type: str = "") -> str:
        """Класс места работы генерации (`adapters.place`, пара «провайдер:вид» первой); null — пустое имя: работа без
        места, одновременность держит другая сторона. Строки нет или не строка — отказ двери с путём ключа; класс не
        объявлен в resources.yaml — отказ связи двух деклараций (иначе работа молча шла бы без предела)."""
        table = self.need("adapters", "place", kind=dict)
        key = next((k for k in (f"{provider}:{resource_type}", provider) if k in table), provider)
        if table.get(key, "") is None:
            return ""
        klass = self.need("adapters", "place", key, kind=str)
        capacity = Capacity()
        if klass not in capacity.resources.classes and klass not in capacity.resources.work:
            raise self._decl.link_refused("class_undeclared", "класс места объявлен в resources.yaml (classes или work)",
                                          [("adapters", "place", key)], capacity.declaration, [("classes",), ("work",)])
        return klass

    def spec(self, provider: str, resource_type: str = "") -> str:
        """Объявленный путь адаптера. Пара «провайдер:ресурс» точнее одного имени и ищется первой."""
        table = (self.config.get("adapters") or {}).get("by_provider") or {}
        entry = self._entry(provider, resource_type)
        if entry and entry.get("adapter"):
            return str(entry["adapter"])
        # В рецепт идут только имена С исполнителем. Перечень всех объявленных советовал бы
        # ровно тех, у кого адаптера нет, — то есть вёл бы ИИ на тот же отказ по кругу.
        runnable = sorted(n for n, v in table.items()
                          if (v if isinstance(v, str) else (v or {}).get("adapter")))
        raise ProviderError("PROVIDER_ADAPTER_MISSING", facts={"provider": provider, "known": ", ".join(runnable)})

    def load(self, provider: str, resource_type: str = ""):
        """Поднять адаптер провайдера. Импорт — только под объявленным корнем: путь адаптера приставляется к корню."""
        spec = self.spec(provider, resource_type)
        root = str((self.config.get("adapters") or {}).get("root") or "").strip()
        module_path, _, class_name = spec.partition(":")
        full = f"{root}.{module_path}" if root else module_path
        try:
            module = importlib.import_module(full)
            adapter_cls = getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            raise ProviderError("PROVIDER_ADAPTER_BROKEN", facts={"provider": provider}) from e
        return adapter_cls(self)

    @overload
    def need(self, *path: str) -> object: ...

    @overload
    def need(self, *path: str, kind: type[T]) -> T: ...

    def need(self, *path: str, kind: type | None = None) -> object:
        """Объявленное значение по пути декларации; ключа нет или тип не тот (`kind`) — отказ кодом реестра."""
        return self._decl.need(*path) if kind is None else self._decl.need(*path, kind=kind)

    def refused(self, rule: str, expected: str, *paths: tuple[str, ...]) -> Exception:
        """Значение декларации нарушает правило, которого схема не видит (допустимое у кода, связь двух ключей), — тот же
        отказ двери: путь ключа и правило, без значения."""
        return self._decl.link_refused(rule, expected, list(paths), self._decl, [])

    # Политика `local.hand_placed` → пускать ли веса, положенные руками, по наличию файла.
    HAND_PLACED: ClassVar[dict[str, bool]] = {"by_presence": True, "refuse": False}

    def ready(self, name: str, must_be_dir: bool = False) -> dict:
        """Готовность модели к подъёму: путь весов и названная ступень проверки; не готова — отказ кодом причины.

        Модель описи судится составом по длине — дёшево, на каждом подъёме. Имя вне описи — веса, положенные руками:
        состава у них нет, и пускать ли их по наличию, решает объявленная политика `local.hand_placed`, а не код.
        """
        from .catalog import ModelCatalog

        path, entry = self.model_path(name), self.model_entry(name)
        if entry:
            catalog = ModelCatalog(self.config_file, self.models_dir)
            disk = catalog.judge(entry)
            if disk["verdict"] != "ready":
                raise catalog.refusal(entry, disk)
            return {**disk, "path": path}
        if not (path.is_dir() if must_be_dir else path.is_file()):
            raise ProviderError("LOCAL_MODEL_MISSING", facts={
                "model": str(name), "path": Files(self.models_dir).name(path)})
        policy = self.need("local", "hand_placed", kind=str)
        if policy not in self.HAND_PLACED:
            raise self.refused("literal_error", f"одно из: {', '.join(self.HAND_PLACED)}", ("local", "hand_placed"))
        if not self.HAND_PLACED[policy]:
            raise ProviderError("MODEL_UNVERIFIED", facts={"model": str(name)})
        return {"verdict": "ready", "checked": "по наличию (состав не объявлен)", "plan": [], "path": path,
                "why": "веса положены руками: лежат по пути, целость не судима — пускает local.hand_placed"}

    def readiness(self) -> list[dict]:
        """Готовность каждой модели описи по составу на диске (по длине): готова, чего не хватает или судить нечем."""
        from .catalog import ModelCatalog

        catalog = ModelCatalog(self.config_file, self.models_dir)
        return [{"id": e.get("id"), "kind": e.get("kind"), **catalog.judge(e)} for e in catalog.entries()]
