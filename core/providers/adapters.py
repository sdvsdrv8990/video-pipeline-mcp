"""
core/providers/adapters.py — какой КОД исполняет провайдера, названного данными.

## Граница доверия
Имя провайдера приходит из книги канала, которую правит ИИ. Путь к модулю по этому имени берётся
из серверного конфига (`config/providers.yaml → adapters`), а не из строки таблицы: иначе данные
выбирали бы, какой Python импортировать, то есть исполняли бы код. Дополнительно импорт ограничен
объявленным корнем — одной опечатки в конфиге мало, чтобы поднять чужой модуль.

## Контракт адаптера
`Adapter(registry)` + `generate(MediaRequest) -> MediaOutcome`. Реестр отдан адаптеру целиком,
чтобы веса он спрашивал (`registry.model_path`), а не искал: раскладка каталога моделей —
знание декларации, и второй копии этого знания в адаптерах быть не должно.
- синхронный провайдер возвращает `files` — файлы уже на диске;
- асинхронный возвращает `task_id`, и тогда вызывающий прозванивает `poll(task_id)` циклом
  (`core/providers/task_cycle.py`) и забирает результат `fetch(answer, target)`.
Адаптер НЕ решает, куда класть файл и сколько ждать: путь и пределы приходят снаружи.
"""

import importlib
from dataclasses import dataclass, field
from pathlib import Path

from core.paths import PathEscapeError, safe_resolve

from .declaration import Declaration
from .resolver import ProviderError


@dataclass
class MediaRequest:
    """Один вызов провайдера: что исполнить, чем и куда положить."""

    input: str                      # текст озвучки или промпт картинки
    params: dict                    # вся строка провайдера минус служебные столбцы
    target: Path                    # абсолютный путь результата (containment уже пройден)
    models_dir: Path                # корень локальных весов (для локальных адаптеров)
    # S23: ключ доступа приходит сюда и уходит ТОЛЬКО в исходящий запрос. В ToolResult, факты,
    # журнал и сообщения об ошибках он не попадает — значение наружу не возвращается никогда.
    api_key: str = ""


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
        self._decl = Declaration(
            config_file, ProviderError, "провайдеров",
            "Заведи config/providers.yaml — соответствие провайдер→адаптер объявлено там.")

    @property
    def config(self) -> dict:
        return self._decl.data

    @property
    def models_dir(self) -> Path:
        """Каталог весов локальных моделей (вне git, наполняется scripts/models.py)."""
        local = self.config.get("local") or {}
        return self.project_root / str(local.get("models_dir", "vendor/models"))

    @property
    def catalog(self) -> list[dict]:
        """Известные локальные модели: объявленные в конфиге + поставленные скриптом (опись)."""
        from .catalog import ModelCatalog
        return ModelCatalog(self.config_file, self.models_dir).entries()

    def model_entry(self, name: str) -> dict:
        """Что опись знает об этой модели: вариант весов, размер, число параметров.

        Свойства СКАЧАННОГО живут в описи, а не в строке канала: требовать их от ИИ значит
        ронять вызов из-за незаполненного столбца (так и было с `variant` — F80).
        """
        wanted = str(name or "").strip()
        return next((e for e in self.catalog if str(e.get("id")) == wanted), {})

    def model_path(self, name: str) -> Path:
        """Где лежат веса модели, названной строкой канала.

        Имя из данных — это `id` объявленной модели; тогда путь берётся из декларации. Если имени
        в каталоге нет, оно трактуется как путь относительно корня весов (модель положили руками).
        В обоих случаях путь проходит containment: строку канала правит ИИ, и `../../.env` в поле
        `model` иначе читало бы чужое.
        """
        wanted = str(name or "").strip()
        if not wanted:
            raise ProviderError(
                "LOCAL_MODEL_MISSING", "В строке провайдера не указана модель.",
                reason="Впиши в столбец model имя объявленной модели "
                       f"({', '.join(str(e.get('id')) for e in self.catalog) or 'каталог пуст'}) "
                       "или путь внутри каталога весов.",
                suggested_tool="table_update")
        declared = {str(e.get("id")): str(e.get("path") or e.get("dest") or "") for e in self.catalog}
        rel = declared.get(wanted) or wanted
        try:
            return safe_resolve(rel, self.models_dir)
        except PathEscapeError as e:
            raise ProviderError(
                "LOCAL_MODEL_MISSING", f"Имя модели ведёт за пределы каталога весов: {wanted}",
                reason="Модель берётся только из каталога локальных весов — путь наружу не читается.",
            ) from e

    def require_model(self, name: str, must_be_dir: bool = False) -> Path:
        """То же, но с проверкой, что веса действительно лежат. Нет — отказ, а не тишина."""
        path = self.model_path(name)
        present = path.is_dir() if must_be_dir else path.is_file()
        if not present:
            raise ProviderError(
                "LOCAL_MODEL_MISSING", f"Нет весов локальной модели: {name}",
                reason=("Веса не лежат в git. Посмотри, что можно поставить — media_models "
                        "(scope='local'), поставь — media_model_install; из терминала то же самое "
                        "делает python scripts/models.py install <id> --kind <вид>. "
                        f"Ожидались в {path}. Либо переключи строку канала на другого провайдера."),
                suggested_tool="media_provider_status")
        return path

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

    def spec(self, provider: str, resource_type: str = "") -> str:
        """Объявленный путь адаптера. Пара «провайдер:ресурс» точнее одного имени и ищется первой."""
        table = (self.config.get("adapters") or {}).get("by_provider") or {}
        entry = self._entry(provider, resource_type)
        if entry and entry.get("adapter"):
            return str(entry["adapter"])
        raise ProviderError(
            "PROVIDER_ADAPTER_MISSING", f"Для провайдера '{provider}' не объявлен адаптер.",
            reason=(f"Сервер знает, чем исполнять: {', '.join(sorted(table)) or '(ничего)'}. "
                    "Либо поставь в строке канала одного из них (table_update), либо объяви "
                    "новый адаптер в config/providers.yaml → adapters.by_provider."),
            suggested_tool="media_provider_status")

    def load(self, provider: str, resource_type: str = ""):
        """Поднять адаптер провайдера. Импорт разрешён только из объявленного корня."""
        spec = self.spec(provider, resource_type)
        root = str((self.config.get("adapters") or {}).get("root") or "").strip()
        module_path, _, class_name = spec.partition(":")
        full = f"{root}.{module_path}" if root else module_path
        if root and not full.startswith(f"{root}."):
            raise ProviderError(
                "PROVIDER_ADAPTER_MISSING", f"Адаптер '{spec}' вне разрешённого корня '{root}'.",
                reason="Адаптеры живут внутри объявленного корня; путь наружу не импортируется.")
        try:
            module = importlib.import_module(full)
            adapter_cls = getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            raise ProviderError(
                "PROVIDER_ADAPTER_MISSING", f"Адаптер '{spec}' не поднимается: {e}",
                reason=("Модуль объявлен, но его нет или в нём нет такого класса. Проверь "
                        "config/providers.yaml → adapters.by_provider и установку зависимостей."),
            ) from e
        return adapter_cls(self)
