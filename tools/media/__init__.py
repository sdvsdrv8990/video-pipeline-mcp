"""
tools/media — медиа-слой: кто исполняет ресурс, и само исполнение.

Два инструмента и разделение между ними такое:
- `media_provider_status` — ВИДИМОСТЬ: чем сейчас исполняется ресурс, откуда это взято,
  не идёт ли работа уже на запасном и не близок ли лимит.
- `media_generate` — ИСПОЛНЕНИЕ: строка канала → адаптер → файл в рабочей области, с приёмкой
  по диску и учтённым расходом. Синхронный провайдер отдаёт файл сразу, асинхронный —
  идентификатор задачи, и тогда сервер прозванивает её циклом внутри этого же вызова.

Цикл прозвонки живёт ВНУТРИ вызова: демона нет (решение владельца S15). Отсюда честная
граница — отвалившийся клиент задачу не теряет и не спасает, её статус прозванивается заново.
"""

from collections import defaultdict
from pathlib import Path

from core.contracts import Fact, ToolResult
from core.engine import Engine
from core.providers import (AdapterRegistry, MediaRequest, ProviderError, ProviderResolver,
                            ResultDownloader, TaskCycle, UsageLedger)
from tools._context import ANNOTATIONS_MODIFY, ANNOTATIONS_READONLY, ToolContext


