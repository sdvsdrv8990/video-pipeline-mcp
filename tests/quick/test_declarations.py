#!/usr/bin/env python3
"""tests/quick/test_declarations.py — дверь деклараций со схемой: проверка файла при загрузке, неизменяемый объект.

Каждая порча декларации — случай данными: что испорчено и какой путь и правило обязан назвать отказ (истина — по
построению порчи). Отказ судится через путь доставки (`Engine.refusal`): поле и правило в `errors`, пришедшее
значение — ни в ответе, ни в журнале. Правка файла посреди работы даёт новый объект целиком или отказ, а не половину.
"""
import copy
import logging
import os
import shutil
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
from core.declaration_schemas import ONLINE_CALL, PACKAGES, REACTIONS, SLOTS, STRICT, TRANSPORT, Resources
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

# Те же формы порчи на transport.yaml: пределы клиентов (что испорчено, порча, путь ключа в отказе, правило)
TRANSPORT_CASES = [
    ("бюджет ноль", spoil(("clients", "claude_ai", "answer_chars"), 0), "clients.claude_ai.answer_chars", "greater_than"),
    ("бюджет строкой", spoil(("clients", "claude_ai", "answer_chars"), MARK), "clients.claude_ai.answer_chars", "int_type"),
    ("клиентов нет", spoil(("clients",), drop=True), "clients", "missing"),
    ("клиенты пусты", spoil(("clients",), {}), "clients", "too_short"),
    ("у клиента нет бюджета", spoil(("clients", "claude_ai", "answer_chars"), drop=True), "clients.claude_ai", "budget_one"),
    ("бюджет в двух единицах", spoil(("clients", "claude_code", "answer_chars"), 1), "clients.claude_code", "budget_one"),
    ("предел текста отказа ноль", spoil(("clients", "claude_code", "error_chars"), 0), "clients.claude_code.error_chars",
     "greater_than"),
    ("путь клиента не из объявленных", spoil(("clients", "claude_ai", "reached"), "radio"), "clients.claude_ai.reached",
     "literal_error"),
    ("опечатка ключа", spoil(("clients", "claude_ai", "answer_char"), 1), "clients.claude_ai.answer_char", "extra_forbidden"),
]

# (что испорчено, тело декларации, путь чтения `need`, тип чтения): истина — поле и правило схемы двери на том же файле
NEED_CASES = [
    ("нет ключа", "a:\n  x: 1\n", ("a", "n"), int),
    ("строка вместо числа", f"a:\n  n: {MARK}\n", ("a", "n"), int),
    ("bool вместо числа", "a:\n  n: true\n", ("a", "n"), int),
    ("число вместо строки", "a:\n  n: 5\n", ("a", "n"), str),
    ("список вместо словаря", "a:\n  n: []\n", ("a", "n"), dict),
]

