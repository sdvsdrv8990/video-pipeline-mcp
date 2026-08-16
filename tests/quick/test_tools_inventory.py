"""
tests/quick/test_tools_inventory.py — контракт инвентаря инструментов (сеть безопасности A2).

Standalone-прогон:  python tests/quick/test_tools_inventory.py
Обновить эталон:    python tests/quick/test_tools_inventory.py --bless

Распил монолита (A2) двигает МЕСТО определения хендлеров, но не контракт клиента:
набор имён, группы, title, annotations и input_schema обязаны остаться идентичными.
Эталон — tools_inventory.golden.json; --bless разрешён только при осознанном
изменении контракта (тогда diff эталона обязан быть виден в ревью коммита).
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import server

GOLDEN = Path(__file__).parent / "tools_inventory.golden.json"

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


def snapshot() -> dict:
    """Инвентарь живого сервера: имя → контракт, видимый клиенту в tools/list."""
    engine, _transport, _firewall = server.create_server()
    return {
        name: {
            "group": tool.group,
            "title": tool.title,
            "description": tool.description,
            "annotations": tool.annotations,
            "input_schema": tool.input_schema,
        }
        for name, tool in sorted(engine.tools.items())
    }


current = snapshot()

if "--bless" in sys.argv:
    GOLDEN.write_text(json.dumps(current, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Эталон обновлён: {GOLDEN.relative_to(ROOT)} ({len(current)} инструментов)")
    sys.exit(0)

golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

print("== Инвентарь: состав ==")
ok(len(current) == len(golden), f"инструментов {len(current)}, эталон {len(golden)}")

missing = sorted(set(golden) - set(current))
extra = sorted(set(current) - set(golden))
ok(not missing, f"ни один инструмент не пропал (пропали: {missing or '—'})")
ok(not extra, f"нет незаявленных инструментов (лишние: {extra or '—'})")

print("== Инвентарь: группы (клиент видит их в tools/list) ==")
for name in sorted(set(current) & set(golden)):
    ok(current[name]["group"] == golden[name]["group"],
       f"{name}: группа {current[name]['group']} == {golden[name]['group']}")

print("== Инвентарь: контракт (title/description/annotations/schema) ==")
for field in ("title", "description", "annotations", "input_schema"):
    diverged = [n for n in sorted(set(current) & set(golden)) if current[n][field] != golden[n][field]]
    ok(not diverged, f"{field} идентичен эталону (разошлись: {diverged or '—'})")

print("== Инвентарь: схемы годны САМИ ПО СЕБЕ, а не только совпадают с эталоном (F98) ==")
# Сверка с эталоном ловит ИЗМЕНЕНИЕ контракта, но не его качество: эталон из кривых схем
# зелёный. Инварианты ниже — про «как должно», и считаются из самих схем, не из числа.
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ANNOTATION_KEYS = {"title", "readOnlyHint", "idempotentHint", "destructiveHint", "openWorldHint"}
SCHEMA_KEYS = {"type", "properties", "required"}
TYPE_DECLARED = {"type", "enum", "anyOf", "oneOf"}

bad_names = [n for n in current if not NAME_RE.match(n)]
ok(not bad_names, f"имена инструментов пригодны для клиента (нарушают: {bad_names or '—'})")

no_desc = [n for n, t in current.items() if not (t["description"] or "").strip()]
ok(not no_desc, f"у каждого инструмента есть описание (без: {no_desc or '—'})")

alien_ann = sorted({f"{n}.{k}" for n, t in current.items()
                    for k in (t["annotations"] or {}) if k not in ANNOTATION_KEYS})
ok(not alien_ann, f"annotations только объявленные MCP-подсказки (чужие: {alien_ann or '—'})")

alien_schema = sorted({f"{n}.{k}" for n, t in current.items()
                       for k in (t["input_schema"] or {}) if k not in SCHEMA_KEYS})
ok(not alien_schema, f"в схеме нет неизвестных ключей верхнего уровня (чужие: {alien_schema or '—'})")

not_object = [n for n, t in current.items()
              if (t["input_schema"] or {}).get("type") != "object"
              or not isinstance((t["input_schema"] or {}).get("properties"), dict)]
ok(not not_object, f"схема — объект со словарём свойств (нарушают: {not_object or '—'})")

# required, ссылающийся на несуществующее свойство, — рассинхрон, который иначе пройдёт молча.
req_orphan = sorted({f"{n}.{r}" for n, t in current.items()
                     for r in ((t["input_schema"] or {}).get("required") or [])
                     if r not in ((t["input_schema"] or {}).get("properties") or {})})
ok(not req_orphan, f"required ⊆ properties (висят: {req_orphan or '—'})")

untyped = sorted({f"{n}.{p}" for n, t in current.items()
                  for p, spec in ((t["input_schema"] or {}).get("properties") or {}).items()
                  if not (isinstance(spec, dict) and TYPE_DECLARED & set(spec))})
ok(not untyped, f"у каждого свойства объявлен тип (без типа: {untyped or '—'})")

# Свойство без описания — потеря контракта именно для клиента-LLM: он не знает, что класть.
undocumented = sorted({f"{n}.{p}" for n, t in current.items()
                       for p, spec in ((t["input_schema"] or {}).get("properties") or {}).items()
                       if not (isinstance(spec, dict) and (spec.get("description") or "").strip())})
ok(not undocumented, f"у каждого свойства есть описание (без: {undocumented or '—'})")

print("== Инвентарь: манифест не отравлен (T2) ==")
# Клиент кладёт манифест в контекст модели с полным доверием, поэтому текст описаний —
# управляющий слой, а не документация: правка здесь исполняется, а не читается.
INVISIBLE_RE = re.compile("[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")
HIDDEN_RE = re.compile(r"<!--|\[//\]:\s*#\s*\(")
INSTR_RE = re.compile(r"SYSTEM:|<system>|\[INST\]|<\|im_start\|>|ignore\s+previous"
                      r"|disregard\s+(?:all|any)\s+instructions|override\s+safety|you\s+are\s+now",
                      re.IGNORECASE)
# Внешний URL/шелл опаснее всего как default: модель подставит значение не задумываясь.
OUTBOUND_RE = re.compile(r"https?://(?!(?:localhost|127\.0\.0\.1)(?:[:/?#]|$))\S+"
                         r"|\bcurl\b|\bwget\b|bash\s+-c|sh\s+-c", re.IGNORECASE)
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_SCRIPTS = ("CYRILLIC", "GREEK", "LATIN", "ARMENIAN", "HEBREW")
_script_cache: dict[str, str | None] = {}


def _script(ch: str) -> str | None:
    if ch not in _script_cache:
        name = unicodedata.name(ch, "")
        _script_cache[ch] = next((s for s in _SCRIPTS if s in name), None)
    return _script_cache[ch]


def _confusable_words(text: str) -> list[str]:
    """Смешение алфавитов ВНУТРИ слова: описания русские, документная проверка ловила бы все."""
    return [w for w in WORD_RE.findall(text)
            if len({s for s in map(_script, w) if s}) > 1]


manifest = {n: json.dumps(t, ensure_ascii=False) for n, t in current.items()}

for label, rx in (("невидимых кодпоинтов", INVISIBLE_RE), ("скрытого текста", HIDDEN_RE),
                  ("токенов перехвата", INSTR_RE), ("внешних URL/шелл-команд", OUTBOUND_RE)):
    hit = sorted(n for n, text in manifest.items() if rx.search(text))
    ok(not hit, f"в манифесте нет {label} (несут: {hit or '—'})")

confusable = sorted({f"{n}:{w}" for n, text in manifest.items() for w in _confusable_words(text)})
ok(not confusable, f"нет слов со смешением алфавитов (несут: {confusable or '—'})")

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    print("\nЕсли контракт менялся ОСОЗНАННО — обнови эталон: --bless (diff обязан быть в ревью).")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
