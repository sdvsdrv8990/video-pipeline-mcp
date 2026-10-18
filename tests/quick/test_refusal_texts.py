#!/usr/bin/env python3
"""tests/quick/test_refusal_texts.py — отказ инструмента на двери: код реестра с фактами, текст и рецепт — из реестра.

Причина подаётся вызовом инструмента (вход клиента или состояние рабочей области), следствие меряется на двери движка —
тем, что видит клиент: код, факты, текст шаблона с подставленными фактами, рецепт своей причины. Случаи — данные.
Инвариант на каждом отказе набора: шаблон не ждёт факта, которого нет («—»), нет фигурных скобок, текст не написан
местом поверх реестра. Узел подстановки фактов в параметры рецепта меряется на реестре напрямую.
"""
import asyncio
import string
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.contracts import ContractError
from core.declaration import Declaration
from core.ids.taxonomy import Taxonomy
from core.providers.catalog import ModelCatalog
from core.reactions import Reactions
from core.reactions.reactions import cli_text
from core.transport.transport import answer
from tests.harness.inprocess import create_server

_checks = 0
_fails: list[str] = []


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


engine, _, _ = create_server()
registry = Reactions(ROOT / "config" / "server_reactions.yaml")


def call(tool: str, **params):
    return asyncio.run(engine.call(tool, params))


def wants(code: str) -> set[str]:
    """Какие факты ждут шаблон и рецепт кода в реестре."""
    entry = registry.reactions[code]
    texts = [entry.get("message_template", ""), (entry.get("recovery") or {}).get("reason", ""),
             *[v for v in ((entry.get("recovery") or {}).get("suggested_params") or {}).values() if isinstance(v, str)]]
    return {f for text in texts for _, f, _, _ in string.Formatter().parse(text) if f}


def listed(errors: list[dict], want: dict) -> bool:
    """В errors есть ошибка с полем want["field"] и правилом, которое начинается с want["rule"]."""
    return any(e.get("field") == want["field"] and str(e.get("rule", "")).startswith(want["rule"]) for e in errors)


call("fs_create_file", path="есть/план.md", content="# план")
# (род, инструмент, параметры, код, факты ⊂, в тексте, в рецепте, не в рецепте, errors ⊃)
CASES = [
    ("файла нет — путь от корня и куда смотреть", "fs_read_file", {"path": "нет/файла.md"}, "FILE_NOT_FOUND",
     {"path": "нет/файла.md"}, ["нет/файла.md"], ["fs_get_directory_tree"], ["Создай файл"], []),
    ("удалять нечего", "fs_delete", {"path": "нет/файла.md"}, "FILE_NOT_FOUND",
     {"path": "нет/файла.md"}, ["нет/файла.md"], ["fs_get_directory_tree"], [], []),
    ("каталога нет", "fs_get_directory_tree", {"path": "нет/каталога"}, "PATH_NOT_FOUND",
     {"path": "нет/каталога"}, ["нет/каталога"], [], [], []),
    ("удаление без подтверждения — force", "fs_delete", {"path": "есть/план.md"}, "CONFIRM_REQUIRED",
     {"param": "force", "operation": "удаление", "target": "есть/план.md", "kind": "файл", "count": 1},
     ["удаление", "есть/план.md"], ["force=true"], ["confirm"], []),
    ("установка без подтверждения — confirm, не удаление", "media_model_install",
     {"model_id": "org/модель", "kind": "image"}, "CONFIRM_REQUIRED",
     {"param": "confirm", "operation": "установка модели", "target": "org/модель"},
     ["установка модели", "org/модель"], ["confirm=true"], ["force", "fs_delete"], []),
    ("таблицы нет — имя в тексте", "json_read_snapshot", {"table": "нет_таблицы"}, "TABLE_NOT_FOUND",
     {"table": "нет_таблицы"}, ["нет_таблицы"], [], [], []),
    ("шаблона нет — имя клиента, без адреса конфигурации", "fs_create_project_structure", {"template": "нет_шаблона"},
     "TEMPLATE_NOT_FOUND", {"template": "нет_шаблона"}, ["нет_шаблона"], ["Проверь имя"], ["config/", "владельцу"], []),
    ("фрагментов нет", "fs_create_project_structure", {"fragments": []}, "NO_FRAGMENTS", {}, [], [], [], []),
    ("режим вне списка — поле и допустимое", "structure_create", {"type": "niche", "name": "x", "mode": "нет_режима"},
     "VALIDATION_ERROR", {}, ["errors"], [], [], [{"field": "mode", "rule": "enum"}]),
    ("действие раннера вне списка", "media_runner", {"action": "взлетай"}, "VALIDATION_ERROR", {}, [], [], [],
     [{"field": "action", "rule": "enum"}]),
    ("запрос не YAML — строка и столбец", "search_tables", {"yaml_query": "a: [1, 2"}, "VALIDATION_ERROR", {}, [], [],
     [], [{"field": "yaml_query", "rule": "yaml"}]),
    ("ни запроса, ни словаря", "search_tables", {}, "VALIDATION_ERROR", {}, [], [], [],
     [{"field": "yaml_query", "rule": "required"}]),
]

