"""
tests/quick/test_what_if.py — отчёт «ожидали против получили» проверяется без живого прогона.

Standalone-прогон:  python tests/quick/test_what_if.py
Вердикты подставляются словарями, поэтому набор быстрый: сравнение двух деревьев стоит минуты и
живёт в самом стороже. Проверяется, что заявленное отделено от незаявленного, а ИСЧЕЗНУВШАЯ
проверка не выдаётся за «цвет не сменился».
"""
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "guards"))

import what_if  # noqa: E402

_checks = 0
_fails = []


def ok(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def run(expect, before, after) -> tuple[str, int]:
    intent = {"intent": "проба", "where": "core/x.py", "why": "проба", "expect": expect}
    out = io.StringIO()
    with redirect_stdout(out):
        code = what_if.report(intent, before, after)
    return out.getvalue(), code


def main() -> int:
    print("§1 заявленное сбылось")
    text, code = run([{"scenario": "alpha", "becomes": "red"}],
                     {"alpha · 1. шаг": True}, {"alpha · 1. шаг": False})
    ok("✓ alpha → red" in text, "смена цвета зачтена как исполнение намерения")
    ok(code == 0, "чистое намерение даёт exit 0")

    print("§2 изменилось незаявленное")
    text, code = run([{"scenario": "alpha", "becomes": "red"}],
                     {"alpha · 1. шаг": True, "beta · 1. шаг": True},
                     {"alpha · 1. шаг": False, "beta · 1. шаг": False})
    ok("beta" in text and "намерение об этом не говорило" in text, "соседнее падение названо риском")
    ok(code == 1, "скрытый риск не пропускается молча")

    print("§3 проверка ИСЧЕЗЛА — это не «без изменений»")
    text, code = run([{"scenario": "reaction_to_client", "becomes": "red"}],
                     {"reaction_to_client: code доезжает": True}, {"другая проверка": True})
    ok("ИСЧЕЗЛИ" in text and "улики нет" in text, "исчезновение названо отсутствием улики")
    ok("получили без изменений" not in text, "исчезнувшее НЕ выдаётся за «цвет не сменился»")
    ok(code == 1, "исчезнувшая улика не считается успехом")

    print("§4 имя маршрута и имя сценария читаются одинаково")
    ok(what_if._named("reaction_to_client: рубеж core/x.py") == "reaction_to_client",
       "у маршрута имя берётся до рубежа")
    ok(what_if._named("alpha · 1. шаг → успех") == "alpha", "у сценария имя берётся до шага")

    print("§5 обе карты объявлены источником вердиктов")
    ok(any("routes" in s for s in what_if.SUITES) and any("scenarios" in s for s in what_if.SUITES),
       "сравниваются и сценарии, и маршруты потоков данных")

    print(f"\nПроверок: {_checks}, провалов: {len(_fails)}")
    for fail in _fails:
        print(f"  - {fail}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
