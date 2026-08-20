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

import json
from collections import defaultdict
from functools import partial
from pathlib import Path

from core.contracts import Fact, ToolResult
from core.engine import Engine
from core.providers import (AdapterRegistry, MediaRequest, ModelSpec, ProviderError, ProviderResolver,
                            ResultDownloader, TaskCycle, UsageLedger)
from core.providers.catalog import ModelCatalog, OnlineCatalog
from core.providers.hardware import probe
from core.providers.installer import ModelInstaller
from core.runner import RunnerSupervisor
from core.secrets import SecretError, redact
from tools._context import ANNOTATIONS_MODIFY, ANNOTATIONS_READONLY, ToolContext


def register(engine: Engine, ctx: ToolContext) -> None:
    """Регистрация группы media в движке."""

    def _rows(table: str, sheet: str) -> list[dict]:
        """Строки листа провайдеров из данных канала (с ID строки — расход пишется именно ей)."""
        try:
            snapshot = ctx.state_manager.read_snapshot(table) or {}
        except Exception:  # нет данных = нет строк
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

    def _key_state(table: str, provider: str, resource_type: str, registry) -> dict:
        """Есть ли ключ у провайдера — БЕЗ значения: имя, отпечаток, дата.

        Отпечаток отвечает на вопрос «тот ли ключ стоит» и не даёт восстановить сам ключ, поэтому
        показывать его можно. Значение не выдаётся ни здесь, ни где-либо ещё.
        """
        required = registry.requires_key(provider, resource_type)
        owner = ctx.secrets.owner_of(table)
        state = {"required": required, "present": False, "fingerprint": "", "updated": "",
                 "stored_at": owner}
        if owner:
            try:
                for row in ctx.secrets.status(owner, provider):
                    state.update({k: row[k] for k in ("present", "fingerprint", "updated")})
            except SecretError as e:
                # Файл ключей есть, но не читается (чужой инстанс, потерянный ключ шифрования).
                # Это про ОДИН канал: ослепить весь отчёт о провайдерах он не вправе.
                state.update({"unreadable": True, "code": e.code, "reason": e.reason})
        if required and not state["present"]:
            state["how_to_set"] = (f"python scripts/set_provider_key.py --channel {table} "
                                   f"--provider {provider} — только владелец, только вручную: "
                                   "прав читать или записывать ключ у сервера нет.")
        return state

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

        registry = AdapterRegistry(ctx.config_path / "providers.yaml", ctx.config_path.parent)
        resolved, failed = [], []
        for rt in types:
            ok_r, res = ctx.safe(partial(resolver.resolve, rows, rt, source=source))
            if ok_r:
                res["key"] = _key_state(table, res["provider"], rt, registry)
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
            # Сервер СОВЕТУЕТ, чем исполнять, и не навязывает: тексты и модели — в декларации.
            "recommendations": ctx.advice.get("media_provider_status.choice", table=table),
        }, facts=[Fact(type="ProviderResolved", data={
            "table": table, "source": source,
            "active": {r["resource_type"]: r["provider"] for r in resolved},
            "on_fallback": [r["resource_type"] for r in resolved if r["exhausted_chain"]],
            "warning": [r["resource_type"] for r in resolved if r["warning"]]})])

    def _key_for(table: str, provider: str) -> str:
        """Ключ провайдера для адреса: ближайший канал вверх по дереву, у которого он есть."""
        owner = ctx.secrets.owner_of(table)
        return ctx.secrets.get(owner, provider) if owner else ""

    def _hide_key(result: "ToolResult", api_key: str) -> "ToolResult":
        """Последний рубеж: провайдер способен процитировать присланный ключ в тексте отказа."""
        if api_key and result.error:
            result.error.message = redact(result.error.message, [api_key])
            if result.error.recovery:
                result.error.recovery.reason = redact(result.error.recovery.reason, [api_key])
        return result

    def _answer_url(cycle: TaskCycle, answer: dict) -> str:
        """Ссылка на результат в ответе задачи. Как называется поле — знает декларация."""
        fields = ((cycle.config.get("download") or {}).get("fetch") or {}).get("url_fields") or []
        for field in fields:
            value = answer.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _input_mode(cfg: dict, resource_type: str) -> str:
        """Чем является `input` для этого вида ресурса: текстом или путём к файлу."""
        rules = cfg.get("resources") or {}
        rule = (rules.get("by_resource") or {}).get(resource_type) or rules.get("default") or {}
        return str(rule.get("input") or "text")

    def _asset_path(cfg: dict, cycle: TaskCycle, table: str, resource_type: str,
                    video_slug: str, scene_id: str, params: dict, source_stem: str = "") -> str:
        """Куда лёг бы результат. Имя — шаблон из декларации, расширение — из строки провайдера."""
        assets = cfg.get("assets") or {}
        rule = (assets.get("by_resource") or {}).get(resource_type) or assets.get("default") or {}
        template = str(rule.get("name") or "{video_slug}_{resource_type}_{scene_id}")
        # Неизвестное поле в шаблоне не роняет вызов: подставляется пустым, имя остаётся читаемым.
        base = template.format_map(defaultdict(str, video_slug=video_slug,
                                               resource_type=resource_type, scene_id=scene_id,
                                               source_stem=source_stem))
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

        # Виды ресурса, которые ПЕРЕРАБАТЫВАЮТ готовый файл (фон, апскейл), получают путь, а не
        # текст. Путь приходит от ИИ, поэтому проходит containment той же дверью, что и запись:
        # `../../.env` в поле input иначе уехало бы клиенту вместе с результатом.
        source_file = None
        if _input_mode(cfg, resource_type) == "file":
            ok, source_file = ctx.safe(lambda: ctx.resolve(input))
            if not ok:
                return source_file
            if not source_file.is_file():
                return ctx.err(
                    "FILE_NOT_FOUND", f"Исходного файла нет: {input}",
                    f"Вид ресурса '{resource_type}' перерабатывает готовый файл, а не создаёт "
                    "новый: в input нужен путь к существующему файлу внутри рабочей области.")
        rel = _asset_path(cfg, cycle, table, resource_type, slug, scene_id, params,
                          source_stem=source_file.stem if source_file else "")
        # Тип файла — та же дверь, что у любой записи (S2): результат провайдера не привилегирован.
        def _checked_target() -> Path:
            ctx.write_policy.check(rel)          # бросает при запрещённом типе
            return ctx.resolve(rel)

        ok, target = ctx.safe(_checked_target)
        if not ok:
            return target
        # Каталог ассетов заводит тот, кто владеет путём. Адаптер получает готовое место —
        # иначе каждый новый адаптер обязан помнить про mkdir, и первый забывший роняет вызов.
        target.parent.mkdir(parents=True, exist_ok=True)

        # S23: ключ проверяется ДО подъёма адаптера — он свойство провайдера, а не кода, и без
        # него вызов не состоится в любом случае. Берётся из закрытого файла канала и живёт
        # только до конца вызова.
        registry = AdapterRegistry(ctx.config_path / "providers.yaml", ctx.config_path.parent)
        api_key = ""
        if registry.key_source(decision["provider"], resource_type) == "runner":
            # Ключ раннера — не секрет владельца, а токен, выданный сервером при запуске. Нет
            # токена — значит раннера нет: отказ ведёт к инструменту, а не к скрипту с ключами.
            # Молчаливого отката «посчитаем в сервере» здесь нет намеренно: строка канала просила
            # изоляцию, и подменить её тишиной значило бы соврать про то, где идёт расчёт.
            api_key = _supervisor().token()
            if not api_key:
                return ctx.err(
                    "RUNNER_NOT_RUNNING", "Строка канала просит считать в раннере, а он не поднят.",
                    "Подними его: media_runner(action='start').")
        elif registry.requires_key(decision["provider"], resource_type):
            ok, api_key = ctx.safe(lambda: _key_for(table, decision["provider"]))
            if not ok:
                return api_key
            if not api_key:
                return ctx.err(
                    "PROVIDER_KEY_MISSING",
                    f"У провайдера '{decision['provider']}' нет ключа доступа для этого канала.",
                    "Прав читать или записывать ключ у меня нет — это не ограничение вызова, а "
                    "устройство сервера. Вставить ключ может только владелец и только вручную: "
                    f"python scripts/set_provider_key.py --channel {table} "
                    f"--provider {decision['provider']} (ввод скрыт, значение шифруется до записи). "
                    "Пока ключа нет — поставь в строке канала провайдера, работающего без ключа "
                    "(например, локального): это обычный table_update.")

        # Что модель принимает и в каких пределах, знает сама модель. Расхождение ловим ДО вызова:
        # у платного провайдера неверный параметр стоит денег, у локального — минут расчёта, и в
        # обоих случаях ответ «не то, что просили» приходит без жалобы.
        spec_engine = ModelSpec(registry)
        ok, spec = ctx.safe(lambda: spec_engine.of(decision["provider"],
                                                   str(params.get("model") or ""), api_key))
        if ok:
            gaps = spec_engine.check(spec, params)
            if gaps:
                return ctx.err(
                    "VALIDATION_ERROR",
                    "Строка канала просит у модели то, чего она не умеет: "
                    + "; ".join(f"{g['param']}={g['value']!r} — {g['problem']}" for g in gaps),
                    f"Допустимое: {json.dumps({g['param']: g['allowed'] for g in gaps}, ensure_ascii=False)}. "
                    f"Источник спецификации — {spec.get('source')}. Поправь строку листа провайдеров "
                    "(table_update) или возьми модель, которая это умеет: "
                    "media_models(scope='spec', provider=..., model=...).")

        ok, adapter = ctx.safe(lambda: registry.load(decision["provider"], resource_type))
        if not ok:
            return adapter

        request = MediaRequest(input=input, params=params, target=target,
                               models_dir=registry.models_dir, api_key=api_key, source=source_file,
                               provider=decision["provider"], resource_type=resource_type,
                               workspace=ctx.workspace_path)
        ok, outcome = ctx.safe(lambda: adapter.generate(request))
        if not ok:
            return _hide_key(outcome, api_key)

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
            ok, polled = ctx.safe(lambda: cycle.wait(adapter.poll, outcome.task_id))
            if not ok:
                return polled
            waited = polled
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
            ok, report = ctx.safe(partial(cycle.verify_download, rel_file, ctx.workspace_path))
            if not ok:
                return report
            verified.append(report)

        usage = _charge(table, sheet, source, decision, input, len(verified))
        data = {
            "table": table, "resource_type": resource_type, "provider": decision["provider"],
            "model": params.get("model", ""), "source": source,
            "files": [v["path"] for v in verified], "verified": verified,
            "on_fallback": decision["exhausted_chain"], "warning": decision["warning"],
            # Где считалось: карта или процессор. Без этого «медленно» неотличимо от «сломано».
            "compute": outcome.meta.get("compute") or {},
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

    async def media_models(kind: str = "", scope: str = "installed", limit: int = 20,
                           provider: str = "", table: str = "", model: str = "") -> "ToolResult":
        """Какие модели стоят на машине, какие можно поставить и какие доступны онлайн.

        `scope=installed` — что уже лежит; `local` — что можно поставить сюда;
        `online` — что умеет вызвать шлюз. С `provider` и `table` онлайн-список спрашивается у
        САМОГО провайдера его ключом: новая линейка появляется у него раньше, чем в реестре шлюза.
        """
        registry = AdapterRegistry(ctx.config_path / "providers.yaml", ctx.config_path.parent)
        catalog = ModelCatalog(ctx.config_path / "providers.yaml", registry.models_dir)
        online = OnlineCatalog(ctx.config_path / "providers.yaml")
        source, live_error = "gateway_registry", None
        hardware = probe(registry.models_dir, catalog.local.get("gpu"))
        if scope == "spec":
            # Что модель принимает и в каких пределах. Спрашивается у неё самой (или у её файлов),
            # а не сочиняется: список параметров модели — её свойство, и оно меняется без нас.
            key = ""
            if provider and table and registry.requires_key(provider):
                ok_key, key = ctx.safe(lambda: _key_for(table, provider))
                key = key if ok_key else ""
            ok, spec = ctx.safe(lambda: ModelSpec(registry).of(provider, model, key))
            if not ok:
                return spec
            return ToolResult(status="success", data={
                "scope": scope, "provider": provider, "model": model,
                "known": spec.get("known"), "source": spec.get("source"), "why": spec.get("why"),
                "parameters": (spec.get("schema") or {}).get("properties") or {},
                "required": (spec.get("schema") or {}).get("required") or [],
                "hint": ("Столбцы строки провайдера должны укладываться в эти пределы — иначе "
                         "media_generate откажет ДО вызова (у платного это стоило бы денег). "
                         "Правится table_update по строке листа провайдеров."),
            }, facts=[Fact(type="ModelSpecRead", data={
                "provider": provider, "model": model, "source": spec.get("source"),
                "known": bool(spec.get("known")),
                "parameters": sorted((spec.get("schema") or {}).get("properties") or {})})])
        if scope == "installed":
            rows = catalog.with_fit([r for r in catalog.installed() if not kind or r["kind"] == kind],
                                    hardware)
            source = "disk"
        elif scope == "online":
            rows = online.available(kind or "image", limit=limit)
            if provider and table:
                # Ключ берётся из закрытого файла канала и уходит только на объявленный адрес.
                ok, key = ctx.safe(lambda: _key_for(table, provider))
                if not ok:
                    # Ключ нечитаем (чужой инстанс) — это причина рядом со списком, а не отказ
                    # всего вызова: реестр шлюза остаётся доступен и без ключа.
                    live_error = {"code": key.error.code if key.error else "INTERNAL_ERROR",
                                  "message": key.error.message if key.error else ""}
                    key = ""
                if not ok:
                    pass
                elif not key:
                    live_error = {"code": "PROVIDER_KEY_MISSING", "message": (
                        f"Ключа '{provider}' для этого канала нет — спросить его список нечем. "
                        "Ниже то, что знает реестр шлюза на день своей сборки.")}
                else:
                    ok, fresh = ctx.safe(lambda: online.merge(online.live(provider, key, kind), kind))
                    if ok:
                        rows, source = fresh[:limit] if limit else fresh, "provider"
                    else:
                        # Частичный результат помечаем: список из реестра остаётся, но он старее.
                        hidden = _hide_key(fresh, key).error
                        live_error = {"code": hidden.code if hidden else "INTERNAL_ERROR",
                                      "message": hidden.message if hidden else ""}
        else:
            # Описание тянется всегда: выбирать модель по размеру и числу загрузок — вслепую.
            ok, rows = ctx.safe(lambda: catalog.available(kind or "tts", limit=limit, describe=True))
            if not ok:
                return rows
            # Число параметров без объёма памяти не значит ничего: вердикт идёт вместе со списком.
            rows = catalog.with_fit(rows, hardware)
            source = "catalog"
        data = {
            "scope": scope, "kind": kind, "source": source, "models": rows,
            # Железо читается БЕЗ root (/proc/meminfo, cgroup, ядра, свободное место); чего
            # прочесть нельзя — перечислено в hardware.unknown, а не выдано за отсутствие.
            "hardware": hardware if scope != "online" else None,
            # Что держим поднятым прямо сейчас: подъём модели стоит секунд, и видеть занятое так
            # же важно, как свободное — иначе «карта полная» выглядит поломкой железа.
            "pool": registry.pool.stats() if scope == "installed" else None,
            "hint": ("Поставить локальную модель — media_model_install; исполнять ею — поставить "
                     "её имя в столбец model листа провайдеров книги канала (table_update). "
                     "Онлайн-список свежее у самого провайдера: media_models(scope='online', "
                     "provider='<имя>', table='<канал>') — понадобится ключ, его вносит владелец."),
        }
        if live_error:
            data["live_error"] = live_error
            data["note"] = ("Список НЕ от провайдера, а из реестра шлюза: он знает то, что знал на "
                            "день сборки, и свежей линейки в нём может не быть.")
        return ToolResult(status="success", data=data, facts=[Fact(type="ModelsListed", data={
            "scope": scope, "kind": kind, "source": source, "count": len(rows),
            "live_error": (live_error or {}).get("code", "")})])

    def _supervisor() -> RunnerSupervisor:
        """Жизненный цикл раннера. Адрес, порт и пределы ожидания — из декларации, не из кода."""
        registry = AdapterRegistry(ctx.config_path / "providers.yaml", ctx.config_path.parent)
        return RunnerSupervisor(registry, ctx.config_path.parent)

    def _installer() -> ModelInstaller:
        """Наблюдатель за установками: пределы прозвонки — из декларации, не из кода."""
        registry = AdapterRegistry(ctx.config_path / "providers.yaml", ctx.config_path.parent)
        catalog = ModelCatalog(ctx.config_path / "providers.yaml", registry.models_dir)
        progress = ((catalog.install_rules.get("progress")) or {})
        return ModelInstaller(catalog,
                              heartbeat_sec=float(progress.get("heartbeat_sec", 2)),
                              stale_after_sec=float(progress.get("stale_after_sec", 120)))

    async def media_model_install(model_id: str, kind: str, confirm: bool = False) -> "ToolResult":
        """Начать установку локальной модели. Идёт в фоне — ход прозванивается отдельно.

        Ставится только объявленный формат и только из объявленного источника: `.bin/.ckpt/.pt`
        это pickle, его загрузка выполняет код из файла. Размер ограничен декларацией.
        """
        if not confirm:
            return ctx.err(
                "CONFIRM_REQUIRED", f"Установка '{model_id}' скачает веса на диск сервера.",
                "Это гигабайты и внешний источник — операция подтверждается явно: повтори с "
                "confirm=true. Сначала посмотри, что ставишь: media_models(scope='local').")
        ok, state = ctx.safe(lambda: _installer().start(model_id, kind))
        if not ok:
            return state
        return ToolResult(status="success", data={
            "install_id": state["install_id"], "model_id": model_id, "kind": kind,
            "phase": state["phase"],
            "next": ("Установка идёт в фоне — гигабайты качаются минутами. Прозвони её: "
                     f"media_install_status(install_id='{state['install_id']}'). Отвалившийся "
                     "клиент установку не теряет и не спасает: она продолжается, статус "
                     "прозванивается заново."),
        }, facts=[Fact(type="ModelInstallStarted", data={
            "install_id": state["install_id"], "model_id": model_id, "kind": kind})])

    async def media_install_status(install_id: str = "") -> "ToolResult":
        """Что с установками моделей: идёт, готово, отказ или прервано.

        Прогресс считается по диску, а не по отчёту загрузчика: он способен отрапортовать и
        оборваться. Если байты не растут дольше объявленного срока — это «прервано», а не «идёт».
        """
        ok, rows = ctx.safe(lambda: _installer().status(install_id))
        if not ok:
            return rows
        done = [r for r in rows if r["phase"] == "done"]
        broken = [r for r in rows if r["phase"] in ("failed", "stale")]
        return ToolResult(status="success", data={
            "installs": rows,
            "running": [r["install_id"] for r in rows if r["phase"] == "running"],
            "hint": ("Прерванную или упавшую установку можно просто повторить: скачанное не "
                     "выбрасывается, загрузка продолжится с места обрыва."),
        }, facts=[Fact(type="ModelInstallStatus", data={
            "total": len(rows), "done": [r["model_id"] for r in done],
            "failed": [{"model": r["model_id"], "code": r.get("code", ""),
                        "phase": r["phase"]} for r in broken]})])

    async def media_runner(action: str = "status", mode: str = "process") -> "ToolResult":
        """Поднять, остановить или прозвонить раннер локальных моделей.

        Раннер считает модели в ОТДЕЛЬНОМ процессе: его падение или нехватка памяти не роняют
        сервер и не рвут связь. Сам он не поднимается никогда — только этой командой.
        """
        if action not in ("start", "stop", "status"):
            return ctx.err(
                "VALIDATION_ERROR", f"Неизвестное действие раннера: '{action}'.",
                "Действия ровно три: start (поднять), stop (остановить), status (прозвонить).")
        calls = {"start": lambda: _supervisor().start(ctx.workspace_path, mode=mode),
                 "stop": lambda: _supervisor().stop(),
                 "status": lambda: _supervisor().status()}
        ok, state = ctx.safe(calls[action])
        if not ok:
            return state
        return ToolResult(status="success", data={
            **state, "action": action,
            "hint": ("Считать в раннере или в самом сервере — выбор строки канала: провайдер "
                     "Local_runner исполняет модель в раннере, Local_onnx_bg и прочие Local_* — "
                     "прямо в сервере. Переключение обычным table_update, без перезапуска. "
                     "Раннер не поднимается сам ни по расписанию, ни при первом вызове."),
        }, facts=[Fact(type="RunnerState", data={
            "action": action, "phase": state.get("phase"), "pid": state.get("pid", 0),
            "mode": state.get("mode", ""), "url": state.get("url", "")})])

    engine.register(
        name="media_runner",
        title="Медиа: раннер локальных моделей (отдельный процесс)",
        description=(
            "Управляет раннером — отдельным процессом, в котором считаются локальные модели. "
            "Зачем он: инференс в самом сервере роняет сервер вместе с собой (нехватка памяти или "
            "сбой в графе рвут связь с клиентом), а рестарт сервера теряет поднятые модели. "
            "action=start поднимает раннер и ждёт, пока он ответит; stop останавливает; status "
            "показывает фазу (running, exited, stopped), pid, время работы и что раннер держит "
            "поднятым. Упавший раннер виден как exited вместе с хвостом его журнала — сам он не "
            "перезапускается, потому что тихий подъём спрятал бы причину падения. "
            "Сервер не поднимает раннер самостоятельно: ни по расписанию, ни при первом вызове — "
            "фоновых процессов у сервера нет по решению владельца. "
            "Считать В раннере или в самом сервере — выбор строки канала (провайдер Local_runner "
            "против Local_*), обычный table_update. Пока раннер не поднят, вызов через Local_runner "
            "честно откажет кодом RUNNER_NOT_RUNNING, а не посчитает молча в сервере: строка "
            "канала просила изоляцию, и подмена её тишиной скрыла бы, что изоляции нет."),
        input_schema={"type": "object", "properties": {
            "action": {"type": "string", "enum": ["status", "start", "stop"], "default": "status",
                       "description": "status — прозвонить; start — поднять; stop — остановить"},
            "mode": {"type": "string", "enum": ["process", "docker"], "default": "process",
                     "description": "action=start: process — тем же интерпретатором; docker — в контейнере (зависимости инференса заперты в образе)"},
        }}, handler=media_runner, group="media", annotations=ANNOTATIONS_MODIFY)

    engine.register(
        name="media_models",
        title="Медиа: какие модели есть, что можно поставить",
        description=(
            "Показывает модели: что уже стоит на машине (scope=installed), что можно поставить "
            "локально из живого каталога с отсевом по объявленным фильтрам (scope=local), и что "
            "умеет вызвать шлюз онлайн — с провайдером, эндпоинтом и ценой (scope=online). "
            "Список онлайн-моделей виден БЕЗ ключа — из реестра шлюза. А с provider и table он спрашивается у САМОГО провайдера его ключом: новая линейка появляется у него раньше, чем в реестре, и модель, которой реестр не знает, придёт помеченной known_to_gateway=false, а не пропадёт. Ключ уходит только на объявленный адрес и в ответе не возвращается. "
            "scope=spec отвечает на другой вопрос — ЧТО МОДЕЛЬ УМЕЕТ: какие параметры принимает, "
            "с какими значениями, дефолтами и пределами (одна модель работает на 1024 и 2048 и не "
            "умеет 4К, у другой нативные 512). Спека берётся у самого провайдера, из справочника "
            "проекта или из файлов модели — источник назван в ответе, и «неизвестно» не выдаётся "
            "за «ограничений нет». Спрашивать её стоит ДО правки строки канала: media_generate "
            "сверяет параметры со спекой и откажет до вызова, а не после оплаты."),
        input_schema={"type": "object", "properties": {
            "kind": {"type": "string", "description": "Вид: tts, image, stt, bg_removal, upscale (пусто → по умолчанию для scope)"},
            "scope": {"type": "string", "enum": ["installed", "local", "online", "spec"], "default": "installed",
                      "description": "installed — стоит сейчас; local — можно поставить; online — умеет шлюз; spec — что модель принимает на вход"},
            "limit": {"type": "integer", "default": 20, "description": "Сколько строк показать"},
            "provider": {"type": "string", "description": "scope=online/spec: у КОГО спрашивать (для scope=online нужен его ключ)"},
            "table": {"type": "string", "description": "scope=online/spec: канал, чьим ключом спрашивать провайдера"},
            "model": {"type": "string", "description": "scope=spec: модель, чьи параметры и пределы показать"},
        }}, handler=media_models, group="media", annotations=ANNOTATIONS_READONLY)

    engine.register(
        name="media_model_install",
        title="Медиа: поставить локальную модель",
        description=(
            "Начинает загрузку весов локальной модели на диск сервера. Гигабайты качаются "
            "минутами, поэтому установка идёт В ФОНЕ и возвращает install_id — ход прозванивается "
            "через media_install_status, который скажет и об успехе, и об обрыве. По завершении "
            "модель попадает в опись, и канал может исполнять ею (table_update по столбцу model). "
            "Ставится только объявленный формат из объявленного источника и не больше объявленного "
            "размера: веса .bin/.ckpt/.pt — это pickle, чья загрузка выполняет код из файла. "
            "Требует confirm=true: это гигабайты с внешнего источника."),
        input_schema={"type": "object", "properties": {
            "model_id": {"type": "string", "description": "Имя модели из media_models(scope='local')"},
            "kind": {"type": "string", "description": "Вид модели: tts, image, bg_removal, upscale"},
            "confirm": {"type": "boolean", "default": False,
                        "description": "Осознанное подтверждение загрузки весов на диск сервера"},
        }, "required": ["model_id", "kind"]},
        handler=media_model_install, group="media", annotations=ANNOTATIONS_MODIFY)

    engine.register(
        name="media_install_status",
        title="Медиа: как идёт установка модели",
        description=(
            "Показывает ход установок весов: идёт, готово, отказ с причиной или прервано. "
            "Прогресс меряется по диску (сколько байт реально легло), а не по отчёту загрузчика — "
            "он способен отрапортовать и оборваться. Если байты не растут дольше объявленного "
            "срока, установка честно называется прерванной, а не остаётся вечным «идёт». "
            "Прерванную можно просто повторить: скачанное не выбрасывается."),
        input_schema={"type": "object", "properties": {
            "install_id": {"type": "string",
                           "description": "Одна установка (пусто → все идущие и завершённые)"},
        }}, handler=media_install_status, group="media", annotations=ANNOTATIONS_READONLY)

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
            "озвучивает текст, рисует промпт, снимает фон с картинки, увеличивает её. Виды, "
            "которые ПЕРЕРАБАТЫВАЮТ готовый файл (bg_removals, upscales), ждут в input путь к "
            "нему внутри рабочей области, а не текст — что именно ждёт вид ресурса, объявлено "
            "сервером. Файл кладётся в рабочую область по объявленному "
            "имени ассета и принимается ПО ДИСКУ (существует, не пуст), а не по коду ответа. "
            "Асинхронный провайдер прозванивается циклом внутри этого же вызова — чтобы причина "
            "отказа (модерация, лимит, недоступность) вернулась текстом, а не молчанием. "
            "После вызова расход прибавляется строке провайдера, поэтому дневной лимит и переход "
            "на fallback работают, а не остаются теорией."),
        input_schema={"type": "object", "properties": {
            "table": {"type": "string", "description": "Путь сущности с данными канала (где лежит read.json)"},
            "resource_type": {"type": "string", "description": "Вид ресурса из листа провайдеров (tts_characters, image_generations, bg_removals, upscales, …)"},
            "input": {"type": "string", "description": "Текст озвучки, промпт картинки — либо путь к исходному файлу для видов, перерабатывающих готовое (bg_removals, upscales)"},
            "scene_id": {"type": "string", "description": "Фрагмент/сцена — попадает в имя файла и связывает ассет с листом сцен"},
            "video_slug": {"type": "string", "description": "Имя видео в названии файла (пусто → имя каталога сущности)"},
        }, "required": ["table", "resource_type", "input", "scene_id"]},
        handler=media_generate, group="media", annotations=ANNOTATIONS_MODIFY)