print("== 1. Причина у места — следствие на двери: код, факты, текст и рецепт своей причины ==")
for kind, tool, params, code, facts, said, recipe, not_recipe, errors in CASES:
    r = call(tool, **params)
    e = r.error
    if r.status != "error" or e is None:
        check(f"{kind}: отказ {code}", False, (r.status, r.data))
        continue
    reason = e.recovery.reason if e.recovery else ""
    template = registry.get_error(code, facts=e.facts).message
    check(f"{kind}: {code}, факты {sorted(facts)}",
          e.code == code and all(e.facts.get(k) == v for k, v in facts.items()), (e.code, e.facts))
    check(f"{kind}: текст — шаблон реестра с фактами, текста места нет; названо {said}",
          e.message == template and all(s in e.message for s in said), (e.message, template))
    check(f"{kind}: рецепт своей причины", all(s in reason for s in recipe) and not any(s in reason for s in not_recipe),
          reason)
    check(f"{kind}: errors называют поле и правило", all(listed(e.errors, want) for want in errors), e.errors)
    check(f"{kind}: шаблону и рецепту хватило фактов", wants(code) <= set(e.facts), (sorted(wants(code)), e.facts))

print("== 2. Узел подстановки: факты доходят и до параметров рецепта ==")
chain = registry.get_error("CHAIN_UNRESOLVED", facts={"paths": "a/b", "path": "a/b/c"})
check("CHAIN_UNRESOLVED: suggested_params.path — факт, а не «{path}»",
      chain.recovery.suggested_params == {"path": "a/b/c"}, chain.recovery.suggested_params)
dup = registry.get_error("DUPLICATE_PATH", raw_message="занят", facts={"path": "a/b", "id": "NET_1"})
check("DUPLICATE_PATH: параметр рецепта — факт и при тексте места", dup.recovery.suggested_params == {"path": "a/b"},
      dup.recovery.suggested_params)

print("== 3. Одна причина — один код (EB-11): нет объявления на сервере ≠ клиент назвал неизвестное ==")
# Через дверь тип узла отбивает enum схемы инструмента раньше обработчика; таксономию зовут и внутренние места — её узел.
try:
    Taxonomy(ROOT / "config" / "templates" / "workspace").prefix("нет_типа")
    typed = None
except ContractError as e:
    typed = engine.refusal(e).error
check("неизвестный тип узла — TEMPLATE_NOT_FOUND: имя клиента в тексте, известные типы — фактом",
      typed is not None and typed.code == "TEMPLATE_NOT_FOUND" and "нет_типа" in typed.message
      and isinstance(typed.facts.get("known"), list) and "niche" in typed.facts["known"]
      and "владельцу" not in typed.recovery.reason, typed and (typed.code, typed.message, typed.facts))
try:
    Declaration(Path(tempfile.mkdtemp()) / "engines.yaml", ContractError, "движков сервера", "").data
    missing = None
except ContractError as e:
    missing = engine.refusal(e).error
check("нет объявления на сервере — DECLARATION_MISSING с предметом и файлом, рецепт — владельцу",
      missing is not None and missing.code == "DECLARATION_MISSING"
      and missing.message == "Нет объявления движков сервера: «engines.yaml»." and "владельцу" in missing.recovery.reason,
      missing and (missing.code, missing.message))
try:
    ModelCatalog(ROOT / "config" / "providers.yaml", Path(tempfile.mkdtemp())).source("нет_вида")
    kind = None
except ContractError as e:
    kind = e
check("вид модели не из объявленных — ошибка ввода: поле kind и объявленные виды, а не «шаблона нет»",
      kind is not None and kind.code == "VALIDATION_ERROR" and kind.errors and kind.errors[0]["field"] == "kind"
      and kind.errors[0]["rule"] == "enum" and kind.errors[0]["expected"], kind and (kind.code, kind.errors))

print("== 4. Два читателя реестра (D26): строка владельца — только у консоли, в провод клиенту не уходит ==")
REGISTRY = ROOT / "config" / "server_reactions.yaml"


def strings(value) -> list[str]:
    """Все строки ответа провода любой вложенности: текст, факты, structuredContent."""
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return [value] if isinstance(value, str) else []


leaked, unprinted, owners = [], [], 0
for code, entry in registry.reactions.items():
    owner = (entry.get("recovery") or {}).get("owner") or ""
    facts = {f: f"факт_{f}" for f in wants(code) | {f for _, f, _, _ in string.Formatter().parse(owner) if f}}
    refusal = ContractError(code, facts=facts)
    wire = strings(answer(engine.refusal(refusal)))
    lines = cli_text(refusal, REGISTRY).splitlines()
    if not owner:
        if len(lines) != 2:
            unprinted.append((code, lines))
        continue
    owners += 1
    filled = owner.format_map(facts)
    piece = max(filled.replace("\\", '"').split('"'), key=len)         # кусок без кавычек: провод экранирует их в JSON
    if any(piece in s for s in wire):
        leaked.append(code)
    if len(lines) != 3 or lines[2].strip() != filled:
        unprinted.append((code, lines[2:] if len(lines) > 2 else lines))
check(f"строка владельца ни в одном ответе провода — {owners} кодов с owner", owners > 0 and not leaked, leaked)
check("консоль печатает строку владельца третьей строкой с фактами отказа, у кодов без неё — две строки",
      not unprinted, unprinted[:3])

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
