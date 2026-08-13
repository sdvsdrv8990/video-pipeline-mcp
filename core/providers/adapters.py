"""
core/providers/adapters.py — какой КОД исполняет провайдера, названного данными.

## Граница доверия
Имя провайдера приходит из книги канала, которую правит ИИ. Путь к модулю по этому имени берётся
из серверного конфига (`config/providers.yaml → adapters`), а не из строки таблицы: иначе данные
выбирали бы, какой Python импортировать, то есть исполняли бы код. Дополнительно импорт ограничен
объявленным корнем — одной опечатки в конфиге мало, чтобы поднять чужой модуль.

## Контракт адаптера
`Adapter(models_dir)` + `generate(MediaRequest) -> MediaOutcome`.
- синхронный провайдер возвращает `files` — файлы уже на диске;
- асинхронный возвращает `task_id`, и тогда вызывающий прозванивает `poll(task_id)` циклом
  (`core/providers/task_cycle.py`) и забирает результат `fetch(answer, target)`.
Адаптер НЕ решает, куда класть файл и сколько ждать: путь и пределы приходят снаружи.
"""

import importlib
from dataclasses import dataclass, field
from pathlib import Path

from .declaration import Declaration
from .resolver import ProviderError


@dataclass
class MediaRequest:
    """Один вызов провайдера: что исполнить, чем и куда положить."""

    input: str                      # текст озвучки или промпт картинки
    params: dict                    # вся строка провайдера минус служебные столбцы
    target: Path                    # абсолютный путь результата (containment уже пройден)
    models_dir: Path                # корень локальных весов (для локальных адаптеров)


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
        """Каталог весов локальных моделей (вне git, наполняется scripts/fetch_local_models.py)."""
        local = self.config.get("local") or {}
        return self.project_root / str(local.get("models_dir", "vendor/models"))

    def spec(self, provider: str, resource_type: str = "") -> str:
        """Объявленный путь адаптера. Пара «провайдер:ресурс» точнее одного имени и ищется первой."""
        table = (self.config.get("adapters") or {}).get("by_provider") or {}
        for key in (f"{provider}:{resource_type}", provider):
            if key in table:
                return str(table[key])
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
        return adapter_cls(self.models_dir)