# (слот, путь поля-кода от корня файла, чтение): код мимо реестра в поле-коде — отказ двери с этим путём (истина — порча)
CODE_CASES = [
    (ONLINE_CALL, ("online", "call", "codes", "timeout"), "раздел"),
    (PACKAGES, ("index", "fetch", "codes", "failed"), "модель"),
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
    """Запись декларации с объявленным временем правки, без часов теста. Одна метка у двух правок — быстрая правка на ФС
    с грубым шагом времени (NTFS — 100 нс, FAT — 2 с): перечитывание обязано видеть смену содержимого, а не метки."""
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
    inline_only = {"default": "inline", "classes": {"inline": {"offload": False}}, "tools": {"x": "inline"}, "work": {}}
    write(work / "inline.yaml", inline_only, 1_000_000_000)
    check("класс без снятия с цикла и без предела — законен", door(work / "inline.yaml").model.classes["inline"].max_concurrent is None)
    from core.capacity import Capacity
    live = Capacity()
    declared = door(ROOT / "config" / "resources.yaml").model
    got = {name: live.places(name) for name in ("describe", "search", "cpu_heavy", "inline")}
    check("узел ёмкости — одна точка мест: внутренняя работа из work, класс инструмента из classes, без предела — None",
          got == {"describe": declared.work["describe"].max_concurrent, "search": declared.work["search"].max_concurrent,
                  "cpu_heavy": declared.classes["cpu_heavy"].max_concurrent, "inline": None}, got)
    given = Resources.model_validate({**inline_only, "work": {"describe": {"max_concurrent": 3}}})
    check("готовую модель узел судит по ней, а не по файлу: describe 3 из модели при 8 в декларации",
          Capacity(given).places("describe") == 3 != declared.work["describe"].max_concurrent,
          (Capacity(given).places("describe"), declared.work["describe"].max_concurrent))
    # Память места работы — снимок узла поровну на объявленные места (CP-01): снимок — шов, иначе он плывёт между чтениями
    import core.providers.hardware as hw
    real_memory = hw.process_memory
    try:
        hw.process_memory = lambda *a, **k: (12_000_000, "проба: шов снимка")
        shares = (live.memory_share("search"), live.memory_share("inline"))
        hw.process_memory = lambda *a, **k: (0, "0: ни одна ступень не ответила")
        silent = live.memory_share("search")
    finally:
        hw.process_memory = real_memory
    check("память места работы — ОЗУ процесса поровну на объявленные места поиска; класс без предела — вся; снимок "
          "молчит — 0, а не «влезет»", shares == (12_000_000 // declared.work["search"].max_concurrent, 12_000_000)
          and silent == 0, (shares, silent))

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
        # Обновлено 2026-10-18 (D26, два читателя реестра): файл — в тексте отказа и у владельца у консоли (recovery.owner);
        # рецепт ИИ-клиента адреса каталога деклараций не несёт — открыть его он не может.
        owner = engine.reactions.get_reaction(said.code)["recovery"]["owner"].format_map(said.facts)
        check("доставленный отказ называет файл декларации в тексте и у владельца, а не чужую причину",
              "bad.yaml" in said.message and "bad.yaml" in owner and "MCP_CONFIG" not in said.recovery.reason,
              (said.message[:120], owner[:120]))
    import server as S
    try:
        S._load_resources(work / "bad.yaml")
        check("сервер: битый resources.yaml — старт отказывает, а не идёт без пределов", False, "загрузилось")
    except ContractError as e:
        check("сервер: битый resources.yaml — старт отказывает, а не идёт без пределов", e.code == CODE, e.code)

    print("\n== провод: пределы клиентов — схема у одной двери (сервер, транспорт, страницы инструментов) ==")
    from core.transport.transport import client_budget, declared_transport
    from tests.harness.live_server import declarations_copy
    wire = yaml.safe_load((ROOT / "config" / TRANSPORT.file).read_text(encoding="utf-8"))
    kinds = wire["clients"].values()
    got = client_budget(declarations_copy())  # копия без машинного install.yaml — установка не объявлена: все клиенты
    check("боевой transport.yaml, установка не объявлена: действуют все клиенты, по каждой мере самый тесный, срок — 0 "
          "при клиенте без срока",
          (got.chars, got.bytes, got.call_sec, got.error_chars)
          == (min((c["answer_chars"] for c in kinds if "answer_chars" in c), default=None),
              min((c["answer_bytes"] for c in kinds if "answer_bytes" in c), default=None),
              0 if any("call_sec" not in c for c in kinds) else min(c["call_sec"] for c in kinds),
              min((c["error_chars"] for c in kinds if "error_chars" in c), default=None)), got)
    for n, (label, apply, field, rule) in enumerate(TRANSPORT_CASES):
        cfg = work / f"wire{n}"
        cfg.mkdir()
        write(cfg / TRANSPORT.file, apply(wire), 3_000_000_000)
        said, heard, cause = refusal_of(engine, lambda cfg=cfg: declared_transport(cfg))
        check(f"transport.yaml, {label}: {CODE}, {field} ({rule}), значения нет ни в ответе, ни в журнале",
              said is not None and said.code == CODE
              and any(x.get("field") == field and x.get("rule") == rule for x in said.errors or [])
              and MARK not in repr(said.model_dump()) and not any(MARK in line for line in heard),
              said and f"{said.code} · {said.errors}")

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
    check("битый YAML — текст и строка владельца называют файл декларации (D26, 2026-10-18)",
          said is not None and "broken.yaml" in said.message
          and "broken.yaml" in engine.reactions.get_reaction(said.code)["recovery"]["owner"].format_map(said.facts),
          said and said.message[:120])

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
    # Обновлено 2026-10-18 (D26, два читателя реестра): адрес файла — в тексте отказа и в строке владельца у консоли
    # (recovery.owner), а не в рецепте ИИ-клиента: открыть каталог деклараций он не может.
    owner = (engine.reactions.get_reaction(inner.code)["recovery"]["owner"].format_map(inner.facts) if inner else "")
    check("файл в подкаталоге назван путём от каталога деклараций — в тексте и у владельца, без приставки",
          named == "templates/workspace/x.tpl.yaml" and f" {named} " in f" {inner.message} " and f" {named} " in f" {owner}"
          and "MCP_CONFIG" not in inner.recovery.reason, inner and (named, inner.message[:100], owner[:100]))
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

    print("\n== поле-код: код реестра рядом с декларацией — код мимо реестра отказ двери, а не UNKNOWN_ERROR у клиента ==")
    unpassed = [f"{s.file}:{'.'.join(s.section)}" for s in SLOTS
                if refusal_of(engine, lambda s=s: read_slot(s, ROOT / "config" / s.file, "раздел"))[0] is not None]
    check(f"боевые декларации: все {len(SLOTS)} слотов проходят дверь — их коды объявлены в реестре", not unpassed, unpassed)
    registry = yaml.safe_load((ROOT / "config" / REACTIONS).read_text(encoding="utf-8"))
    for slot, field, how in CODE_CASES:
        cfg = Path(tempfile.mkdtemp(prefix="decl-codes-"))
        shutil.copy(ROOT / "config" / REACTIONS, cfg / REACTIONS)
        data = yaml.safe_load((ROOT / "config" / slot.file).read_text(encoding="utf-8"))
        named = ".".join(field)
        for code, label in ((MARK, "код мимо реестра"), (next(iter(registry)), "код из реестра")):
            (cfg / slot.file).write_text(yaml.safe_dump(spoil(field, code)(data), allow_unicode=True), encoding="utf-8")
            said, heard, _ = refusal_of(engine, lambda: read_slot(slot, cfg / slot.file, how))
            if code != MARK:
                check(f"{slot.file} ({how}): {label} в {named} — проходит", said is None, said and said.message)
                continue
            check(f"{slot.file} ({how}): {label} в {named} — {CODE}, путь от корня файла, правило registry_code",
                  said is not None and said.code == CODE and f"{named} (registry_code)" in said.message,
                  said and (said.code, said.message))
            check(f"{slot.file} ({how}): пришедшего кода нет ни в ответе, ни в журнале",
                  said is not None and MARK not in repr(said.model_dump()) and not any(MARK in x for x in heard))
    lone = Path(tempfile.mkdtemp(prefix="decl-lone-")) / PACKAGES.file
    packages = yaml.safe_load((ROOT / "config" / PACKAGES.file).read_text(encoding="utf-8"))
    lone.write_text(yaml.safe_dump(spoil(("index", "fetch", "codes", "failed"), MARK)(packages), allow_unicode=True),
                    encoding="utf-8")
    said, _, _ = refusal_of(engine, lambda: read_slot(PACKAGES, lone, "модель"))
    shutil.copy(ROOT / "config" / PACKAGES.file, lone)
    kept, _, _ = refusal_of(engine, lambda: read_slot(PACKAGES, lone, "модель"))
    check("файл вне каталога деклараций, реестра рядом нет — судит реестр каталога деклараций прогона: "
          "код мимо него отказ, боевой код проходит",
          said is not None and said.code == CODE and "index.fetch.codes.failed (registry_code)" in said.message
          and kept is None, (said and said.message, kept and kept.message))
    bare = Path(tempfile.mkdtemp(prefix="decl-bare-"))
    prior = os.environ.get("MCP_CONFIG")
    os.environ["MCP_CONFIG"] = str(bare)
    try:
        said, _, _ = refusal_of(engine, lambda: read_slot(PACKAGES, lone, "модель"))
    finally:
        if prior is None:
            del os.environ["MCP_CONFIG"]
        else:
            os.environ["MCP_CONFIG"] = prior
    # Обновлено 2026-10-18 (EB-11, прецедент D25): объявления нет на сервере — DECLARATION_MISSING, а не «шаблон клиента».
    check("реестра нет ни рядом, ни в каталоге деклараций прогона — коды сверять не с чем: DECLARATION_MISSING с именем "
          "реестра, а не пропуск сверки",
          said is not None and said.code == "DECLARATION_MISSING" and REACTIONS in said.message, said and said.message)
    check("схема без полей-кодов реестра не требует: resources.yaml в каталоге без реестра читается",
          not (work / REACTIONS).exists() and door(work / "inline.yaml").model is not None)
    cfg = Path(tempfile.mkdtemp(prefix="decl-reg-"))
    write(cfg / REACTIONS, registry, 4_000_000_000)
    shutil.copy(ROOT / "config" / PACKAGES.file, cfg / PACKAGES.file)
    held = Declared(cfg / PACKAGES.file, ContractError, PACKAGES.file, "", schema=PACKAGES.schema)
    first, _, _ = refusal_of(engine, lambda: held.model)
    code = yaml.safe_load((cfg / PACKAGES.file).read_text(encoding="utf-8"))["index"]["fetch"]["codes"]["failed"]
    write(cfg / REACTIONS, {k: v for k, v in registry.items() if k != code}, 4_000_000_000)
    said, _, _ = refusal_of(engine, lambda: held.model)
    check("из реестра убрали код, декларация та же — следующее чтение отказывает с путём, а не держит прежнюю сверку",
          first is None and said is not None and said.code == CODE and "index.fetch.codes.failed (registry_code)" in said.message,
          (first and first.message, said and said.message))

    print("\n== связь двух деклараций: один отказ двери называет оба файла и путь ключа каждой стороны, без значений ==")
    link_root = Path(tempfile.mkdtemp(prefix="decl-link-"))
    (link_root / "jobs.yaml").write_text("stale_after_sec: 7171717\n", encoding="utf-8")
    (link_root / "providers.yaml").write_text(
        "local:\n  install:\n    fetch:\n      timeout_sec: 9191919\n      speed_window_sec: 8181818\n", encoding="utf-8")
    prior = os.environ.get("MCP_CONFIG")
    os.environ["MCP_CONFIG"] = str(link_root)
    try:
        jobs = Declaration(link_root / "jobs.yaml", ContractError, "заданий", "")
        providers = Declaration(link_root / "providers.yaml", ContractError, "провайдеров", "")
        fetch = [("local", "install", "fetch", key) for key in ("timeout_sec", "speed_window_sec")]

        def refuse_link():
            raise jobs.link_refused("link_greater", "простой задания дольше тишины загрузчика", [("stale_after_sec",)],
                                    providers, fetch)
        said, heard, _ = refusal_of(engine, refuse_link)
        single, _, _ = refusal_of(engine, lambda: jobs.need("absent"))
    finally:
        if prior is None:
            del os.environ["MCP_CONFIG"]
        else:
            os.environ["MCP_CONFIG"] = prior
        shutil.rmtree(link_root, ignore_errors=True)
    sides = [(x.get("file"), x["field"], x["rule"]) for x in (said.errors if said else [])]
    check("связь: DECLARATION_INVALID, в facts.file оба файла, в errors — сторона каждого ключа",
          said is not None and said.code == CODE and said.facts.get("file") == "jobs.yaml, providers.yaml"
          and sides == [("jobs.yaml", "stale_after_sec", "link_greater"),
                        ("providers.yaml", "local.install.fetch.timeout_sec", "link_greater"),
                        ("providers.yaml", "local.install.fetch.speed_window_sec", "link_greater")],
          said and (said.facts, sides))
    linked = engine.reactions.get_reaction(CODE)["recovery"]["owner"].format_map(said.facts) if said else ""
    check("связь: текст и строка владельца называют оба файла, деталь — файл и путь ключа (D26, 2026-10-18)",
          said is not None and "jobs.yaml: stale_after_sec (link_greater)" in said.message
          and "providers.yaml: local.install.fetch.timeout_sec (link_greater)" in said.message
          and "jobs.yaml" in linked and "providers.yaml" in linked,
          said and (said.message[:200], linked[:120]))
    check("связь: значений ключей нет ни в ответе, ни в журнале",
          said is not None and not any(v in repr(said.model_dump()) or any(v in x for x in heard)
                                       for v in ("7171717", "9191919", "8181818")), said and said.facts)
    check("ошибка одного файла — прежняя форма: один файл, деталь без имени файла",
          single is not None and single.facts.get("file") == "jobs.yaml" and single.facts.get("details") == "absent (missing)"
          and all("file" not in x for x in single.errors), single and (single.facts, single.errors))

    print("\n== разрушительное: правка файла посреди работы ==")
    live = work / "live.yaml"
    write(live, real, 3_000_000_000)
    watched = door(live)
    first = watched.model
    changed = spoil(("classes", "gpu", "max_concurrent"), 2)(real)
    write(live, changed, 3_000_000_000)
    second = watched.model
    check("правка прошла проверку — новый объект целиком", second is not first and second.classes["gpu"].max_concurrent == 2)
    check("прежний объект у того, кто его держит, не изменился", first.classes["gpu"].max_concurrent == real["classes"]["gpu"]["max_concurrent"])
    write(live, spoil(("classes", "gpu", "offload"), "да")(real), 3_000_000_000)
    refused = 0
    for _ in range(2):
        try:
            watched.model
        except ContractError as e:
            refused += e.code == CODE
    check("битая правка — отказ на каждое чтение, без тихого отката к прежнему объекту", refused == 2, f"отказов {refused}/2")
    write(live, changed, 3_000_000_000)
    check("починили — снова объект", watched.model.classes["gpu"].max_concurrent == 2)
    live.write_text("classes: [не словарь\n", encoding="utf-8")
    os.utime(live, ns=(3_000_000_000, 3_000_000_000))
    try:
        watched.model
        check("битый YAML — отказ кодом", False, "загрузилось")
    except ContractError as e:
        check("битый YAML — отказ кодом двери, а не сырой ParserError", e.code == CODE, e.code)
    try:
        door(work / "нет.yaml").model
        check("нет файла — отказ кодом", False, "загрузилось")
    except ContractError as e:
        check("нет файла — DECLARATION_MISSING, а не пустой объект", e.code == "DECLARATION_MISSING", e.code)  # 2026-10-18

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


def read_slot(slot, path: Path, how: str):
    """Слот тем путём двери, который судится: раздел (`section`) или файл целиком (`Declared.model`)."""
    if how == "модель":
        return Declared(path, ContractError, slot.file, "", schema=slot.schema).model
    return Declaration(path, ContractError, slot.file, "").section(*slot.section, schema=slot.schema)


if __name__ == "__main__":
    main()
