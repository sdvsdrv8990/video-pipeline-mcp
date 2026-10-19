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

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.contracts import ContractError
from core.declaration import Declaration
from core.ids.taxonomy import Taxonomy
from core.providers.adapters import AdapterRegistry, MediaRequest
from core.providers.catalog import ModelCatalog, OnlineCatalog, provider_refusal
from core.providers.img.diffusers_local import DiffusersLocalIMG
from core.providers.task_cycle import TaskCycle
from core.reactions import Reactions
from core.reactions.reactions import cli_text
from core.runner.supervisor import RunnerSupervisor
from core.transport.transport import answer
from tests.harness.inprocess import create_server
from tests.harness.live_server import declarations_copy

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
call("excel_create_workbook", path="книга.xlsx", sheet="Лист1")
call("excel_add_column", path="книга.xlsx", sheet="Лист1", column="A")
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
    # Обновлено 2026-10-18 (дыра R3 №1, слово 6a): параметр template снят — имя от ИИ уходило в путь config; мёртв с
    # рождения (ни один объявленный шаблон не несёт fragments). Прогноз: дверь движка называет поле, файл не читается.
    ("шаблон — не параметр инструмента: поле названо дверью", "fs_create_project_structure", {"template": "../чужой"},
     "VALIDATION_ERROR", {}, ["errors"], [], [], [{"field": "template", "rule": "unexpected"}]),
    ("фрагментов нет", "fs_create_project_structure", {"fragments": []}, "NO_FRAGMENTS", {}, [], [], [], []),
    ("режим вне списка — поле и допустимое", "structure_create", {"type": "niche", "name": "x", "mode": "нет_режима"},
     "VALIDATION_ERROR", {}, ["errors"], [], [], [{"field": "mode", "rule": "enum"}]),
    ("действие раннера вне списка", "media_runner", {"action": "взлетай"}, "VALIDATION_ERROR", {}, [], [], [],
     [{"field": "action", "rule": "enum"}]),
    ("запрос не YAML — строка и столбец", "search_tables", {"yaml_query": "a: [1, 2"}, "VALIDATION_ERROR", {}, [], [],
     [], [{"field": "yaml_query", "rule": "yaml"}]),
    ("ни запроса, ни словаря", "search_tables", {}, "VALIDATION_ERROR", {}, [], [], [],
     [{"field": "yaml_query", "rule": "required"}]),
    # Семейство «таблицы и книги»: отказ рождается узлом хранилища (_book, _sheet, _vacant, _column), имена — фактами.
    ("книги нет — путь клиента", "excel_add_sheet", {"path": "нет.xlsx", "sheet": "X"}, "WORKBOOK_NOT_FOUND",
     {"path": "нет.xlsx"}, ["нет.xlsx"], ["excel_create_workbook"], [], []),
    ("листа нет — имя и листы, что есть", "excel_delete_sheet", {"path": "книга.xlsx", "sheet": "Нет"}, "SHEET_NOT_FOUND",
     {"sheet": "Нет", "known": "Лист1"}, ["Нет", "Лист1"], [], [], []),
    ("лист уже есть", "excel_add_sheet", {"path": "книга.xlsx", "sheet": "Лист1"}, "SHEET_EXISTS",
     {"sheet": "Лист1"}, ["Лист1"], [], [], []),
    ("переименование на занятое имя", "excel_rename_sheet", {"path": "книга.xlsx", "sheet": "Лист1", "new_name": "Лист1"},
     "SHEET_EXISTS", {"sheet": "Лист1"}, ["Лист1"], [], [], []),
    ("копия на занятое имя", "excel_copy_sheet", {"path": "книга.xlsx", "sheet": "Лист1", "new_name": "Лист1"},
     "SHEET_EXISTS", {"sheet": "Лист1"}, ["Лист1"], [], [], []),
    # Опись №6 (ложная вина): адрес и цвет от ИИ — ошибка вызова с полем и правилом, прежде INTERNAL_ERROR human_required.
    ("ячейка вне A1", "excel_insert_formula", {"path": "книга.xlsx", "sheet": "Лист1", "cell": "A0", "formula": "=1"},
     "VALIDATION_ERROR", {}, ["errors"], [], [], [{"field": "cell", "rule": "a1_cell"}]),
    ("диапазон оборван", "excel_apply_formatting", {"path": "книга.xlsx", "sheet": "Лист1", "target": "A1:", "bold": True},
     "VALIDATION_ERROR", {}, ["errors"], [], [], [{"field": "target", "rule": "a1_range"}]),
    ("цвет не aRGB", "excel_apply_formatting", {"path": "книга.xlsx", "sheet": "Лист1", "target": "A1", "fill": "#FF0000"},
     "VALIDATION_ERROR", {}, ["errors"], [], [], [{"field": "fill", "rule": "argb_hex"}]),
    ("диапазон чтения не A1", "excel_read_range", {"path": "книга.xlsx", "sheet": "Лист1", "cell_range": "Б1"},
     "VALIDATION_ERROR", {}, ["errors"], [], [], [{"field": "cell_range", "rule": "a1_range"}]),
    # Опись №6, поиск: regex, дата, YAML и типы полей запроса — до обхода, ошибкой вызова с полем.
    ("regex имени не разбирается", "fs_smart_search", {"directory": "есть", "name_pattern": "[незакрыт"},
     "VALIDATION_ERROR", {}, ["errors"], [], [], [{"field": "name_pattern", "rule": "regex"}]),
    ("regex ID не разбирается", "fs_smart_search", {"directory": "есть", "id_pattern": "(?P<"},
     "VALIDATION_ERROR", {}, ["errors"], [], [], [{"field": "id_pattern", "rule": "regex"}]),
    ("дата не ISO 8601", "fs_smart_search", {"directory": "есть", "modified_after": "вчера"},
     "VALIDATION_ERROR", {}, ["errors"], [], [], [{"field": "modified_after", "rule": "iso8601"}]),
    ("YAML запроса битый", "fs_search_yaml", {"yaml_query": "root: [незакрыт"},
     "VALIDATION_ERROR", {}, ["errors"], [], [], [{"field": "yaml_query", "rule": "yaml"}]),
    ("YAML запроса не словарь", "fs_search_yaml", {"yaml_query": "- список"},
     "VALIDATION_ERROR", {}, ["errors"], [], [], [{"field": "yaml_query", "rule": "object"}]),
    ("поле запроса не того типа", "fs_search_yaml", {"yaml_query": "limit: много"},
     "VALIDATION_ERROR", {}, ["errors"], [], [], [{"field": "limit", "rule": "int_parsing"}]),
    ("чтение плана не словарь", "search_tables", {"query_dict": {"reads": ["не словарь"]}},
     "VALIDATION_ERROR", {}, ["errors"], [], [], [{"field": "reads.0", "rule": "dataclass_type"}]),
    ("столбца нет — имя и лист", "excel_delete_column", {"path": "книга.xlsx", "sheet": "Лист1", "column": "Нет"},
     "COLUMN_NOT_FOUND", {"column": "Нет", "sheet": "Лист1"}, ["«Нет»", "«Лист1»"], [], [], []),
    ("столбец уже есть", "excel_add_column", {"path": "книга.xlsx", "sheet": "Лист1", "column": "A"}, "COLUMN_EXISTS",
     {"column": "A", "sheet": "Лист1"}, ["«A»", "«Лист1»"], [], [], []),
    ("порядок листов — не все листы книги", "excel_reorder_sheets", {"path": "книга.xlsx", "order": ["Нет"]},
     "VALIDATION_ERROR", {}, [], [], [], [{"field": "order", "rule": "all_sheets"}]),
    # Предок без регистрации: рецепт — по факту диска у предка. Нет на диске — создать сущностью; есть — усыновить.
    ("ID в папке, которой нет: создать, не усыновлять", "fs_create_file",
     {"path": "нет/папки/x.md", "assign_id": True}, "CHAIN_NOT_CREATED", {"paths": "нет, нет/папки", "path": "нет/папки"},
     ["нет/папки"], ["Ничего не записано", "structure_create"], ["adopt"], []),
    ("ID в папке на диске без записи: усыновить", "fs_create_file", {"path": "есть/x.md", "assign_id": True},
     "CHAIN_UNRESOLVED", {"paths": "есть", "path": "есть"}, ["есть"], ["adopt=true"], ["structure_create"], []),
    ("предок на диске и под ним нового нет: сперва усыновить стоящий", "fs_create_file",
     {"path": "есть/новая/x.md", "assign_id": True}, "CHAIN_UNRESOLVED", {"paths": "есть", "path": "есть/новая"},
     ["есть"], ["adopt=true"], ["structure_create"], []),
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
    Declaration(Path(tempfile.mkdtemp()) / "engines.yaml", ContractError, "движков сервера").data
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

