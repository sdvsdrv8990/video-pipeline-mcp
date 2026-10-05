"""tests/quick/test_error_boundary.py — граница исполнения инструментов: что уходит клиенту, что остаётся владельцу.

Падение уходит клиенту общим текстом с номером события, а причина с трассировкой остаётся в журнале сервера. Ошибка
привязки параметров — вина клиента, она называет имя параметра, но не значение. Объявленный отказ предметной области
(`ContractError`) несёт свой код и текст. `TypeError` изнутри обработчика — падение, а не вина клиента.
"""

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.contracts import ContractError, ToolResult
from core.engine.engine import Engine
from core.paths import PathEscapeError
from core.reactions import Reactions

_checks = 0
_fails: list[str] = []
SECRET = "/home/owner/.secrets/provider.key"


def ok(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


class _Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _text(result: ToolResult) -> str:
    e = result.error
    return " ".join(filter(None, [e.message, e.recovery.reason if e.recovery else ""])) if e else ""


async def main():
    engine = Engine(reactions=Reactions(ROOT / "config" / "server_reactions.yaml"))
    schema = {"type": "object", "properties": {}}

    async def crashes():
        raise RuntimeError(f"не открылся {SECRET}")

    async def body_type_error():
        return None + 1

    async def domain_refusal():
        raise ContractError("SHEET_NOT_FOUND", "Лист «Итоги» не найден", reason="Посмотри список листов")

    async def refusal_over_cause():
        try:
            open(SECRET)
        except OSError as exc:
            raise ContractError("RECONCILE_ROLLED_BACK", "Reconcile откачен: ни одна сущность не сдвинута") from exc

    async def path_escape():
        raise PathEscapeError("../../etc/passwd")

    async def shaped(path: str, width: int = 0, mode: str = "a"):
        return ToolResult(status="success", data={})

    async def by_facts():
        raise ContractError("FILE_EXISTS", facts={"path": "books/a.xlsx"})

    async def fact_missing():
        raise ContractError("FILE_EXISTS")

    async def literal_braces():
        raise ContractError("ROW_NOT_FOUND", "Строки R9 нет")

    for name, handler in (("падает", crashes), ("ошибка_в_теле", body_type_error), ("отказ", domain_refusal),
                          ("отказ_поверх_причины", refusal_over_cause), ("выход_из_корня", path_escape),
                          ("по_фактам", by_facts), ("без_факта", fact_missing), ("скобки_примера", literal_braces)):
        engine.register(name, "проба границы", schema, handler)
    engine.register("по_схеме", "проба схемы", {
        "type": "object", "required": ["path"],
        "properties": {"path": {"type": "string"}, "width": {"type": "integer"}, "mode": {"enum": ["a", "b"]}}}, shaped)

    seen = _Records()
    log = logging.getLogger("core.engine.engine")
    log.addHandler(seen)
    log.setLevel(logging.INFO)

    r = await engine.call("падает", {})
    ok(r.status == "error" and r.error.code == "INTERNAL_ERROR", f"падение — INTERNAL_ERROR (вышло {r.error.code if r.error else r.status})")
    ok(SECRET not in _text(r), f"текст падения не уходит клиенту (ответ: {_text(r)[:90]!r})")
    crash = [x for x in seen.records if x.exc_info]
    ok(len(crash) == 1, f"падение записано в журнал сервера с трассировкой ровно один раз (записей: {len(crash)})")
    if crash:
        ok(SECRET in "".join(logging.Formatter().formatException(crash[0].exc_info)),
           "причина падения — в журнале владельца")
        event = getattr(crash[0], "event", None)
        ok(bool(event) and str(event) in _text(r), "ответ и запись журнала связаны одним номером события")

    r = await engine.call("ошибка_в_теле", {})
    ok(r.status == "error" and r.error.code == "INTERNAL_ERROR",
       f"TypeError изнутри обработчика — падение, а не вина клиента (вышло {r.error.code if r.error else r.status})")

    r = await engine.call("падает", {"bogus": "SECRETVALUE"})
    ok(r.status == "error" and r.error.code == "VALIDATION_ERROR", "лишний параметр — VALIDATION_ERROR")
    ok("bogus" in _text(r) and "SECRETVALUE" not in _text(r), "ошибка привязки называет имя, а не значение")

    for params, field in (({"path": "p", "width": "SECRETVALUE"}, "width"), ({"width": 1}, "path"),
                          ({"path": "p", "mode": "SECRETVALUE"}, "mode")):
        r = await engine.call("по_схеме", params)
        ok(r.status == "error" and r.error.code == "VALIDATION_ERROR" and field in _text(r)
           and "SECRETVALUE" not in _text(r),
           f"отказ схемы называет параметр `{field}` и правило, а не присланное значение (ответ: {_text(r)[:80]!r})")
    r = await engine.call("по_схеме", {"path": "p", "width": 3, "mode": "b"})
    ok(r.status == "success", "параметры по схеме проходят тем же путём")
    r = await engine.call("по_схеме", {"path": "p", "width": "SECRETVALUE"})
    ok(r.error is not None and r.error.errors == [{"field": "width", "rule": "type: integer"}],
       f"ошибка ввода едет полем errors — что не так и где (вышло {r.error.errors if r.error else None})")

    r = await engine.call("по_фактам", {})
    ok(r.status == "error" and "books/a.xlsx" in r.error.message and "{" not in r.error.message
       and r.error.facts == {"path": "books/a.xlsx"},
       f"текст — шаблон реестра с фактом отказа, факт едет полем (ответ: {r.error.message!r})")
    templating = _Records()
    logging.getLogger("core.reactions.reactions").addHandler(templating)
    r = await engine.call("без_факта", {})
    logging.getLogger("core.reactions.reactions").removeHandler(templating)
    ok(r.status == "error" and r.error.code == "FILE_EXISTS" and "{path}" not in r.error.message
       and "—" in r.error.message,
       f"нет факта, которого ждёт шаблон, — отказ своим кодом с «—», а не скобки (ответ: {r.error.message!r})")
    ok(any(x.levelno == logging.ERROR and "FILE_EXISTS" in x.getMessage() and "path" in x.getMessage()
           for x in templating.records), "разрыв шаблона и места сборки — в журнале владельца с кодом и именем факта")
    r = await engine.call("скобки_примера", {})
    ok(r.error is not None and "where={столбец: значение}" in r.error.recovery.reason,
       f"скобки примера в подсказке реестра доезжают как текст (подсказка: {r.error.recovery.reason[:70]!r})")

    import string
    broken = []
    for code in engine.reactions.reactions:
        entry = engine.reactions.reactions[code]
        texts = [entry.get("message_template", ""), (entry.get("recovery") or {}).get("reason", "")]
        names = {f for t in texts for _, f, _, _ in string.Formatter().parse(t) if f}
        got = engine.reactions.get_error(code, facts=dict.fromkeys(names, "ФАКТ"))
        if any("{" + n + "}" in got.message + got.recovery.reason for n in names):
            broken.append(code)
    ok(not broken, f"каждый шаблон реестра заполняется своими фактами (не заполнились: {broken or '—'})")

    r = await engine.call("отказ", {})
    ok(r.status == "error" and r.error.code == "SHEET_NOT_FOUND",
       f"объявленный отказ предметной области доходит своим кодом (вышло {r.error.code if r.error else r.status})")

    before = len(seen.records)
    r = await engine.call("отказ_поверх_причины", {})
    noted = seen.records[before:]
    ok(r.status == "error" and r.error.code == "RECONCILE_ROLLED_BACK" and SECRET not in _text(r),
       f"объявленный отказ поверх чужой причины — свой код без её текста (ответ: {_text(r)[:90]!r})")
    ok(len(noted) == 1 and noted[0].levelno == logging.INFO and not noted[0].exc_info
       and SECRET in noted[0].getMessage(),
       "ожидаемый отказ: причина в журнале строкой INFO, без трассировки (трассировка — только у падения)")

    r = await engine.call("выход_из_корня", {})
    ok(r.status == "error" and r.error.code == "PATH_ESCAPE",
       f"выход из корня мимо ctx.safe — PATH_ESCAPE, а не падение (вышло {r.error.code if r.error else r.status})")
    log.removeHandler(seen)


asyncio.run(main())
print(f"\nграница ошибок: проверок {_checks}, провалов {len(_fails)}")
sys.exit(1 if _fails else 0)
