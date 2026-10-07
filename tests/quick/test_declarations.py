#!/usr/bin/env python3
"""tests/quick/test_declarations.py — дверь деклараций со схемой: проверка файла при загрузке, неизменяемый объект.

Каждая порча декларации — случай данными: что испорчено и какой путь и правило обязан назвать отказ (истина — по
построению порчи). Отказ судится через путь доставки (`Engine.refusal`): поле и правило в `errors`, пришедшее
значение — ни в ответе, ни в журнале. Правка файла посреди работы даёт новый объект целиком или отказ, а не половину.
"""
import copy
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pydantic import BaseModel, ValidationError, create_model

from core.contracts import ContractError
from core.declaration import Declaration, Declared
from core.declaration_schemas import STRICT, Resources
from core.engine import Engine
from core.reactions import Reactions

CODE = "DECLARATION_INVALID"
MARK = "МЕТКА-7f3c-не-для-ответа"  # подложенное значение: его не должно быть ни в ответе, ни в журнале

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


def spoil(path: tuple, value=None, drop: bool = False):
    """Порча боевой декларации: значение по пути или удаление ключа."""
    def apply(data: dict) -> dict:
        data = copy.deepcopy(data)
        node = data
        for key in path[:-1]:
            node = node[key]
        if drop:
            del node[path[-1]]
        else:
            node[path[-1]] = value
        return data
    return apply


# (что испорчено, порча, путь ключа в отказе, правило)
CASES = [
    ("строка вместо числа", spoil(("classes", "gpu", "max_concurrent"), MARK), "classes.gpu.max_concurrent", "int_type"),
    ("bool вместо числа", spoil(("classes", "gpu", "max_concurrent"), True), "classes.gpu.max_concurrent", "int_type"),
    ("лишний ключ — опечатка", spoil(("classes", "gpu", "max_concurent"), 1), "classes.gpu.max_concurent", "extra_forbidden"),
    ("нет обязательного ключа", spoil(("default",), drop=True), "default", "missing"),
    ("класс инструмента не объявлен", spoil(("tools", "media_generate"), "gpuu"), "tools.media_generate", "class_undeclared"),
    ("default не объявлен", spoil(("default",), "inlien"), "default", "class_undeclared"),
    ("снят с цикла без предела", spoil(("classes", "gpu"), {"offload": True}), "classes.gpu.max_concurrent", "limit_missing"),
    ("предел без срока повтора", spoil(("classes", "gpu"), {"offload": True, "max_concurrent": 1}),
     "classes.gpu.retry_after_sec", "retry_missing"),
    ("предел ноль", spoil(("classes", "gpu", "max_concurrent"), 0), "classes.gpu.max_concurrent", "greater_than"),
]

# (что испорчено, тело декларации, путь чтения `need`, тип чтения): истина — поле и правило схемы двери на том же файле
NEED_CASES = [
    ("нет ключа", "a:\n  x: 1\n", ("a", "n"), int),
    ("строка вместо числа", f"a:\n  n: {MARK}\n", ("a", "n"), int),
    ("bool вместо числа", "a:\n  n: true\n", ("a", "n"), int),
    ("число вместо строки", "a:\n  n: 5\n", ("a", "n"), str),
    ("список вместо словаря", "a:\n  n: []\n", ("a", "n"), dict),
]