print("== 5. Провайдеры (П1 плана текста места): у причины свой код, текст — реестра, устройство — в журнал ==")
CFG = ROOT / "config" / "providers.yaml"
SPOILED = declarations_copy({"providers.yaml": {
    "adapters.by_provider.Сломан": "нет_модуля:Класс", "adapters.by_provider.Наружу": "os:system",
    "local.runner.provider": "Нет_раннера", "local.hand_placed": "maybe", "local.sources.tts.kind": "нет_вида"}}) \
    / "providers.yaml"
REFUSING = declarations_copy({"providers.yaml": {"local.hand_placed": "refuse"}}) / "providers.yaml"
project = Path(tempfile.mkdtemp(prefix="refusal-project-"))
(project / "vendor" / "models").mkdir(parents=True)
(project / "vendor" / "models" / "руками.onnx").write_bytes(b"r")
BIG = project / "big.png"
BIG.write_bytes(b"\x89PNG" + b"0" * 299_996)                 # 0.3 МБ — больше предела встроенной отправки 0.1 МБ
asked = httpx.Request("GET", "https://api.example/v1/models")


def raised(error: Exception):
    raise error


def gateway_missing():
    """Шлюз litellm не импортируется: None в sys.modules — ImportError на import."""
    held = sys.modules.get("litellm")
    sys.modules["litellm"] = None  # type: ignore[assignment]
    try:
        OnlineCatalog(CFG).available("image")
    finally:
        if held is None:
            sys.modules.pop("litellm", None)
        else:
            sys.modules["litellm"] = held


