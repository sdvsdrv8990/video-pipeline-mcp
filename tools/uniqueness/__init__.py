"""
tools/uniqueness — расчёт уникальности по требованию (A7.2, doc `10 §2`/`§3`).

Планировщика на сервере НЕТ намеренно (решение владельца S15): за туннелем один клиент, демон
избыточен. Проверку дёргает сам ИИ, когда это нужно; сервер лишь рекомендует момент.

Обёртка тонкая: собрать объявленные входы из данных проекта → отдать их `core/uniqueness` →
упаковать в ToolResult. Ни параметров расчёта, ни имён листов здесь нет — всё в `config/uniqueness.yaml`.
"""

from core.contracts import Fact, ToolResult
from core.engine import Engine
from core.tables import TableError
from core.uniqueness import UniquenessEngine
from tools._context import ANNOTATIONS_MODIFY, ToolContext


def register(engine: Engine, ctx: ToolContext) -> None:
    """Регистрация группы uniqueness в движке."""

    def _rows(table: str, sheet: str) -> dict:
        """Строки листа из данных проекта. Нет листа — пусто, а не исключение: отсутствие входа
        это факт расчёта (`readiness`), а не ошибка вызова."""
        try:
            snapshot = ctx.state_manager.read_snapshot(table) or {}
        except Exception:  # нет данных = нет входа
            return {}
        sheet_obj = snapshot.get(sheet) or {}
        return sheet_obj.get("rows") or {}

    def _profile(table: str, spec: dict) -> tuple[list[dict], str]:
        """Профиль фрагментов: сперва данные канала, иначе дефолт из декларации книги.

        Источник называется в ответе: дефолт декларации — не то же самое, что настроенный
        владельцем профиль, и выдавать одно за другое нельзя.
        """
        rows = _rows(table, spec.get("sheet", ""))
        if rows:
            return list(rows.values()), "project"
        book = spec.get("fallback_book")
        if not book:
            return [], "none"
        path = ctx.config_path / "templates" / "tables" / f"{book}.schema.yaml"
        if not path.exists():
            return [], "none"
        import yaml
        schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for sheet in schema.get("sheets") or []:
            if sheet.get("name") == spec.get("sheet"):
                return list(sheet.get("rows") or []), "declaration"
        return [], "none"

    def _compensation_history(table: str, spec: dict, scene_ref: str) -> int:
        """Сколько раз сцена проходила за счёт соседей — БЕЗ снятых решением записей.

        Требование сервера оспоримо: расчёт может ошибиться. Запись, помеченную снимающим
        статусом, в долг не считаем — иначе одна ошибка навсегда держала бы сцену в эскалации.
        Снимает запись сам ИИ или человек ОБЫЧНЫМ `table_update` по журнальному листу: своего
        инструмента для этого не заводим, дверь уже есть.
        """
        sheet = spec.get("record_sheet")
        if not sheet:
            return 0
        col = spec.get("scene_column", "scene_ref")
        status_col = spec.get("status_column", "signal_status")
        neutral = set(spec.get("neutralizing_statuses") or [])
        return sum(1 for r in _rows(table, sheet).values()
                   if r.get(col) == scene_ref and r.get(status_col) not in neutral)

    def _record_compensation(table: str, spec: dict, scene_ref: str, comp: dict, res: dict) -> dict:
        """Записать факт компенсации в данные проекта и СКАЗАТЬ, получилось ли.

        Без записи «применён несколько раз» неоткуда узнать, а флаг сцены остался бы разговором.
        Поэтому провал записи не глушится: он возвращается в ответ. Молчаливый `except` здесь
        означал бы, что счётчик тихо не растёт, а эскалация никогда не наступит.
        """
        sheet = spec.get("record_sheet")
        if not sheet:
            return {"recorded": False, "reason": "лист журнала не объявлен в конфиге"}
        row = {spec.get("scene_column", "scene_ref"): scene_ref,
               "compensated_items": ",".join(comp["items"]),
               "composed": res["composed"], "alert": res["alert"] or "",
               "escalated": comp["escalated"],
               # Свежая запись активна; снять её может только явное решение — статусом в этом же
               # листе (`table_update`), с указанием источника и причины.
               spec.get("status_column", "signal_status"): "active",
               spec.get("source_column", "flag_source"): "server",
               spec.get("reason_column", "reason"): ""}
        try:
            # Журнал заводит сервер: до первой компенсации листа в данных проекта нет,
            # а append проверяет существование листа (и правильно делает для клиентских правок).
            snapshot = ctx.state_manager.read_snapshot(table) or {}
            if sheet not in snapshot:
                snapshot[sheet] = {"schema": {}, "rows": {}}
                ctx.state_manager.write_snapshot(table, snapshot)
            ctx.table_engine.append(table, sheet, row, id_prefix="UALERT")
            ctx.table_engine.execute_queue(table)
            return {"recorded": True, "sheet": sheet}
        except (TableError, OSError) as e:
            return {"recorded": False, "reason": str(e)}

    async def uniqueness_check(table: str, row_id: str = "") -> "ToolResult":
        """Посчитать уникальность применения патерна и сказать, ХВАТИЛО ЛИ данных.

        Отвечает не только числом: `readiness` = full / partial / empty и поимённый список
        отсутствующих входов. Пустой вход даёт объявленный ноль, а не «100% уникально».
        """
        uniq = UniquenessEngine(ctx.config_path / "uniqueness.yaml")
        ok, cfg = ctx.safe(lambda: uniq.config)
        if not ok:
            return cfg
        sources = cfg.get("sources") or {}

        text_spec = sources.get("text") or {}
        text_rows = _rows(table, text_spec.get("sheet", ""))
        text_col = text_spec.get("column", "")
        text = str((text_rows.get(row_id) or {}).get(text_col, "") or "") if row_id else ""

        corpus_spec = sources.get("corpus") or {}
        corpus_rows = _rows(table, corpus_spec.get("sheet", ""))
        corpus = [str(r.get(corpus_spec.get("column", ""), "") or "")
                  for rid, r in corpus_rows.items()
                  if not (corpus_spec.get("exclude_current") and rid == row_id)]
        corpus = [c for c in corpus if c.strip()]

        frag_spec = sources.get("fragments") or {}
        fragments: dict[str, list] = {}
        for r in _rows(table, frag_spec.get("sheet", "")).values():
            ftype = r.get(frag_spec.get("type_column", ""))
            if ftype is None:
                continue
            fragments.setdefault(str(ftype), []).append(r.get(frag_spec.get("id_column", "")))

        profile_rows, profile_source = _profile(table, sources.get("profile") or {})

        # Сколько раз эта сцена УЖЕ проходила за счёт соседей: приём разрешён, но накапливается.
        comp_spec = cfg.get("compensation") or {}
        scene_ref = row_id or table
        history = _compensation_history(table, comp_spec, scene_ref)

        ok, res = ctx.safe(lambda: uniq.compute(text=text, corpus=corpus,
                                                fragments=fragments, profile_rows=profile_rows,
                                                compensation_history=history))
        if not ok:
            return res
        res["table"] = table
        res["row_id"] = row_id
        res["profile_source"] = profile_source
        res["scene_ref"] = scene_ref

        # A7.3: чего не хватает для сравнения — и что важнее. Отсутствие эталона конкурентов
        # приоритетнее отсутствия своих наработок: без внешнего эталона работа слепая.
        gaps_advice: list = []
        for src in cfg.get("corpus_sources") or []:
            if not _rows(table, src.get("sheet", "")):
                gaps_advice += ctx.advice.get(src.get("advice_key", ""), table=table)
        res["corpus_gaps"] = [src["id"] for src in (cfg.get("corpus_sources") or [])
                              if not _rows(table, src.get("sheet", ""))]

        comp = res["compensation"]
        if comp.get("active"):
            # Событие пишется В ДАННЫЕ проекта той же дверью, что и любая правка строки:
            # без записи «несколько раз» неоткуда узнать, а флаг сцены был бы разговором.
            comp.update(_record_compensation(table, comp_spec, scene_ref, comp, res))
            key = "uniqueness.escalated" if comp.get("escalated") else "uniqueness.compensated"
            res["recommendations"] = ctx.advice.get(key, table=table, scene_ref=scene_ref) + gaps_advice
        else:
            res["recommendations"] = gaps_advice

        facts = [Fact(type="UniquenessComputed", data={
            "table": table, "row_id": row_id, "readiness": res["readiness"],
            "composed": res["composed"], "scores": res["scores"],
            "profile_source": profile_source})]
        if res["missing_inputs"]:
            # Неполнота — самостоятельный факт: молча посчитать по части входов и отдать одно
            # число значит выдать неготовность за результат.
            facts.append(Fact(type="UniquenessIncomplete", data={
                "table": table, "row_id": row_id, "missing": res["missing_inputs"]}))
        if comp.get("active"):
            facts.append(Fact(type="UniquenessCompensated", data={
                "table": table, "scene_ref": scene_ref, "items": comp["items"],
                "history": comp["history"], "escalated": comp["escalated"]}))
        if res["alert"]:
            facts.append(Fact(type="UniquenessAlert", data={
                "table": table, "row_id": row_id, "level": res["alert"],
                "sources": res["alert_sources"],
                "composed": res["composed"], "thresholds": res["thresholds"]}))
        return ToolResult(status="success", data=res, facts=facts)

    engine.register(
        name="uniqueness_check",
        title="Уникальность: посчитать по требованию",
        description=(
            "Считает уникальность применения патерна ЛОКАЛЬНО (n-gram по словам, без внешних API) "
            "и честно сообщает, хватило ли данных: readiness = full (все входы на месте) / partial "
            "(часть входов нет — перечислены поимённо в missing_inputs) / empty (мерить нечего, "
            "число = объявленный ноль, а НЕ «100% уникально»). Сигнал поднимается по ХУДШЕЙ оценке, "
            "а не по среднему: alert_sources называет, что именно пробило порог. Выключенный в профиле тип фрагмента "
            "в расчёт не входит вовсе («тихий столбец»). Параметры, веса и пороги — config/uniqueness.yaml, "
            "профиль типов — данные канала; серверного планировщика нет, проверку дёргаешь ты сам."),
        input_schema={"type": "object", "properties": {
            "table": {"type": "string", "description": "Путь сущности с данными (где лежит read.json), напр. niches/n/networks/net1/channels/chA/videos/intro"},
            "row_id": {"type": "string", "description": "ID измеряемого применения патерна; пусто → текста нет, расчёт будет неполным"},
        }, "required": ["table"]},
        # ПИШУЩИЙ: при компенсации инструмент заводит запись в журнале сцены. Оставить READONLY
        # значило бы объявить клиенту не тот контракт, чем инструмент является.
        handler=uniqueness_check, group="uniqueness", annotations=ANNOTATIONS_MODIFY)