class Heard(logging.Handler):
    """Всё, что ушло в журнал за время отказа: значение декларации туда не попадает."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def door(path: Path) -> Declared[Resources]:
    return Declared(path, ContractError, "ресурсов", "Заведи config/resources.yaml.", schema=Resources)


def write(path: Path, data: dict, stamp: int) -> None:
    """Запись декларации с объявленным временем правки: перечитывание судится по mtime, без часов теста."""
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    os.utime(path, ns=(stamp, stamp))


def main():
    real_file = ROOT / "config" / "resources.yaml"
    real = yaml.safe_load(real_file.read_text(encoding="utf-8"))
    engine = Engine(reactions=Reactions(ROOT / "config" / "server_reactions.yaml"))
    work = Path(tempfile.mkdtemp(prefix="decl-"))

    print("== законное: боевая декларация — типизированный неизменяемый объект ==")
    model = door(real_file).model
    check("боевой resources.yaml проходит схему целиком", isinstance(model, Resources))
    check("классы и инструменты — как в файле", (len(model.classes), len(model.tools))
          == (len(real["classes"]), len(real["tools"])), f"{len(model.classes)}/{len(model.tools)}")
    check("поле — типизированное значение, а не строка", model.classes["gpu"].max_concurrent == real["classes"]["gpu"]["max_concurrent"])
    try:
        model.classes["gpu"] = model.classes["inline"]  # type: ignore[index]
        check("отображение классов только для чтения", False, "запись прошла")
    except TypeError:
        check("отображение классов только для чтения", True)
    try:
        model.default = "gpu"  # type: ignore[misc]
        check("объект неизменяем", False, "присваивание прошло")
    except ValidationError as e:
        check("объект неизменяем", e.errors()[0]["type"] == "frozen_instance", e.errors()[0]["type"])
    inline_only = {"default": "inline", "classes": {"inline": {"offload": False}}, "tools": {"x": "inline"}}
    write(work / "inline.yaml", inline_only, 1_000_000_000)
    check("класс без снятия с цикла и без предела — законен", door(work / "inline.yaml").model.classes["inline"].max_concurrent is None)

    print("\n== больное: порча — отказ при загрузке с путём и правилом, через путь доставки ==")
    for label, apply, field, rule in CASES:
        path = work / "bad.yaml"
        write(path, apply(real), 2_000_000_000)
        said, heard, cause = refusal_of(engine, lambda: door(path).model)
        if said is None:
            check(label, False, "загрузилось")
            continue
        named = [x for x in said.errors if x.get("field") == field and x.get("rule") == rule]
        check(f"{label}: {CODE}, {field} ({rule}) — в errors и в доставленном тексте",
              said.code == CODE and bool(named) and f"{field} ({rule})" in said.message,
              f"{said.code} · {said.message} · {said.errors}")
        delivered = repr(said.model_dump())
        check(f"{label}: пришедшего значения нет ни в ответе, ни в журнале, ни в причине",
              MARK not in delivered and not any(MARK in line for line in heard) and cause is None,
              f"ответ:{MARK in delivered} журнал:{sum(MARK in x for x in heard)} причина:{cause!r}")
    write(work / "bad.yaml", spoil(("tools", "media_generate"), "gpuu")(real), 2_100_000_000)
    try:
        door(work / "bad.yaml").model
    except ContractError as e:
        said = engine.refusal(e).error
        check("подсказка доставленного отказа называет файл декларации, а не чужую причину",
              "bad.yaml" in said.recovery.reason, said.recovery.reason[:120])
    import server as S
    try:
        S._load_resources(work / "bad.yaml")
        check("сервер: битый resources.yaml — старт отказывает, а не идёт без пределов", False, "загрузилось")
    except ContractError as e:
        check("сервер: битый resources.yaml — старт отказывает, а не идёт без пределов", e.code == CODE, e.code)

    print("\n== одна точка: need и битый YAML — тот же отказ двери тем же словом, что у схемы ==")
    for n, (label, body, path, kind) in enumerate(NEED_CASES):
        probe = work / f"need{n}.yaml"
        probe.write_text(body, encoding="utf-8")
        said, heard, cause = refusal_of(engine, lambda: Declaration(probe, ContractError, "пробы", "").need(*path, kind=kind))
        word, _, _ = refusal_of(engine, lambda: Declared(probe, ContractError, "пробы", "", schema=schema_of(path, kind)).model)
        pairs = [(x["field"], x["rule"]) for x in said.errors] if said else []
        truth = [(x["field"], x["rule"]) for x in word.errors] if word else []
        check(f"need: {label} — {CODE}, то же поле и правило, что у схемы двери, и они в доставленном тексте",
              said is not None and said.code == CODE and len(pairs) == 1 and pairs[0] in truth
              and f"{pairs[0][0]} ({pairs[0][1]})" in said.message,
              f"need {said and (said.code, said.message)} {pairs} · схема {truth}")
        check(f"need: {label} — значения нет ни в ответе, ни в журнале, ни в причине",
              said is not None and MARK not in repr(said.model_dump()) and not any(MARK in x for x in heard)
              and cause is None, f"причина:{cause!r}")
    broken = work / "broken.yaml"
    broken.write_text(f"a: [1\nb: {MARK}\n", encoding="utf-8")
    said, heard, cause = refusal_of(engine, lambda: Declaration(broken, ContractError, "пробы", "").data)
    where = said.errors[0]["field"].split(":") if said and said.errors else []
    check(f"битый YAML — {CODE}, правило yaml_syntax, место «строка:столбец»",
          said is not None and said.code == CODE and [x["rule"] for x in said.errors] == ["yaml_syntax"]
          and len(where) == 2 and all(part.isdigit() for part in where), said)
    check("битый YAML — строки файла нет ни в ответе, ни в журнале, ни в причине",
          said is not None and MARK not in repr(said.model_dump()) and not any(MARK in x for x in heard)
          and cause is None, f"причина:{cause!r}")
    check("битый YAML — подсказка называет файл декларации", said is not None and "broken.yaml" in said.recovery.reason,
          said and said.recovery.reason[:120])

    print("\n== каталог деклараций: одно решение «где лежат», подсказка называет файл от него ==")
    decl_root = Path(tempfile.mkdtemp(prefix="decl-root-"))
    (decl_root / "install.yaml").write_text("mcp: false\ntunnel: none\n", encoding="utf-8")
    nested = decl_root / "templates" / "workspace" / "x.tpl.yaml"
    nested.parent.mkdir(parents=True)
    nested.write_text("a: [1\n", encoding="utf-8")
    outside = work / "вне.yaml"
    outside.write_text("a: [1\n", encoding="utf-8")
    prior = os.environ.get("MCP_CONFIG")
    os.environ["MCP_CONFIG"] = str(decl_root)
    try:
        inner, _, _ = refusal_of(engine, lambda: Declaration(nested, ContractError, "пробы", "").data)
        outer, _, _ = refusal_of(engine, lambda: Declaration(outside, ContractError, "пробы", "").data)
    finally:
        if prior is None:
            del os.environ["MCP_CONFIG"]
        else:
            os.environ["MCP_CONFIG"] = prior
    named = inner.facts.get("file") if inner else None
    check("файл в подкаталоге назван путём от каталога деклараций, подсказка — им же, без приставки",
          named == "templates/workspace/x.tpl.yaml" and f" {named} " in f" {inner.recovery.reason}",
          inner and (named, inner.recovery.reason[:100]))
    shown = repr(outer.model_dump()) if outer else ""
    check("файл вне каталога деклараций — только имя, пути хоста в ответе нет",
          outer is not None and outer.facts.get("file") == "вне.yaml" and str(work) not in shown
          and str(Path.home()) not in shown, outer and outer.facts)
    seen = subprocess.run(
        [sys.executable, "-B", "-c", "import server; from core import deploy; from core.declaration import declarations_root;"
                                     "print(server.CONFIG_PATH == declarations_root() == deploy._CONFIG.parent, deploy.install())"],
        cwd=ROOT, env={**os.environ, "MCP_CONFIG": str(decl_root)}, capture_output=True, text=True)
    check("MCP_CONFIG задан — сервер, установщик и дверь видят один каталог, установщик читает его install.yaml",
          seen.stdout.strip() == "True (False, 'none')", seen.stdout.strip() or seen.stderr.strip()[-200:])

    print("\n== разрушительное: правка файла посреди работы ==")
    live = work / "live.yaml"
    write(live, real, 3_000_000_000)
    watched = door(live)
    first = watched.model
    changed = spoil(("classes", "gpu", "max_concurrent"), 2)(real)
    write(live, changed, 3_000_000_001)
    second = watched.model
    check("правка прошла проверку — новый объект целиком", second is not first and second.classes["gpu"].max_concurrent == 2)
    check("прежний объект у того, кто его держит, не изменился", first.classes["gpu"].max_concurrent == real["classes"]["gpu"]["max_concurrent"])
    write(live, spoil(("classes", "gpu", "offload"), "да")(real), 3_000_000_002)
    refused = 0
    for _ in range(2):
        try:
            watched.model
        except ContractError as e:
            refused += e.code == CODE
    check("битая правка — отказ на каждое чтение, без тихого отката к прежнему объекту", refused == 2, f"отказов {refused}/2")
    write(live, changed, 3_000_000_003)
    check("починили — снова объект", watched.model.classes["gpu"].max_concurrent == 2)
    live.write_text("classes: [не словарь\n", encoding="utf-8")
    os.utime(live, ns=(3_000_000_004, 3_000_000_004))
    try:
        watched.model
        check("битый YAML — отказ кодом", False, "загрузилось")
    except ContractError as e:
        check("битый YAML — отказ кодом двери, а не сырой ParserError", e.code == CODE, e.code)
    try:
        door(work / "нет.yaml").model
        check("нет файла — отказ кодом", False, "загрузилось")
    except ContractError as e:
        check("нет файла — TEMPLATE_NOT_FOUND, а не пустой объект", e.code == "TEMPLATE_NOT_FOUND", e.code)

    print(f"\n{'=' * 50}")
    print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
    if _fails:
        print("ПРОВАЛЫ:")
        for f in _fails:
            print(f"  - {f}")
        sys.exit(1)
    print("ВСЁ ЗЕЛЁНОЕ ✅")


def refusal_of(engine: Engine, call) -> tuple:
    """Отказ, каким его доставит Engine, всё, что ушло в журнал за время вызова, и причина; без отказа — None."""
    heard, root = Heard(), logging.getLogger()
    root.addHandler(heard)
    try:
        call()
        return None, heard.lines, None
    except ContractError as e:
        return engine.refusal(e).error, heard.lines, e.__cause__
    finally:
        root.removeHandler(heard)


def schema_of(path: tuple, kind: type) -> type[BaseModel]:
    """Схема двери, которая читает то же значение тем же типом: её поле и правило — истина для `need`."""
    inner = create_model("Inner", __config__=STRICT, **{path[1]: (kind, ...)})
    return create_model("Probe", __config__=STRICT, **{path[0]: (inner, ...)})


if __name__ == "__main__":
    main()