# (род, вызов места, код, факты ⊂, в тексте, номер события, чего нет в проводе)
PROVIDER_CASES = [
    ("в строке канала провайдер без исполнителя", lambda: AdapterRegistry(CFG, project).spec("Нет_провайдера"),
     "PROVIDER_ADAPTER_MISSING", {"provider": "Нет_провайдера"}, ["Нет_провайдера"], False, ["config/", "адаптер"]),
    ("модуль исполнителя не импортируется", lambda: AdapterRegistry(SPOILED, project).load("Сломан"),
     "PROVIDER_ADAPTER_BROKEN", {"provider": "Сломан"}, ["Сломан"], True, ["нет_модуля", "No module"]),
    ("путь исполнителя ищется только под корнем: «os:system» — это core.providers.os, а не os",
     lambda: AdapterRegistry(SPOILED, project).load("Наружу"),
     "PROVIDER_ADAPTER_BROKEN", {"provider": "Наружу"}, [], True, ["core.providers"]),
    ("у провайдера свежий список не объявлен", lambda: OnlineCatalog(CFG).live("Bria", "ключ"),
     "PROVIDER_LIST_UNDECLARED", {"provider": "Bria"}, ["Bria"], False, ["config/"]),
    ("ключа нет — отказ до запроса", lambda: OnlineCatalog(CFG).live("OpenAI", ""),
     "PROVIDER_KEY_MISSING", {"provider": "OpenAI"}, ["OpenAI"], False, []),
    ("провайдер отклонил ключ", lambda: raised(provider_refusal(httpx.Response(401, request=asked), "OpenAI", "запрос")),
     "PROVIDER_KEY_REJECTED", {"provider": "OpenAI", "status": 401}, ["OpenAI", "401"], False, ["set_provider_key"]),
    ("провайдер отказал: тело ответа — в журнал",
     lambda: raised(provider_refusal(httpx.Response(500, text="ТЕЛО-ПРОВАЙДЕРА", request=asked), "OpenAI", "запрос")),
     "PROVIDER_FAILED", {"host": "api.example", "status": 500}, [], True, ["ТЕЛО-ПРОВАЙДЕРА"]),
    ("шлюз онлайн-моделей не импортируется", gateway_missing,
     "PROVIDER_ADAPTER_BROKEN", {"provider": "litellm"}, ["litellm"], True, ["pip install"]),
    ("исполнитель http_image без блока online.http",
     lambda: AdapterRegistry(CFG, project).load("RemoveBg", "bg_removals")._decl("Нет"),
     "DECLARATION_INVALID", {"file": "providers.yaml"}, ["online.http.Нет (http_undeclared)"], False, ["providers.yaml: "]),
    ("раннер не объявлен провайдером", lambda: RunnerSupervisor(AdapterRegistry(SPOILED, project), project).endpoint,
     "DECLARATION_INVALID", {"file": "providers.yaml"},
     ["local.runner.provider (runner_undeclared)", "online.http.Нет_раннера (runner_undeclared)"], False,
     ["providers.yaml: "]),
    ("политика весов, положенных руками, — чужое значение", lambda: AdapterRegistry(SPOILED, project).ready("руками.onnx"),
     "DECLARATION_INVALID", {"file": "providers.yaml"}, ["local.hand_placed (literal_error)"], False, ["providers.yaml: "]),
    ("вид каталога моделей — чужое значение",
     lambda: ModelCatalog(SPOILED, project / "vendor" / "models").available("tts"),
     "DECLARATION_INVALID", {"file": "providers.yaml"}, ["local.sources.tts.kind (literal_error)"], False,
     ["providers.yaml: "]),
    ("веса модели лежат не целиком", lambda: raised(ModelCatalog(CFG, project).refusal(
        {"id": "org/m"}, {"verdict": "incomplete", "plan": [], "why": "часть короче"})),
     "MODEL_INCOMPLETE", {"model": "org/m"}, [], False, ["python scripts", "часть короче"]),
    ("руками положенное при политике refuse", lambda: AdapterRegistry(REFUSING, project).ready("руками.onnx"),
     "MODEL_UNVERIFIED", {"model": "руками.onnx"}, [], False, ["python scripts", "local.hand_placed"]),
    # П2: не влезает на устройство при объявленном отказе — до подъёма модели; опечатка режима — отказ декларации.
    ("модель не влезает на устройство", lambda: DiffusersLocalIMG(AdapterRegistry(CFG, project))._pipeline(
        project / "m", {}, {"device": "cuda:0", "dtype": ""}, {"mode": "error", "need_mb": 9000, "available_mb": 4000}),
     "MODEL_DOES_NOT_FIT", {"device": "cuda:0", "need_mb": 9000, "available_mb": 4000}, ["9000", "4000"], False,
     ["oversize", "config/"]),
    ("режим выгрузки — чужое значение", lambda: DiffusersLocalIMG(AdapterRegistry(CFG, project))._pipeline(
        project / "m", {}, {"device": "cuda:0", "dtype": ""}, {"mode": "offlaod", "need_mb": 9000, "available_mb": 4000}),
     "DECLARATION_INVALID", {"file": "providers.yaml"}, ["local.gpu.oversize (literal_error)"], False, ["offlaod"]),
    # П3: предел встроенной отправки — свой код с размером и пределом; сверка диска после загрузки — без хоста в тексте.
    ("файл больше предела встроенной отправки провайдера", lambda: AdapterRegistry(CFG, project).load(
        "Replicate", "upscales")._body(
        {**AdapterRegistry(CFG, project).config["online"]["http"]["Replicate"], "send_mode": "json_base64",
         "max_inline_mb": 0.1},
        MediaRequest(input="x", params={}, target=project / "o.png", models_dir=project, source=BIG,
                     provider="Replicate", workspace=project)),
     "PROVIDER_INPUT_TOO_LARGE", {"path": "big.png", "size_mb": 0.3, "limit_mb": 0.1, "provider": "Replicate"},
     ["big.png", "0.3", "0.1", "Replicate"], False, ["max_inline_mb", "config/"]),
    ("результат не лёг на диск", lambda: TaskCycle(ROOT / "config" / "media_tasks.yaml").verify_download(
        "нет.wav", project), "DOWNLOAD_INCOMPLETE", {"path": "нет.wav", "got": 0}, ["0"], False, []),
]
sent: list[str] = []
seams = OnlineCatalog.transport, OnlineCatalog.resolve
OnlineCatalog.transport = httpx.MockTransport(lambda request: sent.append(str(request.url)) or httpx.Response(401))
OnlineCatalog.resolve = staticmethod(lambda host, port, *a, **k: [(2, 1, 6, "", ("52.7.7.7", port))])
try:
    for kind, place, code, facts, said, evented, absent in PROVIDER_CASES:
        try:
            place()
            caught: Exception | None = None
        except Exception as e:                         # чужое исключение — улика проверки, а не обрыв набора
            caught = e
        refused = engine.refusal(caught) if caught is not None else None
        got = refused.error if refused is not None else None
        if refused is None or got is None:                 # сырое исключение до двери — провал той же проверки кода
            check(f"{kind}: {code}, факты {sorted(facts)} — кодом, а не сырым исключением", False, repr(caught))
            continue
        wire = " ".join(strings(answer(refused)))
        check(f"{kind}: {code}, факты {sorted(facts)}",
              got.code == code and all(got.facts.get(k) == v for k, v in facts.items()), (got.code, got.facts))
        check(f"{kind}: текст — шаблон реестра с фактами, текста места нет; названо {said}",
              got.message == registry.get_error(code, facts=got.facts).message and all(s in got.message for s in said),
              got.message)
        check(f"{kind}: шаблону и рецепту хватило фактов", wants(code) <= set(got.facts), (sorted(wants(code)), got.facts))
        check(f"{kind}: номер события {'фактом — причина в журнале' if evented else 'не нужен'}",
              ("event" in got.facts) == evented, got.facts)
        check(f"{kind}: в проводе нет {absent}", not any(a in wire for a in absent), wire[:240])
