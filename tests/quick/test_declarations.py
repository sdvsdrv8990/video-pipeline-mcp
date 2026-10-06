#!/usr/bin/env python3
"""tests/quick/test_declarations.py — дверь деклараций со схемой: проверка файла при загрузке, неизменяемый объект.

Каждая порча декларации — случай данными: что испорчено и какой путь и правило обязан назвать отказ (истина — по
построению порчи). Отказ судится через путь доставки (`Engine.refusal`): поле и правило в `errors`, пришедшее
значение — ни в ответе, ни в журнале. Правка файла посреди работы даёт новый объект целиком или отказ, а не половину.
"""
import copy
import logging
import os
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError

from core.contracts import ContractError
from core.declaration import Declared
from core.declaration_schemas import Resources
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
        heard, root = Heard(), logging.getLogger()
        root.addHandler(heard)
        try:
            door(path).model
            check(label, False, "загрузилось")
            continue
        except ContractError as e:
            said = engine.refusal(e).error
            cause = e.__cause__
        finally:
            root.removeHandler(heard)
        named = [x for x in said.errors if x.get("field") == field and x.get("rule") == rule]
        check(f"{label}: {CODE}, {field} ({rule})", said.code == CODE and bool(named),
              f"{said.code} · {said.errors}")
        delivered = repr(said.model_dump())
        check(f"{label}: пришедшего значения нет ни в ответе, ни в журнале, ни в причине",
              MARK not in delivered and not any(MARK in line for line in heard.lines) and cause is None,
              f"ответ:{MARK in delivered} журнал:{sum(MARK in x for x in heard.lines)} причина:{cause!r}")
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
        check("битый YAML — отказ кодом, а не сырой ParserError (до DC-09 — прежний SCHEMA_INVALID двери .data)",
              e.code == "SCHEMA_INVALID", e.code)
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


if __name__ == "__main__":
    main()
