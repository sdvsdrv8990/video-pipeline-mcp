"""
tools/media — медиа-слой: кто и какой моделью исполняет запрос (шаг 2).

Первый инструмент группы — не генерация, а ВИДИМОСТЬ: чем сейчас исполняется ресурс, откуда
это взято, не идёт ли работа уже на fallback и не близок ли лимит. Без этого ИИ не заметит,
что основной провайдер исчерпан и работа давно идёт запасным.

Генерации здесь пока нет намеренно: адаптеры (`core/providers/{img,tts,stt}`) — честные стабы,
и объявлять инструмент, который «сгенерирует», значит соврать в `tools/list` (G16).
"""

from core.contracts import Fact, ToolResult
from core.engine import Engine
from core.providers import ProviderResolver
from tools._context import ANNOTATIONS_READONLY, ToolContext


def register(engine: Engine, ctx: ToolContext) -> None:
    """Регистрация группы media в движке."""

    def _rows(table: str, sheet: str) -> list[dict]:
        """Строки листа провайдеров из данных канала."""
        try:
            snapshot = ctx.state_manager.read_snapshot(table) or {}
        except Exception:                                  # noqa: BLE001 — нет данных = нет строк
            return []
        return list(((snapshot.get(sheet) or {}).get("rows") or {}).values())

    def _declared(book: str, sheet: str) -> list[dict]:
        """Дефолт из декларации книги, пока книга канала не заполнена."""
        path = ctx.config_path / "templates" / "tables" / f"{book}.schema.yaml"
        if not path.exists():
            return []
        import yaml
        schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for sh in schema.get("sheets") or []:
            if sh.get("name") == sheet:
                return list(sh.get("rows") or [])
        return []

    async def media_provider_status(table: str, resource_type: str = "") -> "ToolResult":
        """Кем и какой моделью исполняется ресурс прямо сейчас — и почему именно так.

        Смотрит строки провайдеров канала: кто основной, не исчерпан ли лимит, на кого
        переключились и близко ли предупреждение. Переключить = `table_update` по строке.
        """
        resolver = ProviderResolver(ctx.config_path / "providers.yaml")
        ok, cfg = ctx.safe(lambda: resolver.config)
        if not ok:
            return cfg
        src = cfg.get("source") or {}
        sheet = src.get("sheet", "RESOURCE_LIMITS")

        rows = _rows(table, sheet)
        source = "project"
        if not rows:
            rows = _declared(src.get("fallback_book", ""), sheet)
            source = "declaration" if rows else "none"

        type_col = src.get("type_column", "resource_type")
        types = [resource_type] if resource_type else sorted(
            {str(r.get(type_col)) for r in rows if r.get(type_col)})

        resolved, failed = [], []
        for rt in types:
            ok_r, res = ctx.safe(lambda t=rt: resolver.resolve(rows, t, source=source))
            if ok_r:
                resolved.append(res)
            else:
                # Отказ по одному ресурсу не прячет остальные: тот же закон, что у фазы ТАБЛИЦЫ.
                failed.append({"resource_type": rt,
                               "code": res.error.code if res.error else "INTERNAL_ERROR",
                               "message": res.error.message if res.error else ""})
        return ToolResult(status="success", data={
            "table": table, "sheet": sheet, "source": source,
            "resolved": resolved, "failed": failed,
            "switch_hint": (f"Сменить провайдера или модель — table_update по строке листа {sheet} "
                            "(колонки provider/model/daily_limit/fallback_provider). Перезапуск не нужен."),
        }, facts=[Fact(type="ProviderResolved", data={
            "table": table, "source": source,
            "active": {r["resource_type"]: r["provider"] for r in resolved},
            "on_fallback": [r["resource_type"] for r in resolved if r["exhausted_chain"]],
            "warning": [r["resource_type"] for r in resolved if r["warning"]]})])

    engine.register(
        name="media_provider_status",
        title="Медиа: кто исполняет и какой моделью",
        description=(
            "Показывает, каким провайдером и какой моделью исполняется каждый вид ресурса "
            "(озвучка, картинки, транскрипция) прямо сейчас, откуда это взято (данные канала "
            "или дефолт декларации), не идёт ли работа уже на fallback после исчерпания лимита "
            "и не близок ли порог предупреждения. Переключение провайдера или модели — обычный "
            "table_update по строке листа провайдеров: правка данных, без перезапуска сервера."),
        input_schema={"type": "object", "properties": {
            "table": {"type": "string", "description": "Путь сущности с данными канала (где лежит read.json)"},
            "resource_type": {"type": "string", "description": "Один вид ресурса (пусто → все объявленные)"},
        }, "required": ["table"]},
        handler=media_provider_status, group="media", annotations=ANNOTATIONS_READONLY)
