"""tools/_context.py — общие зависимости тонких обёрток инструментов.

Зовёт: Engine, TableEngine, ExcelEngine, StateManager, WritePolicy, Files.
Зовут: server, excel, filesystem, media, memory, montage, search, structure, tables, uniqueness.

Логики продукта здесь нет: контекст раздаёт зависимости и переводит исключения ядра в контракт
ответа ЕДИНЫМ путём через реестр реакций. Разойдись этот путь по группам — клиент получал бы
разные коды на одну и ту же беду.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.advice import Advice
from core.contracts import ContractError, Recovery, ToolResult
from core.declaration import Declaration, instance_home
from core.firewall.rules.injection_detector import InjectionDetector
from core.engine import Engine, TemplateEngine, TemplateResolver
from core.excel import ExcelEngine
from core.ids import ChainResolver, IDGenerator, LinkRegistry, Taxonomy
from core.integrity import InstanceIdentity
from core.write_policy import WritePolicy
from core.paths import Files, configure_secret_dirs
from core.secrets import ChannelSecrets
from core.state import StateManager
from core.tables import TableEngine

# Аннотации MCP для инструментов (помогают клиенту определить уровень доступа)
ANNOTATIONS_READONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
ANNOTATIONS_MODIFY = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
# Резерв, намеренно не назначен: destructiveHint триггерит auth-гейт коннектора Claude.ai.
ANNOTATIONS_DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False}


def _firewall(config_path: Path) -> Declaration:
    """Дверь к `config/firewall.yaml`. Одна на всех читателей: своя загрузка у каждого дала
    пять разных политик на один отказ, и две из них молча ослабляли защиту."""
    return Declaration(
        config_path / "firewall.yaml", ContractError, "правил файрвола",
        "Заведи config/firewall.yaml — закрытые каталоги и паттерны инъекций объявлены там.")


@dataclass
class ToolContext:
    """Зависимости, общие для всех групп инструментов."""

    engine: Engine
    id_generator: IDGenerator
    state_manager: StateManager
    table_engine: TableEngine
    excel_engine: ExcelEngine
    template_engine: TemplateEngine
    link_registry: LinkRegistry
    workspace_path: Path
    config_path: Path

    def resolve(self, path: str) -> Path:
        """Путь внутри рабочей области — через дверь слоя (core/paths): отказ — код и факт `path`."""
        return Files(self.workspace_path).place(path)

    @property
    def template_resolver(self) -> TemplateResolver:
        """Чьи шаблоны действуют для адреса: проекта (`.templates/`) или сервера."""
        return TemplateResolver(self.workspace_path, self.config_path)

    @property
    def advice(self) -> Advice:
        """Советы в успешном ответе — тексты из config/recommendations.yaml, не из кода."""
        return Advice(self.config_path / "recommendations.yaml")

    @property
    def secrets(self) -> ChannelSecrets:
        """Ключи провайдеров канала. Единственная дверь в закрытый каталог — и она серверная:
        значение уходит только в исходящий вызов провайдера, клиенту — отпечаток."""
        return ChannelSecrets(self.workspace_path, instance_home(self.config_path))

    @property
    def write_policy(self) -> WritePolicy:
        """Какие типы файлов сервер вправе материализовать (список в firewall.yaml)."""
        return WritePolicy(self.config_path)

    @property
    def read_limit(self) -> int:
        """Сколько байт файла читать в ответ (FS-12): `firewall.yaml → file_limits.read_bytes`, без запасного числа."""
        return _firewall(self.config_path).need("file_limits", "read_bytes", kind=int)

    @property
    def injection_flagger(self) -> InjectionDetector:
        """Детектор для ПОМЕТКИ чужого текста в выводе. Паттерны — боевые, из firewall.yaml:
        второй копии в коде быть не должно. Это подсказка клиенту, а не барьер — барьер — конверт."""
        declared = _firewall(self.config_path).optional()
        patterns = (declared.get("injection_detection") or {}).get("patterns")
        return InjectionDetector(patterns)

    @property
    def taxonomy(self) -> Taxonomy:
        """Объявленная иерархия типов (живёт в шаблонах, поднимается движком шаблонов)."""
        return self.template_engine.taxonomy

    @property
    def chain_resolver(self) -> ChainResolver:
        """Резолвер цепочки по каталогу назначения — собран из уже имеющихся частей."""
        return ChainResolver(self.workspace_path, self.link_registry, self.taxonomy)

    def err(self, code: str, message: str = "", reason: str = "",
            suggested_tool: str | None = None) -> ToolResult:
        """Ошибочный ToolResult через реестр реакций (yaml = источник class/recovery).

        Для кода из реестра class/message_template/recovery берутся из server_reactions.yaml
        (raw message сохраняет специфику). reason/suggested_tool — fallback лишь для кодов вне реестра.
        """
        return self.engine._error(code, message, Recovery(reason=reason, suggested_tool=suggested_tool))

    def safe(self, call: Callable[[], Any]) -> tuple[bool, Any]:
        """Выполнить sync-вызов ядра; объявленное исключение — готовый отказ (перевод один — `Engine.refusal`),
        чужое летит дальше, на границу исполнения.

        Returns (ok, value_or_error_result): при ok=False во втором элементе —
        готовый ошибочный ToolResult, иначе — результат ядра.
        """
        try:
            return True, call()
        except Exception as e:
            refused = self.engine.refusal(e)
            if refused is None:
                raise
            return False, refused


def build_context(engine: Engine, id_generator: IDGenerator, state_manager: StateManager,
                  config_path: Path) -> ToolContext:
    """Собрать контекст: движки поднимаются здесь, группы их только используют."""
    workspace_path = state_manager.workspace_path
    # Какие каталоги внутри рабочей области закрыты — объявлено в firewall.yaml. Конфиг
    # только РАСШИРЯЕТ встроенный минимум: не загрузился — запрет остаётся (fail-closed).
    _declared = (_firewall(config_path).optional().get("secret_paths") or {}).get("dirs") or []
    configure_secret_dirs(_declared)
    # Личность инстанса — ключ подписи выпускается в доме инстанса (вне workspace/).
    identity = InstanceIdentity(instance_home(config_path), workspace_path)
    identity.ensure_key()
    return ToolContext(
        engine=engine,
        id_generator=id_generator,
        state_manager=state_manager,
        # Категория 3 (данные) — TableEngine поверх read.json/write.json.
        table_engine=TableEngine(state_manager, id_generator),
        # Категория 2 (структура) — ExcelEngine поверх .xlsx (openpyxl).
        excel_engine=ExcelEngine(workspace_path),
        # Композиция по ссылке + контроль глубины.
        template_engine=TemplateEngine(workspace_path, id_generator, config_path / "templates" / "workspace", config_path),
        # Анонимные → ORPHAN, link() в одном месте; записи подписаны личностью
        # инстанса (ключ лежит рядом с .env, вне workspace) — чужие правки отклоняются.
        link_registry=LinkRegistry(workspace_path, identity=identity),
        workspace_path=workspace_path,
        config_path=config_path,
    )