finally:
    OnlineCatalog.transport, OnlineCatalog.resolve = seams
check("ни один случай не ушёл в сеть: ключа нет — запрос не уходит", not sent, sent)

print("== 6. Сбой части поиска (опись №7): часть — формой отказа, ни одной удачной — отказ вызова ==")
# Прежде упавшее чтение уходило в успех как errors[{error: str(e)}]: «TABLE_NOT_FOUND» строкой без фактов и рецепта,
# одиночный поиск по несуществующей таблице — «успех» с нулём строк, который ИИ читает как «ничего не нашлось».
engine._state_manager.write_snapshot("поиск/есть", {"META": {"schema": {}, "rows": {"R1": {"title": "x"}}}})
got = call("search_quick", table="поиск/есть", sheet="META")
check("законный: таблица есть — строки, сбоев нет", got.status == "success" and got.data["rows"] and not got.data["errors"],
      got.error or got.data)
got = call("search_quick", table="нет_таблицы", sheet="META")
check("одно чтение упало — отказ вызова кодом своей причины, а не «успех» с пустым итогом",
      got.status == "error" and got.error.code == "TABLE_NOT_FOUND" and got.error.facts.get("table") == "нет_таблицы",
      got.error or got.data)
got = call("search_multi", tables=[{"table": "поиск/есть", "sheet": "META"}, {"table": "нет_таблицы", "sheet": "META"}])
part = ((got.data or {}).get("errors") or [{}])[0]
check("часть упала — строки удачной и часть формой отказа: код, текст реестра, факты",
      got.status == "success" and got.data["rows"] and part.get("code") == "TABLE_NOT_FOUND"
      and part.get("message") == registry.get_error("TABLE_NOT_FOUND", facts={"table": "нет_таблицы"}).message
      and part.get("facts", {}).get("table") == "нет_таблицы", part)