def register(engine: Engine, ctx: ToolContext) -> None:
    """Регистрация группы media в движке."""

    def _rows(table: str, sheet: str) -> list[dict]:
        """Строки листа провайдеров из данных канала (с ID строки — расход пишется именно ей)."""
        try:
            snapshot = ctx.state_manager.read_snapshot(table) or {}
        except Exception:                                  # noqa: BLE001 — нет данных = нет строк
            return []
        rows = ((snapshot.get(sheet) or {}).get("rows") or {})
        # Служебное поле с «_» не уходит в параметры вызова: фильтр резолвера его отбрасывает.
        return [{**row, "_row_id": row_id} for row_id, row in rows.items()]

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

    def _source(table: str, cfg: dict) -> tuple[list[dict], str, str]:
        """Строки провайдеров + откуда они: данные канала или дефолт декларации."""
        src = cfg.get("source") or {}
        sheet = src.get("sheet", "RESOURCE_LIMITS")
        rows = _rows(table, sheet)
        if rows:
            return rows, "project", sheet
        rows = _declared(src.get("fallback_book", ""), sheet)
        return rows, ("declaration" if rows else "none"), sheet

    async def media_provider_status(table: str, resource_type: str = "") -> "ToolResult":
        """Кем и какой моделью исполняется ресурс прямо сейчас — и почему именно так.

        Смотрит строки провайдеров канала: кто основной, не исчерпан ли лимит, на кого
        переключились и близко ли предупреждение. Переключить = `table_update` по строке.
        """
        resolver = ProviderResolver(ctx.config_path / "providers.yaml")
        ok, cfg = ctx.safe(lambda: resolver.config)
        if not ok:
            return cfg
        rows, source, sheet = _source(table, cfg)

        type_col = (cfg.get("source") or {}).get("type_column", "resource_type")
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

    def _answer_url(cycle: TaskCycle, answer: dict) -> str:
        """Ссылка на результат в ответе задачи. Как называется поле — знает декларация."""
        fields = ((cycle.config.get("download") or {}).get("fetch") or {}).get("url_fields") or []
        for field in fields:
            value = answer.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _asset_path(cfg: dict, cycle: TaskCycle, table: str, resource_type: str,
                    video_slug: str, scene_id: str, params: dict) -> str:
        """Куда лёг бы результат. Имя — шаблон из декларации, расширение — из строки провайдера."""
        assets = cfg.get("assets") or {}
        rule = (assets.get("by_resource") or {}).get(resource_type) or assets.get("default") or {}
        template = str(rule.get("name") or "{video_slug}_{resource_type}_{scene_id}")
        # Неизвестное поле в шаблоне не роняет вызов: подставляется пустым, имя остаётся читаемым.
        base = template.format_map(defaultdict(str, video_slug=video_slug,
                                               resource_type=resource_type, scene_id=scene_id))
        base = "_".join(p for p in base.split("_") if p) or resource_type
        name = cycle.expected_name(base, params)
        if name == base and rule.get("ext"):
            name = f"{base}.{rule['ext']}"
        return f"{table}/{rule.get('dir', 'assets')}/{name}"

    async def media_generate(table: str, resource_type: str, input: str, scene_id: str,
                             video_slug: str = "") -> "ToolResult":
        """Исполнить ресурс активным провайдером канала: озвучить текст, нарисовать промпт.

        Провайдер и модель берутся из строки канала (кого именно — покажет
        `media_provider_status`). Синхронный провайдер отдаёт файл сразу; асинхронный —
        идентификатор задачи, и сервер прозванивает её здесь же, пока не придёт готовность
        или отказ. Файл принимается по диску, расход прибавляется строке провайдера.
        """
        resolver = ProviderResolver(ctx.config_path / "providers.yaml")
        ok, cfg = ctx.safe(lambda: resolver.config)
        if not ok:
            return cfg
        rows, source, sheet = _source(table, cfg)
        ok, decision = ctx.safe(lambda: resolver.resolve(rows, resource_type, source=source))
        if not ok:
            return decision

        cycle = TaskCycle(ctx.config_path / "media_tasks.yaml")
        params = decision["params"]
        slug = video_slug or Path(table).name
        rel = _asset_path(cfg, cycle, table, resource_type, slug, scene_id, params)
        # Тип файла — та же дверь, что у любой записи (S2): результат провайдера не привилегирован.
        ok, target = ctx.safe(lambda: (ctx.write_policy.check(rel), ctx.resolve(rel))[1])
        if not ok:
            return target
        # Каталог ассетов заводит тот, кто владеет путём. Адаптер получает готовое место —
        # иначе каждый новый адаптер обязан помнить про mkdir, и первый забывший роняет вызов.
        target.parent.mkdir(parents=True, exist_ok=True)

        registry = AdapterRegistry(ctx.config_path / "providers.yaml", ctx.config_path.parent)
        ok, adapter = ctx.safe(lambda: registry.load(decision["provider"], resource_type))
        if not ok:
            return adapter

        request = MediaRequest(input=input, params=params, target=target,
                               models_dir=registry.models_dir)
        ok, outcome = ctx.safe(lambda: adapter.generate(request))
        if not ok:
            return outcome

        waited: dict = {}
        if outcome.task_id:
            if not callable(getattr(adapter, "poll", None)):
                # Задача без прозвонки — молчание вместо причины отказа; это отказ, а не «успех».
                return ctx.err(
                    "PROVIDER_ADAPTER_MISSING",
                    f"Адаптер '{decision['provider']}' вернул задачу, но не умеет её прозвонить.",
                    "Асинхронный провайдер обязан уметь прозвонить статус — иначе причина "
                    "отказа (модерация, лимит, недоступность) не вернётся никогда.")
            # Прозвонка нужна не «дождаться», а поймать причину отказа.
            ok, waited = ctx.safe(lambda: cycle.wait(adapter.poll, outcome.task_id))
            if not ok:
                return waited
            answer = waited.get("answer") or {}
            if callable(getattr(adapter, "fetch", None)):
                ok, fetched = ctx.safe(lambda: adapter.fetch(answer, target))
            else:
                # Адаптер не забирает файл сам — забираем по ссылке из ответа задачи.
                url = _answer_url(cycle, answer)
                if not url:
                    return ctx.err(
                        "PROVIDER_FAILED", "Задача готова, но в ответе нет ссылки на результат.",
                        "Сервер ищет ссылку в полях, объявленных в config/media_tasks.yaml → "
                        "download.fetch.url_fields. Добавь туда поле этого провайдера.")
                ok, fetched = ctx.safe(
                    lambda: ResultDownloader(ctx.config_path / "media_tasks.yaml").fetch(url, target))
            if not ok:
                return fetched
            outcome.files = [target]

        verified = []
        for path in outcome.files:
            rel_file = str(Path(path).relative_to(ctx.workspace_path))
            ok, report = ctx.safe(lambda p=rel_file: cycle.verify_download(p, ctx.workspace_path))
            if not ok:
                return report
            verified.append(report)

        usage = _charge(table, sheet, source, decision, input, len(verified))
        data = {
            "table": table, "resource_type": resource_type, "provider": decision["provider"],
            "model": params.get("model", ""), "source": source,
            "files": [v["path"] for v in verified], "verified": verified,
            "on_fallback": decision["exhausted_chain"], "warning": decision["warning"],
            "usage": usage, "task": {k: v for k, v in waited.items() if k != "answer"},
        }
        return ToolResult(status="success", data=data, facts=[Fact(type="MediaGenerated", data={
            "table": table, "resource_type": resource_type, "provider": decision["provider"],
            "model": params.get("model", ""), "files": data["files"],
            "usage_after": usage.get("after"), "charged": usage.get("charged")})])

    def _charge(table: str, sheet: str, source: str, decision: dict,
                text: str, files: int) -> dict:
        """Прибавить расход строке провайдера. Не получилось — это видно, а не проглатывается."""
        ledger = UsageLedger(ctx.config_path / "providers.yaml", ctx.state_manager)
        if source != "project":
            return {"charged": False, "reason": (
                "Расход не учтён: провайдер взят из дефолтов декларации, а не из книги канала — "
                "прибавлять счётчик некуда. Заполни лист провайдеров книги канала.")}
        try:
            report = ledger.charge_call(table, sheet, decision, text=text, files=files)
        except ProviderError as e:
            # Файл уже сделан: молча «успех без расхода» означал бы вечный лимит (F72-класс).
            return {"charged": False, "code": e.code, "reason": e.message}
        report["charged"] = True
        return report

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

    engine.register(
        name="media_generate",
        title="Медиа: исполнить ресурс активным провайдером",
        description=(
            "Исполняет вид ресурса тем провайдером и той моделью, что стоят в строке канала: "
            "озвучивает текст, рисует промпт. Файл кладётся в рабочую область по объявленному "
            "имени ассета и принимается ПО ДИСКУ (существует, не пуст), а не по коду ответа. "
            "Асинхронный провайдер прозванивается циклом внутри этого же вызова — чтобы причина "
            "отказа (модерация, лимит, недоступность) вернулась текстом, а не молчанием. "
            "После вызова расход прибавляется строке провайдера, поэтому дневной лимит и переход "
            "на fallback работают, а не остаются теорией."),
        input_schema={"type": "object", "properties": {
            "table": {"type": "string", "description": "Путь сущности с данными канала (где лежит read.json)"},
            "resource_type": {"type": "string", "description": "Вид ресурса из листа провайдеров (tts_characters, image_generations, …)"},
            "input": {"type": "string", "description": "Текст для озвучки или промпт картинки"},
            "scene_id": {"type": "string", "description": "Фрагмент/сцена — попадает в имя файла и связывает ассет с листом сцен"},
            "video_slug": {"type": "string", "description": "Имя видео в названии файла (пусто → имя каталога сущности)"},
        }, "required": ["table", "resource_type", "input", "scene_id"]},
        handler=media_generate, group="media", annotations=ANNOTATIONS_MODIFY)