got = call("fs_search_multi", queries=[{"root": "../../etc"}])
check("поиск файлов вне рабочей области — отказ PATH_ESCAPE, а не «успех» с кодом строкой",
      got.status == "error" and got.error.code == "PATH_ESCAPE", got.error or got.data)
got = call("fs_search_multi", queries=[{"root": "есть", "name_pattern": "["}, {"root": "есть"}])
part = ((got.data or {}).get("errors") or [{}])[0]
check("часть пакета с кривым regex — VALIDATION_ERROR с полем, соседняя часть отработала (опись №6)",
      got.status == "success" and got.data["results"] and part.get("code") == "VALIDATION_ERROR"
      and listed(part.get("errors") or [], {"field": "name_pattern", "rule": "regex"}), part)

print("== 7. Хаб моделей перегружен, офлайн или молчит (опись №5): отказ «источник недоступен», срок объявлен ==")
# Прежде список хаба (list_models) не был обёрнут: 429/5xx/офлайн уходили INTERNAL_ERROR «инструмент упал», а сессия хаба
# ждала ответа без срока (huggingface_hub 1.31.0: httpx.Client(timeout=None)).
import huggingface_hub as _hf7
from huggingface_hub.errors import HfHubHTTPError as _HubHTTP7
from huggingface_hub.utils import get_session as _hub_session7

_url7 = "https://huggingface.co/api/models"
_hub_timeout7 = Declaration(ROOT / "config" / "providers.yaml", ContractError, "провайдеров").need(
    "local", "source_kinds", "hf_hub", "timeout_sec")
_real_api7 = _hf7.HfApi
for _label7, _cause7, _status7 in (
        ("429", _HubHTTP7("429", response=httpx.Response(429, request=httpx.Request("GET", _url7))), 429),
        ("503", _HubHTTP7("503", response=httpx.Response(503, request=httpx.Request("GET", _url7))), 503),
        ("офлайн", httpx.ConnectError("нет сети"), None), ("срок", httpx.ReadTimeout("срок вышел"), None)):
    class _Broken7:
        def list_models(self, **kw):
            raise _cause7

    _hf7.HfApi = _Broken7
    try:
        got = call("media_models", scope="local", kind="image")
    finally:
        _hf7.HfApi = _real_api7
    err = got.error
    check(f"список хаба, {_label7}: MODEL_SOURCE_UNREACHABLE с хостом, текст — шаблон реестра",
          err is not None and err.code == "MODEL_SOURCE_UNREACHABLE" and err.facts.get("host") == "huggingface.co"
          and err.facts.get("status") == _status7 and wants(err.code) <= set(err.facts)
          and err.message == registry.get_error(err.code, facts=err.facts).message,
          err or got.data)
check("срок фазы запроса хаба — объявленный, а не «без срока»",
      _hub_session7().timeout.read == float(_hub_timeout7), _hub_session7().timeout)

print("== 8. Законные адреса книги (опись №6): ячейка, столбец, диапазон читаются, а не падают ==")
for _label8, _range8, _shape8 in (("одна ячейка", "A1", (1, 1)), ("диапазон", "A1:B2", (2, 2)), ("столбец", "A:A", None)):
    got = call("excel_read_range", path="книга.xlsx", sheet="Лист1", cell_range=_range8)
    rows8 = (got.data or {}).get("values") or []
    check(f"чтение {_label8} {_range8}: строки, а не INTERNAL_ERROR",
          got.status == "success" and rows8 and (_shape8 is None or (len(rows8), len(rows8[0])) == _shape8),
          got.error or got.data)

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
