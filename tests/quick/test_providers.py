"""
tests/quick/test_providers.py — динамический выбор провайдера/модели по данным канала.

Проверяет: провайдер и модель приходят ИЗ ДАННЫХ (не из кода), исчерпанный лимит уводит на
объявленный fallback и говорит об этом, кольцо в данных не зацикливает, отказы идут кодами
реестра, параметры вызова = вся строка минус служебные столбцы.
"""
import os
import sys
import tempfile
import warnings
from pathlib import Path

import yaml

warnings.simplefilter("error", UserWarning)
# ...но только на СВОИ предупреждения. Чужие библиотеки предупреждают штатно (ROCm сообщает, что
# быстрый бэкенд внимания на этой карте экспериментальный), и падать из-за чужой информационной
# строки значит получить красный тест там, где ничего не сломано.
for _noisy in ("torch.*", "transformers.*", "diffusers.*", "huggingface_hub.*"):
    warnings.filterwarnings("default", category=UserWarning, module=_noisy)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.providers import ProviderError, ProviderResolver

CFG = ROOT / "config" / "providers.yaml"

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


def rows():
    return [
        {"resource_type": "image_generations", "provider": "Fal", "fallback_provider": "OpenAI",
         "daily_limit": 500, "current_usage": 0, "warning_threshold": 400,
         "model": "flux-pro-1.1", "img_size": "1024x1024", "img_n": 1, "sync_mode": False},
        {"resource_type": "image_generations", "provider": "OpenAI", "fallback_provider": "",
         "daily_limit": 200, "current_usage": 0, "warning_threshold": 150,
         "model": "dall-e-3", "img_size": "1024x1024", "img_n": 1, "sync_mode": False},
        {"resource_type": "stt_characters", "provider": "Local", "fallback_provider": "",
         "daily_limit": -1, "current_usage": 99999, "warning_threshold": -1,
         "model": "whisper-large-v3", "model_size": "large"},
    ]


res = ProviderResolver(CFG)

print("== 1. Провайдер и модель приходят ИЗ ДАННЫХ канала ==")
_d = res.resolve(rows(), "image_generations", source="project")
ok(_d["provider"] == "Fal", f"взят первый объявленный провайдер ({_d['provider']})")
ok(_d["params"]["model"] == "flux-pro-1.1", "модель из той же строки")
ok(_d["source"] == "project", "источник данных назван")
ok("daily_limit" not in _d["params"] and "provider" not in _d["params"],
   "служебные столбцы не уходят в вызов")
ok(_d["params"]["img_size"] == "1024x1024" and _d["params"]["sync_mode"] is False,
   "ВСЯ остальная строка уходит адаптеру как параметры — новый параметр = новый столбец")
import re as _re
_src = (ROOT / "core/providers/resolver.py").read_text(encoding="utf-8")
# По границам слова: «Fal» подстрокой сидит внутри `False` и дал бы ложное срабатывание.
_hits = [n for n in ("Fal", "OpenAI", "flux", "dall-e", "ElevenLabs", "whisper", "Local_vtracer")
         if _re.search(rf"\b{_re.escape(n)}\b", _src)]
ok(not _hits, f"в коде резолвера нет ни одного имени провайдера или модели (найдено: {_hits})")

print("== 2. Смена провайдера = правка данных, не кода ==")
_r = rows()
_r[0]["model"] = "flux-dev"                      # так это делает table_update по строке
ok(res.resolve(_r, "image_generations")["params"]["model"] == "flux-dev",
   "смена модели в строке меняет решение сервера немедленно, без перезапуска")
_r2 = rows()
_r2[0], _r2[1] = _r2[1], _r2[0]                  # порядок строк = приоритет
ok(res.resolve(_r2, "image_generations")["provider"] == "OpenAI",
   "порядок строк задаёт, кто основной")

print("== 3. Исчерпанный лимит уводит на fallback и ГОВОРИТ об этом ==")
_r = rows()
_r[0]["current_usage"] = 500
_d = res.resolve(_r, "image_generations")
ok(_d["provider"] == "OpenAI", f"ушли на объявленный fallback ({_d['provider']})")
ok(_d["exhausted_chain"] == [{"provider": "Fal", "reason": "лимит исчерпан"}],
   "пропущенный провайдер назван с причиной, а не молча заменён")
ok(_d["chain"] == ["Fal", "OpenAI"], "цепочка перехода видна целиком")

print("== 4. Пороги и безлимитность — из строки ==")
_r = rows()
_r[0]["current_usage"] = 400
ok(res.resolve(_r, "image_generations")["warning"] is True,
   "расход дошёл до warning_threshold → предупреждение")
ok(res.resolve(_r, "image_generations")["provider"] == "Fal",
   "предупреждение НЕ переключает провайдера — это сигнал, а не отказ")
ok(res.resolve(rows(), "stt_characters")["provider"] == "Local",
   "отрицательный daily_limit = без ограничения, расход не исчерпывает")

print("== 5. Отказы идут кодами реестра ==")
try:
    res.resolve(rows(), "нет_такого_ресурса")
    ok(False, "неизвестный ресурс должен падать")
except ProviderError as e:
    ok(e.code == "PROVIDER_NOT_CONFIGURED", f"нет провайдера → PROVIDER_NOT_CONFIGURED ({e.code})")
    ok(e.suggested_tool == "table_append", "recovery ведёт к добавлению строки, а не к правке кода")
_r = rows()
_r[0]["current_usage"] = 500
_r[1]["current_usage"] = 200
try:
    res.resolve(_r, "image_generations")
    ok(False, "исчерпание всех провайдеров должно падать")
except ProviderError as e:
    ok(e.code == "PROVIDER_EXHAUSTED", f"все исчерпаны → PROVIDER_EXHAUSTED ({e.code})")
    ok("Fal" in e.message and "OpenAI" in e.message, "в отказе перечислено, кто именно исчерпан")

print("== 6. Кольцо в данных не зацикливает (данные правит ИИ) ==")
_r = rows()
_r[0]["current_usage"] = 500
_r[1]["current_usage"] = 200
_r[1]["fallback_provider"] = "Fal"               # A → B → A
try:
    res.resolve(_r, "image_generations")
    ok(False, "кольцо должно завершиться отказом, а не зависанием")
except ProviderError as e:
    ok(e.code == "PROVIDER_EXHAUSTED", "кольцо A→B→A завершается кодом, а не бесконечным обходом")
    # Наблюдаемое следствие защиты: провайдер не перечисляется в цепочке дважды. Ограничение
    # глубины оборвало бы обход и без неё, но отчёт стал бы врать про число попыток.
    ok(e.message.count("Fal") == 1, f"в отчёте провайдер назван ОДИН раз, а не по кругу ({e.message})")

print("== 7. Битая декларация не глушится ==")
_bad = Path(tempfile.mkdtemp()) / "providers.yaml"
_bad.write_text("source: [не словарь\n", encoding="utf-8")
try:
    ProviderResolver(_bad).config
    ok(False, "битый YAML должен падать")
except ProviderError as e:
    ok(e.code == "DECLARATION_INVALID", f"битая декларация → DECLARATION_INVALID ({e.code})")
try:
    ProviderResolver(Path(tempfile.mkdtemp()) / "нет.yaml").config
    ok(False, "отсутствие декларации должно падать")
except ProviderError as e:
    ok(e.code == "TEMPLATE_NOT_FOUND", f"нет декларации → TEMPLATE_NOT_FOUND ({e.code})")

print("== 8. Декларация реально грузится (правка меняет поведение) ==")
_cfg2 = Path(tempfile.mkdtemp()) / "providers.yaml"
_data = yaml.safe_load(CFG.read_text(encoding="utf-8"))
_data["meta_columns"] = _data["meta_columns"] + ["model"]     # объявили model служебным
_cfg2.write_text(yaml.safe_dump(_data, allow_unicode=True), encoding="utf-8")
ok("model" not in ProviderResolver(_cfg2).resolve(rows(), "image_generations")["params"],
   "смена meta_columns меняет состав параметров — конфиг не декоративный")

print("== 8а. Опечатка в декларации не глушится копией в коде ==")
# Книга канала с ЧУЖИМИ именами столбцов: имена берутся из декларации, и если код держит свою
# копию, промах в декларации не отвергается — исчерпанный провайдер проходит как свободный.
_exhausted_rows = [{"resource_type": "tts", "provider": "elevenlabs", "quota": 10, "used": 10,
                    "fallback_provider": ""}]


def _declared_as(mutate) -> Path:
    data = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    mutate(data)
    path = Path(tempfile.mkdtemp()) / "providers.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


_named_ok = _declared_as(lambda d: d["limits"].update(limit_column="quota", usage_column="used"))
try:
    ProviderResolver(_named_ok).resolve(_exhausted_rows, "tts")
    ok(False, "объявленные имена столбцов должны читаться: лимит исчерпан")
except ProviderError as e:
    ok(e.code == "PROVIDER_EXHAUSTED", f"имена столбцов взяты из декларации ({e.code})")

_typo = _declared_as(lambda d: (d["limits"].update(limit_colunm="quota", usage_column="used"),
                                d["limits"].pop("limit_column")))
try:
    _got = ProviderResolver(_typo).resolve(_exhausted_rows, "tts")
    ok(False, f"опечатка в имени ключа отдала провайдера {_got['provider']} вместо отказа")
except ProviderError as e:
    ok(e.code == "DECLARATION_INVALID",
       f"опечатка в ключе лимита → отказ, а не молчаливый выбор исчерпанного ({e.code})")

_gone = _declared_as(lambda d: d.pop("limits"))
try:
    _got = ProviderResolver(_gone).resolve(_exhausted_rows, "tts")
    ok(False, f"снос раздела limits отдал провайдера {_got['provider']} вместо отказа")
except ProviderError as e:
    ok(e.code == "DECLARATION_INVALID",
       f"снесённый раздел лимитов → отказ: контроль расхода не выключается молча ({e.code})")

print("== 9. Инструмент: кто исполняет и какой моделью (шаг 2) ==")
import asyncio as _aio
import json as _json
import importlib.util as _ilu
from core.engine import Engine as _Eng
from core.ids import IDGenerator as _IDG
from core.reactions import Reactions as _Reactions
from core.state import StateManager as _SM
import server as _srv

_ws = Path(tempfile.mkdtemp(prefix="prov_")) / "workspace"
_ws.mkdir(parents=True)
_sm = _SM(_ws)
# Движок — с реестром реакций, как его собирает сервер (server.py): набор судит тот отказ, что получает клиент.
_REACTIONS = _Reactions(ROOT / "config" / "server_reactions.yaml")
_eng = _Eng(reactions=_REACTIONS, state_manager=_sm)
_srv.register_basic_tools(_eng, _IDG(), _sm)
(_ws / "ch").mkdir()


def _call(tool, **params):
    return _aio.run(_eng.call(tool, params))


_r = _call("media_provider_status", table="ch")
ok(_r.status == "success" and _r.data["source"] == "declaration",
   f"книга канала пуста → дефолт декларации, и это НАЗВАНО ({_r.data['source']})")
_active = {x["resource_type"]: x["provider"] for x in _r.data["resolved"]}
ok(len(_active) >= 3 and all(_active.values()),
   f"провайдер найден для каждого объявленного вида ресурса ({_active})")

(_ws / "ch" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
    "R1": {"resource_type": "image_generations", "provider": "Fal", "fallback_provider": "OpenAI",
           "daily_limit": 500, "current_usage": 500, "warning_threshold": 400, "model": "flux-pro-1.1"},
    "R2": {"resource_type": "image_generations", "provider": "OpenAI", "fallback_provider": "",
           "daily_limit": 200, "current_usage": 0, "warning_threshold": 150, "model": "dall-e-3"}}}}),
    encoding="utf-8")
_r2 = _call("media_provider_status", table="ch", resource_type="image_generations")
_x = _r2.data["resolved"][0]
ok(_r2.data["source"] == "project", "данные канала перекрывают дефолт декларации")
ok(_x["provider"] == "OpenAI" and _x["params"]["model"] == "dall-e-3",
   f"основной исчерпан → показан фактически работающий ({_x['provider']}/{_x['params']['model']})")
ok(_x["exhausted_chain"], "видно, что работа идёт УЖЕ на запасном, а не на основном")
_fact = _r2.facts[0].data
ok(_fact["on_fallback"] == ["image_generations"],
   "переход на fallback приходит фактом контракта, а не только в тексте")
ok("table_update" in _r2.data["switch_hint"],
   "сервер называет, ЧЕМ переключить — существующим инструментом, без нового")
_bad = _call("media_provider_status", table="ch", resource_type="нет_такого")
ok(_bad.status == "success" and _bad.data["failed"]
   and _bad.data["failed"][0]["code"] == "PROVIDER_NOT_CONFIGURED",
   "неизвестный ресурс отчитывается кодом в failed, не роняет остальные")

print("== 10. Цикл ожидания задачи + приёмка загрузки (шаг 3, часть 1) ==")
from core.providers import TaskCycle, TaskCycleError
from core.paths import PathEscapeError

_tc = TaskCycle(ROOT / "config" / "media_tasks.yaml", sleep=lambda s: None)
ok(_tc.classify("succeeded") == "done" and _tc.classify("failed") == "failed"
   and _tc.classify("что_то_своё") == "running",
   "слова статусов берутся из декларации, неизвестное считается «ещё выполняется»")
_seq = iter([{"status": "queued"}, {"status": "processing"}, {"status": "succeeded"}])
_w = _tc.wait(lambda tid: next(_seq), "T1")
ok(_w["outcome"] == "done" and _w["attempts"] == 3, f"ждём до готовности ({_w['attempts']} опроса)")
ok(_w["unknown_status"] == ["processing", "queued"],
   "непонятые статусы копятся в отчёте, а не выдаются за успех и не обрывают ожидание")
try:
    _tc.wait(lambda tid: {"status": "failed", "error": "модерация"}, "T2")
    ok(False, "отказ провайдера должен падать")
except TaskCycleError as e:
    ok(e.code == "PROVIDER_FAILED" and "модерация" in e.reason,
       "отказ провайдера → код реестра, причина провайдера сохранена")
_stuck = iter([{"status": "крутится"}] * 500)
try:
    _tc.wait(lambda tid: next(_stuck), "T3")
    ok(False, "вечная задача должна обрываться пределом")
except TaskCycleError as e:
    ok(e.code == "PROVIDER_TIMEOUT" and "крутится" in e.reason,
       "предел ожидания срабатывает и называет последний статус, а не молчит")

_dws = Path(tempfile.mkdtemp(prefix="dl_"))
(_dws / "a.wav").write_bytes(b"12345")
_v = _tc.verify_download("a.wav", _dws, declared_bytes=5)
ok(_v["bytes"] == 5 and all(_v["checks"].values()), "загрузка подтверждена ДИСКОМ, а не кодом ответа")
(_dws / "empty.wav").write_bytes(b"")
for _case, _p, _kw in (("нулевой размер", "empty.wav", {}),
                       ("размер не сошёлся", "a.wav", {"declared_bytes": 99}),
                       ("файла нет вовсе", "нет.wav", {})):
    try:
        _tc.verify_download(_p, _dws, **_kw)
        ok(False, f"{_case} должен падать")
    except TaskCycleError as e:
        ok(e.code == "DOWNLOAD_INCOMPLETE", f"{_case} → DOWNLOAD_INCOMPLETE ({e.code})")
try:
    _tc.verify_download("../../etc/passwd", _dws)
    ok(False, "путь наружу должен отбиваться")
except PathEscapeError:
    ok(True, "ссылка приходит извне — путь загрузки проходит containment")
ok(_tc.expected_name("scene_01", {"response_format": "wav"}) == "scene_01.wav",
   "расширение берётся из строки провайдера, а не угадывается из ссылки")
_tsrc = (ROOT / "core/providers/task_cycle.py").read_text(encoding="utf-8")
ok("succeeded" not in _tsrc and "processing" not in _tsrc,
   "в коде цикла нет ни одного слова-статуса провайдера — они в декларации")

print("== 11. Учёт расхода: лимит перестал быть теорией (шаг 3, часть 2) ==")
from core.providers import UsageLedger

_led = UsageLedger(CFG, _sm)
ok(_led.measure({"usage_unit": "character"}, text="12345") == 5.0,
   "единица «символ» меряется длиной текста — так же, как считает провайдер")
ok(_led.measure({"usage_unit": "image"}, files=3) == 3.0, "единица «файл» меряется числом файлов")
ok(_led.measure({}) == 1.0, "единица не названа → объявленный дефолт (вызов)")
try:
    _led.measure({"usage_unit": "попугаи"})
    ok(False, "неизвестная единица должна падать")
except ProviderError as e:
    ok(e.code == "USAGE_UNIT_UNKNOWN",
       f"неизвестная единица — отказ, а не тихий ноль: тихий ноль = вечный лимит ({e.code})")

# Расход пишется в строку канала, а чужая очередь при этом не применяется.
(_ws / "u").mkdir()
(_ws / "u" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
    "R1": {"resource_type": "tts_characters", "provider": "P1", "fallback_provider": "P2",
           "daily_limit": 10, "current_usage": 0, "warning_threshold": 8, "usage_unit": "character"},
    "R2": {"resource_type": "tts_characters", "provider": "P2", "fallback_provider": "",
           "daily_limit": 100, "current_usage": 0, "warning_threshold": 90}}}}), encoding="utf-8")
_sm.push_to_queue("u", {"action": "set", "sheet": "RESOURCE_LIMITS", "row_id": "R1",
                        "column": "provider", "value": "ЧУЖАЯ_ПРАВКА"})
_rep = _led.charge_call("u", "RESOURCE_LIMITS", {"row_id": "R1", "row": {"usage_unit": "character"}},
                        text="семь бук")
_after = _json.loads((_ws / "u" / "read.json").read_text())["RESOURCE_LIMITS"]["rows"]["R1"]
ok(_rep["before"] == 0 and _after["current_usage"] == 8, f"счётчик двинулся: 0 → {_after['current_usage']}")
ok(_after["provider"] == "P1" and len(_json.loads((_ws / "u" / "write.json").read_text())) == 1,
   "учёт расхода НЕ применил чужую очередь — накопленные правки ИИ остались нетронутыми")

# Замыкание петли: расход добивает лимит → резолвер сам уходит на запасного.
_rows_u = [{**r, "_row_id": rid} for rid, r in
           _json.loads((_ws / "u" / "read.json").read_text())["RESOURCE_LIMITS"]["rows"].items()]
ok(res.resolve(_rows_u, "tts_characters")["provider"] == "P1", "до исчерпания работает основной")
_led.charge_call("u", "RESOURCE_LIMITS", {"row_id": "R1", "row": {"usage_unit": "character"}},
                 text="ещё пять")
_rows_u = [{**r, "_row_id": rid} for rid, r in
           _json.loads((_ws / "u" / "read.json").read_text())["RESOURCE_LIMITS"]["rows"].items()]
_d_u = res.resolve(_rows_u, "tts_characters")
ok(_d_u["provider"] == "P2" and _d_u["exhausted_chain"],
   "расход добил лимит → переключение на запасного произошло само (петля замкнулась)")
try:
    _led.charge("u", "RESOURCE_LIMITS", "", 1)
    ok(False, "расход без ID строки должен падать")
except ProviderError as e:
    ok(e.code == "ROW_NOT_FOUND", f"дефолт декларации нельзя «зарядить» молча ({e.code})")

print("== 12. Исполнение локальной моделью: файл, приёмка, расход (шаг 3, часть 3) ==")
from core.providers import AdapterRegistry, MediaRequest

_reg = AdapterRegistry(CFG, ROOT)
try:
    _reg.load("Нет_такого_провайдера", "tts_characters")
    ok(False, "неизвестный провайдер должен падать")
except ProviderError as e:
    ok(e.code == "PROVIDER_ADAPTER_MISSING",
       f"имя провайдера из данных не поднимает произвольный модуль ({e.code})")

# Облачные провайдеры ОБЪЯВЛЕНЫ, но исполнителя у них нет. Честность держит этот отказ,
# а не файл-заглушка: мёртвый адаптер снят, значит отказ обязан остаться слышным и называть выход.
for _cloud, _rt in (("ElevenLabs", "tts_characters"), ("OpenAI", "images"), ("Fal", "images")):
    try:
        _reg.load(_cloud, _rt)
        ok(False, f"{_cloud} без адаптера обязан отказать, а не сделать вид, что исполнил")
    except ProviderError as _ec:
        ok(_ec.code == "PROVIDER_ADAPTER_MISSING" and "media_provider_status" in (_ec.suggested_tool or ""),
           f"{_cloud}: отказ назван и есть чем разбираться ({_ec.code})")
        # Рецепт называет ТОЛЬКО имена с исполнителем — иначе он ведёт на тот же отказ.
        # Сверка по элементам, а не подстрокой: пара «Fal:bg_removals» исполнима, голый «Fal» — нет.
        _named = [s.strip() for s in (_ec.reason or "").split("исполнять:")[-1].split(".")[0].split(",")]
        ok("Local_piper" in _named and _cloud not in _named,
           f"{_cloud}: в рецепте только исполнимое, без безадаптерных ({', '.join(_named[:4])})")

# Раскладка каталога весов — ОПИСЬ, а не знание адаптера: правка `path` меняет, куда он смотрит.
_lroot = Path(tempfile.mkdtemp(prefix="local_"))
_lmodels = _lroot / "иной_каталог"
_lmodels.mkdir()
(_lmodels / "installed.yaml").write_text(yaml.safe_dump(
    {"models": [{"id": "переложенный_голос", "kind": "tts", "path": "переложено/голос.onnx"}]},
    allow_unicode=True), encoding="utf-8")
_lcfg = _lroot / "providers.yaml"
_ldata = yaml.safe_load(CFG.read_text(encoding="utf-8"))
_ldata["local"]["models_dir"] = "иной_каталог"
_lcfg.write_text(yaml.safe_dump(_ldata, allow_unicode=True), encoding="utf-8")
_lreg = AdapterRegistry(_lcfg, _lroot)
ok(_lreg.model_path("переложенный_голос") == (_lmodels / "переложено/голос.onnx").resolve(),
   "путь к весам взят из описи целиком (каталог + path), в коде адаптера его нет")
ok(_lreg.model_path("руками_положенная.onnx") == (_lmodels / "руками_положенная.onnx").resolve(),
   "модель вне описи трактуется как путь внутри корня весов — руками положенное тоже работает")
try:
    _lreg.model_path("../../../../etc/passwd")
    ok(False, "путь наружу должен падать")
except PathEscapeError as e:
    ok(e.code == "PATH_ESCAPE" and e.facts.get("path") == "../../../../etc/passwd",
       f"имя модели из данных не выводит за корень весов — отказ двери, путь назван от корня ({e.code}, {e.facts})")
for _mod in ("core/providers/tts/piper_local.py", "core/providers/img/diffusers_local.py",
             "core/providers/stt/whisper_local.py"):
    _msrc = (ROOT / _mod).read_text(encoding="utf-8")
    _code = "\n".join(_line for _line in _msrc.splitlines() if not _line.lstrip().startswith("#"))
    _code = _code.split('"""', 2)[-1]                     # шапку модуля не считаем кодом
    ok("SUBDIR" not in _code and 'models_dir /' not in _code,
       f"{Path(_mod).name}: подкаталог весов не зашит в код")

_tts_model = _reg.models_dir / "tts" / "ru_RU-dmitri-medium.onnx"
# Модель — это ДВЕ разные вещи: веса на диске и библиотека, которая их исполняет. Веса тянет
# `models.py`, библиотеку — необязательная группа манифеста, и она не стоит ни в CI, ни на
# машине без выбранной среды расчёта. Проверять только веса значит требовать живого прогона
# там, где исполнять его нечем, — и красить набор за отсутствие необязательного.
_have_lib = _ilu.find_spec("piper") is not None
_have_tts = _tts_model.is_file() and _have_lib
_row_local = {"resource_type": "tts_characters", "provider": "Local_piper", "fallback_provider": "",
              "daily_limit": -1, "current_usage": 0, "warning_threshold": -1,
              "model": "ru_RU-dmitri-medium", "response_format": "wav", "speed": 1.0,
              "usage_unit": "character"}
(_ws / "v").mkdir()
(_ws / "v" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
    "L1": dict(_row_local)}}}), encoding="utf-8")

if _have_tts:
    _g = _call("media_generate", table="v", resource_type="tts_characters",
               input="Проверка локальной озвучки.", scene_id="scene01", video_slug="demo")
    ok(_g.status == "success", f"локальная озвучка исполнена ({_g.error.code if _g.error else 'ok'})")
    _file = _ws / _g.data["files"][0]
    ok(_file.is_file() and _file.stat().st_size > 0,
       f"на диске лежит непустой файл ({_file.name}, {_file.stat().st_size if _file.is_file() else 0} байт)")
    ok(_g.data["files"][0].endswith("demo_tts_scene01.wav"),
       f"имя ассета собрано по объявленному шаблону ({Path(_g.data['files'][0]).name})")
    _usage = _json.loads((_ws / "v" / "read.json").read_text())["RESOURCE_LIMITS"]["rows"]["L1"]
    ok(_g.data["usage"]["charged"] and _usage["current_usage"] == len("Проверка локальной озвучки."),
       f"расход состоявшегося вызова записан в строку канала ({_usage['current_usage']})")
    ok(_g.facts[0].type == "MediaGenerated" and _g.facts[0].data["provider"] == "Local_piper",
       "исполнение приходит фактом контракта: кто исполнил и чем")
else:
    _чего = "весов" if not _tts_model.is_file() else "библиотеки piper (среда расчёта не ставилась)"
    print(f"  ⚠ живой прогон локальной озвучки пропущен: нет {_чего}")

# Имя модели — данные, которые правит ИИ: путь наружу не читается.
(_ws / "v" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
    "L1": {**_row_local, "model": "../../../../etc/passwd"}}}}), encoding="utf-8")
_esc = _call("media_generate", table="v", resource_type="tts_characters",
             input="текст", scene_id="s1", video_slug="demo")
ok(_esc.status == "error" and _esc.error.code == "PATH_ESCAPE"
   and not Path(_esc.error.facts.get("path", "/")).is_absolute(),
   f"путь наружу в имени модели отбит дверью: PATH_ESCAPE, путь без места сервера "
   f"({_esc.error.code if _esc.error else 'success'}, {_esc.error.facts if _esc.error else ''})")

(_ws / "v" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
    "L1": {**_row_local, "model": "нет-такой-модели.onnx"}}}}), encoding="utf-8")
_nom = _call("media_generate", table="v", resource_type="tts_characters",
             input="текст", scene_id="s1", video_slug="demo")
ok(_nom.status == "error" and _nom.error.code == "LOCAL_MODEL_MISSING"
   and "media_model_install" in (_nom.error.recovery.reason if _nom.error.recovery else ""),
   "нет весов → честный отказ и назван инструмент, которым модель ставят")

(_ws / "v" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
    "L1": {**_row_local, "model": ""}}}}), encoding="utf-8")
_unnamed = _call("media_generate", table="v", resource_type="tts_characters",
                 input="текст", scene_id="s1", video_slug="demo")
ok(_unnamed.status == "error" and _unnamed.error.code == "BOOK_ROW_INVALID"
   and _unnamed.error.facts.get("sheet") == "RESOURCE_LIMITS" and _unnamed.error.facts.get("row") == "L1"
   and {"field": "model", "rule": "required"} in _unnamed.error.errors and "L1" in _unnamed.error.message,
   f"пустой model — ошибка строки книги: лист и строка от резолвера, поле и правило в errors "
   f"({_unnamed.error.code if _unnamed.error else 'успех!'}, {_unnamed.error.facts if _unnamed.error else ''})")

(_ws / "v" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
    "L1": {**_row_local, "response_format": "sh"}}}}), encoding="utf-8")
_sh = _call("media_generate", table="v", resource_type="tts_characters",
            input="текст", scene_id="s1", video_slug="demo")
ok(_sh.status == "error" and _sh.error.code == "FILE_TYPE_FORBIDDEN",
   f"результат провайдера не привилегирован: тип файла проходит тот же allowlist ({_sh.error.code if _sh.error else 'success'})")

# Параметры вызова картинки — только объявленные столбцы; чего в строке нет, того не передаём.
from core.providers.img.diffusers_local import DiffusersLocalIMG as _IMG

_kw = _IMG(_reg)._call_kwargs({"img_size": "768x512", "steps": 4, "img_n": 2, "guidance": 0.0})
ok(_kw == {"width": 768, "height": 512, "num_inference_steps": 4,
           "num_images_per_prompt": 2, "guidance_scale": 0.0},
   f"все параметры генерации пришли из строки канала ({_kw})")
ok(_IMG(_reg)._call_kwargs({}) == {},
   "пустая строка → ничего не навязываем: размер, шаги и силу подсказки решает сама модель")
ok("guidance_scale" in _IMG(_reg)._call_kwargs({"guidance": 0}),
   "guidance=0 — осмысленное значение (пошаговые модели), а не «не задано»")

# Параметры ПОДЪЁМА — по объявленной карте «столбец → параметр библиотеки», а не ветками.
_ldk = _IMG(_reg)._load_kwargs
ok(_ldk({}, {"variant": "fp16"}) == {"variant": "fp16"},
   "вариант весов приходит из ОПИСИ: ИИ не обязан знать, что лежит на диске")
ok(_ldk({"variant": "fp32"}, {"variant": "fp16"})["variant"] == "fp32",
   "непустой столбец строки перекрывает опись — но только непустой")
ok(_ldk({"device_map": "balanced", "max_memory": {0: "8GiB"}}, {}) ==
   {"device_map": "balanced", "max_memory": {0: "8GiB"}},
   "новый рычаг библиотеки = строка в декларации, а не ветка в коде")
ok(_ldk({"выдуманный_столбец": "x", "steps": 4}, {}) == {},
   "чего в объявленной карте нет — в загрузчик не уезжает, даже если это знакомый столбец")
ok(_reg.model_entry("такой-модели-нет") == {},
   "неизвестной модели опись не выдумывает свойств")

# Модель берётся из ОПИСИ, а не зашита в тест: источник локальных моделей один.
from core.providers.catalog import ModelCatalog as _MCat
from core.providers.hardware import probe as _probe

_gpu_decl = (yaml.safe_load(CFG.read_text(encoding="utf-8"))["local"] or {}).get("gpu") or {}
_img_entry = next((e for e in _MCat(CFG, _reg.models_dir).installed()
                   if e["kind"] == "image" and e["disk"]["verdict"] == "ready"), None)
# То же правило, что у озвучки: веса без среды расчёта живым прогоном не проверяются. `torch`
# приезжает группой ускорителя под КАРТУ машины, и на дереве без него набор обязан пропускать
# прогон с причиной, а не краснеть за отсутствие необязательного.
_img_lib = _ilu.find_spec("torch") is not None and _ilu.find_spec("diffusers") is not None
if _img_entry and _img_lib:
    (_ws / "i").mkdir()
    (_ws / "i" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
        "I1": {"resource_type": "image_generations", "provider": "Local_diffusers",
               "fallback_provider": "", "daily_limit": -1, "current_usage": 0,
               # `variant` в строке НЕТ намеренно: он приходит из описи. Если регрессия
               # вернёт требование столбца, вызов упадёт LOCAL_MODEL_MISSING прямо здесь.
               "warning_threshold": -1, "model": _img_entry["id"], "img_size": "512x512",
               "img_n": 1, "steps": 1, "usage_unit": "image"}}}}), encoding="utf-8")
    _gi = _call("media_generate", table="i", resource_type="image_generations",
                input="a lighthouse on a cliff at sunrise", scene_id="scene01", video_slug="demo")
    ok(_gi.status == "success", f"локальная генерация картинки исполнена моделью {_img_entry['id']} "
                                f"({_gi.error.code + ': ' + _gi.error.message if _gi.error else 'ok'})")
    _cmp = (_gi.data or {}).get("compute") or {}
    ok(_cmp.get("device") and _cmp.get("why"),
       f"в ответе сказано, ГДЕ считалось и почему — иначе «медленно» неотличимо от «сломано» ({_cmp})")
    _hw_now = _probe(_reg.models_dir, _gpu_decl)
    ok(_cmp.get("device") != "cpu" or not any(c["usable"] for c in _hw_now["gpu"]),
       f"на процессоре считаем, ТОЛЬКО если доступной карты нет ({_cmp.get('device')}, "
       f"карт доступных: {sum(c['usable'] for c in _hw_now['gpu'])})")
    ok(_cmp.get("placement", {}).get("mode") == "device",
       f"модель влезла — положили целиком, без выгрузки ({_cmp.get('placement')})")

    # Ветка выгрузки — на настоящей модели и настоящей карте: меняется только объявленный порог,
    # при котором она перестаёт «влезать». Иначе ветка живёт непроверенной до первой большой модели.
    _big_decl = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    _big_decl["local"]["fit"]["overhead"] = 100.0
    _big_cfg = _ws.parent / "providers_oversize.yaml"
    _big_cfg.write_text(yaml.safe_dump(_big_decl, allow_unicode=True), encoding="utf-8")
    _off_reg = AdapterRegistry(_big_cfg, ROOT)
    _off = _IMG(_off_reg).generate(MediaRequest(
        input="a lighthouse", target=_ws / "offload.png", models_dir=_off_reg.models_dir,
        params={"model": _img_entry["id"], "img_size": "256x256", "img_n": 1, "steps": 1}))
    ok(_off.meta["compute"]["placement"]["mode"] == "model_offload"
       and (_ws / "offload.png").stat().st_size > 5000,
       "модель больше карты — не приговор: выгрузка включилась по декларации, картинка нарисована "
       f"({_off.meta['compute']['placement']['mode']}, "
       f"{(_ws / 'offload.png').stat().st_size // 1024} КБ)")
    _pi = _ws / _gi.data["files"][0]
    ok(_pi.is_file() and _pi.stat().st_size > 10000,
       f"картинка на диске и не пустая ({_pi.stat().st_size if _pi.is_file() else 0} байт)")
    ok(_json.loads((_ws / "i" / "read.json").read_text())["RESOURCE_LIMITS"]["rows"]["I1"]["current_usage"] == 1,
       "расход картинки меряется файлами, а не символами — единица из строки канала")
else:
    _нет = "весов" if not _img_entry else "среды расчёта (torch/diffusers под карту машины)"
    print(f"  ⚠ живой прогон локальной генерации картинок пропущен: нет {_нет}")

print("== 12a-2. Установка модели отказывает по МЕСТУ до фоновой загрузки ==")
# Сценарием это недостижимо: чтобы вызвать отказ по-настоящему, нужен заполненный диск.
# Здесь судится сам путь — что проверка стоит ДО потока и что поток не стартует.
from core.providers.installer import ModelInstaller as _Инсталлятор


import threading as _потоки


class _КаталогМеста:
    def __init__(self, мб):
        self.models_dir = _ws
        self._мб = мб
        self.ставил = False
        self.снимки, self.поставлено, self.готово = [], None, _потоки.Event()

    def installed_as(self, model_id, kind):
        return None                             # здесь судится место, а не «уже стоит»

    def место_под(self, model_id, kind, force=False):
        свободно = 100.0
        if self._мб > свободно:                 # как у настоящего каталога: нехватку отказывает сам `место_под`
            raise ProviderError("MODEL_NO_DISK_SPACE", facts={"model": model_id, "need_mb": self._мб, "free_mb": свободно})
        self.снимки.append({"нужно_мб": self._мб, "свободно_мб": свободно})
        return self.снимки[-1]

    def install_planned(self, место, progress=None, mark=None, held=lambda: None):
        held()                                  # замок модели взят — как у настоящего каталога
        self.ставил, self.поставлено = True, место
        self.готово.set()
        return {"id": "проба", "mb": self._мб, "path": "x"}

    def silence_sec(self):
        return 0.0                              # здесь судится место, а не тишина загрузчика

    def speed(self):
        return {"floor": 0, "window": 0}        # мера скорости выключена — судится место


_тесный = _КаталогМеста(10 ** 6)
try:
    _Инсталлятор(_тесный).start("проба", "tts")
    ok(False, "установка при нехватке места обязана отказать, а не начать загрузку")
except ProviderError as _отказ:
    ok(_отказ.code == "MODEL_NO_DISK_SPACE", f"отказ каталога по месту доходит из start своим кодом ({_отказ.code})")
ok(not _тесный.ставил,
   "до скачивания дело не дошло: проверка стоит ДО фонового потока, а не после гигабайтов")
_просторный = _КаталогМеста(1.0)
_состояние = _Инсталлятор(_просторный).start("проба", "tts")
ok(_состояние["status"] == "processing",
   "когда места хватает, установка стартует обычным порядком — отказ не стал всегдашним")
ok(_просторный.готово.wait(10) and len(_просторный.снимки) == 1 and _просторный.поставлено is _просторный.снимки[0],
   "поток ставит по тому же снимку, по которому судили место: место спрошено один раз")

print("== 12a-3. Отбор по машине: отсеянные посчитаны, а не потеряны ==")
_все = _call("media_models", scope="installed")
_подходят = _call("media_models", scope="installed", fits=True)
ok(_все.status == "success" and _подходят.status == "success", "оба списка отдались")
ok(_подходят.data["fits"] is True and "filtered_out" in _подходят.data,
   "ответ говорит, что отбор был, и называет число отсеянных")
ok(len(_подходят.data["models"]) + _подходят.data["filtered_out"] == len(_все.data["models"]),
   f"баланс сходится: {len(_подходят.data['models'])} + {_подходят.data['filtered_out']} = "
   f"{len(_все.data['models'])} — отсеянное посчитано, а не пропало молча")
ok(_все.data["filtered_out"] == 0 and _все.data["fits"] is False,
   "без запроса отбора никто ничего не режет: список остаётся полным")

print("== 12b. Асинхронный провайдер: цикл прозвонки живёт ВНУТРИ вызова ==")
import types as _types

# Двойник асинхронного провайдера: он отвечает задачей, а не файлом. Ставим его в реестр
# адаптеров временной декларацией — так проверяется проводка, а не наличие ключей от облака.
_fake = _types.ModuleType("core.providers.fake_async")


class _FakeAsync:
    calls = {"poll": 0}

    def __init__(self, models_dir):
        self.models_dir = models_dir

    def generate(self, request):
        from core.providers import MediaOutcome
        self._input = request.input
        return MediaOutcome(task_id="TASK-1")

    def poll(self, task_id):
        _FakeAsync.calls["poll"] += 1
        return ({"status": "processing"} if _FakeAsync.calls["poll"] < 3
                else {"status": "succeeded", "url": "https://example.invalid/a.wav"})

    def fetch(self, answer, target):
        Path(target).write_bytes(b"RIFFfake-audio")     # так провайдер положил бы скачанный файл
        return {"bytes": 14}


class _FakeLink:
    """Провайдер, который файл сам не забирает: он отдаёт только ссылку."""

    url = ""

    def __init__(self, models_dir):
        self.models_dir = models_dir

    def generate(self, request):
        from core.providers import MediaOutcome
        return MediaOutcome(task_id="TASK-2")

    def poll(self, task_id):
        return {"status": "succeeded", "audio_url": _FakeLink.url}


_fake._FakeAsync = _FakeAsync
_fake._FakeLink = _FakeLink
sys.modules["core.providers.fake_async"] = _fake

# Живой HTTP-сервер: ссылку провайдера забирает САМ сервер, через свои проверки.
import http.server as _http
import threading as _thr


class _Serve(_http.BaseHTTPRequestHandler):
    body = b"RIFF" + b"z" * 200

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


_httpd = _http.HTTPServer(("127.0.0.1", 0), _Serve)
_thr.Thread(target=_httpd.serve_forever, daemon=True).start()
_FakeLink.url = f"http://127.0.0.1:{_httpd.server_port}/result.wav"

# Конфиг сервера не трогаем: копия каталога config/ + свой движок на ней. Правка боевого
# конфига «на время теста» — ровно тот немой побочный эффект, который мы ловим у других.
from tests.harness.live_server import declarations_copy

# Тестовый провайдер живёт на петле — послабление объявлено в КОПИИ конфига, боевой запрет цел.
_acfg_dir = declarations_copy({
    "providers.yaml": {"adapters.by_provider.Обл_async": "fake_async:_FakeAsync",
                       "adapters.by_provider.Обл_link": "fake_async:_FakeLink"},
    "media_tasks.yaml": {"download.fetch.schemes": ["http", "https"], "download.fetch.block_private": False}})

(_ws / "a").mkdir()
_arow = {"resource_type": "tts_characters", "provider": "Обл_async", "fallback_provider": "",
         "daily_limit": 100, "current_usage": 0, "warning_threshold": 90, "model": "облачная",
         "response_format": "wav", "sync_mode": False, "usage_unit": "character"}
(_ws / "a" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
    "A1": dict(_arow)}}}), encoding="utf-8")
_cfg_was = _srv.CONFIG_PATH
try:
    _srv.CONFIG_PATH = _acfg_dir
    _eng_a = _Eng(reactions=_REACTIONS, state_manager=_sm)
    _srv.register_basic_tools(_eng_a, _IDG(), _sm)
    _ga = _aio.run(_eng_a.call("media_generate", {
        "table": "a", "resource_type": "tts_characters", "input": "пять!",
        "scene_id": "s1", "video_slug": "demo"}))
    (_ws / "a" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
        "A1": {**_arow, "provider": "Обл_link"}}}}), encoding="utf-8")
    _gl = _aio.run(_eng_a.call("media_generate", {
        "table": "a", "resource_type": "tts_characters", "input": "ссылка",
        "scene_id": "s2", "video_slug": "demo"}))
finally:
    _srv.CONFIG_PATH = _cfg_was
    _httpd.shutdown()

ok(_gl.status == "success" and (_ws / _gl.data["files"][0]).read_bytes() == _Serve.body,
   f"провайдер отдал только ссылку — файл забрал сервер, содержимое совпало ({_gl.error.code if _gl.error else 'ok'})")

ok(_ga.status == "success", f"асинхронная задача доведена до файла ({_ga.error.code if _ga.error else 'ok'})")
ok(_ga.data["task"]["attempts"] == 3 and _ga.data["task"]["outcome"] == "done",
   f"прозвонка шла внутри ЭТОГО вызова, пока задача не была готова ({_ga.data['task'].get('attempts')} опроса)")
ok((_ws / _ga.data["files"][0]).is_file() and _ga.data["usage"]["charged"],
   "файл забран по ссылке и расход учтён — та же приёмка, что у локального провайдера")

print("== 13. Загрузка по ссылке провайдера: адрес и размер проверяются ДО диска ==")
import http.server as _http
import threading as _thr

import httpx as _httpx
from core.contracts import ContractError as _CE13
from core.paths import Files as _Files13
from core.providers.download import results as _results

_wire13: list[str] = []
_dl_dir = Path(tempfile.mkdtemp(prefix="dlout_"))
for _case, _url in (("http вместо https", "http://example.com/a.wav"),
                    ("петля", "https://127.0.0.1/a.wav"),
                    ("localhost по имени", "https://localhost/a.wav"),
                    ("метаданные облака", "https://169.254.169.254/latest/meta-data")):
    _dl13 = _results(ROOT / "config" / "media_tasks.yaml", proxies=dict,
                     transport=_httpx.MockTransport(lambda r: _wire13.append(str(r.url)) or _httpx.Response(200)))
    try:
        _dl13.fetch(_url, _Files13(_dl_dir), "a.wav", "t13")
        ok(False, f"{_case} должен отбиваться")
    except _CE13 as e:
        ok(e.code == "DOWNLOAD_FORBIDDEN" and not _wire13, f"{_case} → DOWNLOAD_FORBIDDEN до сети ({e.code} {e.facts})")

# Предел размера считается ПО ХОДУ записи: живой сервер отдаёт больше, чем разрешено.
_lim_cfg = Path(tempfile.mkdtemp(prefix="dlcfg_")) / "media_tasks.yaml"
_lim_decl = yaml.safe_load((ROOT / "config" / "media_tasks.yaml").read_text(encoding="utf-8"))
_lim_decl["download"]["fetch"].update({"schemes": ["http"], "block_private": False, "max_bytes": 1000,
                                       "timeout_sec": 10, "attempts": 1})
_lim_cfg.write_text(yaml.safe_dump(_lim_decl, allow_unicode=True), encoding="utf-8")


class _Big(_http.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "50000")
        self.end_headers()
        self.wfile.write(b"x" * 50000)

    def log_message(self, *a):
        pass


_srv_http = _http.HTTPServer(("127.0.0.1", 0), _Big)
_thr.Thread(target=_srv_http.serve_forever, daemon=True).start()
_out = Path(tempfile.mkdtemp(prefix="dlout_")) / "big.wav"
try:
    _results(_lim_cfg, proxies=dict).fetch(f"http://127.0.0.1:{_srv_http.server_port}/big.wav",
                                           _Files13(_out.parent), _out.name, "t13")
    ok(False, "превышение предела должно падать")
except _CE13 as e:
    ok(e.code == "DOWNLOAD_TOO_LARGE" and e.facts == {"limit_mb": 0.001},
       f"предел размера обрывает загрузку своим кодом ({e.code} {e.facts})")
ok(not _out.exists() and not list(_out.parent.iterdir()),
   "ни файла, ни остатка: недописанное убрано, а не выдано за результат")
_srv_http.shutdown()

print("== 14. Ключи провайдеров: лежат в канале, зашифрованы, ИИ недоступны (S23) ==")
from core.paths import (BUILTIN_SECRET_DIRS, Files, PathEscapeError, SecretAccessError,
                        configure_secret_dirs, is_secret_path)
from core.secrets import ChannelSecrets, fingerprint, redact

_KEY = "sk-СЕКРЕТНЫЙ-КЛЮЧ-1234567890"
_home = Path(tempfile.mkdtemp(prefix="home_"))
_box = ChannelSecrets(_ws, _home)
_set = _box.set("ch", "ElevenLabs", _KEY)
_encfile = _ws / "ch/.secrets/provider_keys.enc"
ok(_encfile.is_file() and _KEY.encode("utf-8") not in _encfile.read_bytes(),
   "на диске лежит шифротекст, а не ключ")
ok(oct(_encfile.stat().st_mode)[-3:] == "600" and oct(_encfile.parent.stat().st_mode)[-3:] == "700",
   f"права файла и каталога закрыты ({oct(_encfile.stat().st_mode)[-3:]}/{oct(_encfile.parent.stat().st_mode)[-3:]})")
ok(_set["fingerprint"] == fingerprint(_KEY) and _KEY not in str(_set),
   "наружу отдан отпечаток, а не значение")
ok(_box.get("ch", "ElevenLabs") == _KEY, "сервер свой ключ читает — иначе провайдера не вызвать")

# Все двери инструментов, ведущие к файлу: чтение, запись, удаление, перенос, листинг, поиск.
_doors = [("fs_read_file", {"path": "ch/.secrets/provider_keys.enc"}),
          ("fs_write_file", {"path": "ch/.secrets/provider_keys.enc", "content": "подмена"}),
          ("fs_delete", {"path": "ch/.secrets", "force": True}),
          ("fs_move", {"source": "ch/.secrets/provider_keys.enc", "destination": "ch/утёк.txt"}),
          ("fs_rename", {"path": "ch/.secrets", "new_name": "открыто"}),
          ("memory_read", {"path": "ch/.secrets/provider_keys.enc"})]
for _tool, _params in _doors:
    _dr = _call(_tool, **_params)
    ok(_dr.status == "error" and _dr.error.code == "SECRET_ACCESS_DENIED",
       f"{_tool}: закрыто кодом (получено {_dr.error.code if _dr.error else _dr.status})")
ok(_encfile.is_file() and _box.get("ch", "ElevenLabs") == _KEY,
   "после всех попыток файл цел и ключ на месте — отказ не повредил хранилище")

_tree = _call("fs_get_directory_tree", path="ch")
ok(".secrets/" not in _json.dumps(_tree.data, ensure_ascii=False),
   "каталог секретов не виден даже в списке: «невидим для чтения, но виден в дереве» — половина защиты")
for _q in ({"directory": ".", "keyword": "sk-"}, {"directory": ".", "extension": ".enc"}):
    _sr = _call("fs_smart_search", **_q)
    ok("secrets" not in _json.dumps(_sr.data, ensure_ascii=False),
       f"поиск не выдаёт закрытый файл ({_q})")

# Отказ должен объяснять, что двери НЕТ, иначе модель начнёт искать обход.
_dr = _call("fs_read_file", path="ch/.secrets/provider_keys.enc")
ok("вручную" in _dr.error.recovery.reason and "set_provider_key" in _dr.error.recovery.reason,
   "в отказе названа единственная законная дверь: владелец, вручную, скриптом")

# Ротация: новый ключ занимает место старого, прежний отпечаток остаётся следом замены.
_set2 = _box.set("ch", "ElevenLabs", "sk-НОВЫЙ-КЛЮЧ-0987654321")
ok(_box.get("ch", "ElevenLabs") == "sk-НОВЫЙ-КЛЮЧ-0987654321" and _set2["replaced"] == fingerprint(_KEY),
   "новый ключ заменил старый, и видно, что замена состоялась")
_st = _box.status("ch")
ok(_st[0]["present"] and _st[0]["fingerprint"] and "sk-" not in _json.dumps(_st, ensure_ascii=False),
   f"статус показывает отпечаток и дату, но не значение ({_st[0]['fingerprint']})")

# Fail-closed: конфиг может РАСШИРИТЬ запрет, но не отменить встроенный минимум.
configure_secret_dirs([])
ok(is_secret_path("ch/.secrets/provider_keys.enc"),
   "пустой список в конфиге не открывает каталог — запрет живёт в коде")
configure_secret_dirs(["мои_ключи"])
ok(is_secret_path("ch/мои_ключи/x") and is_secret_path("ch/.secrets/x"),
   "конфиг добавляет закрытые имена к встроенному минимуму")
configure_secret_dirs(sorted(BUILTIN_SECRET_DIRS))

# «Двери нет» и «путь наружу» — РАЗНЫЕ причины, и различает их сам тип исключения. Без этого
# закрытый каталог пришёл бы как PATH_ESCAPE, и модель искала бы обход там, где двери просто нет.
try:
    Files(_ws).place("ch/.secrets/provider_keys.enc")
    ok(False, "закрытый каталог обязан отказывать")
except SecretAccessError:
    ok(True, "закрытый каталог → SecretAccessError, а не общий выход за область")
except PathEscapeError:
    ok(False, "закрытый каталог назван «выходом за область» — причина подменена")

print("== 14b. Провайдеру нужен ключ: сервер говорит «прав нет», а не молчит ==")
_kcfg_dir = declarations_copy({"providers.yaml": {
    "adapters.by_provider.Обл_ключ": {"adapter": "fake_async:_FakeKeyed", "requires_key": True}}})


class _FakeKeyed:
    """Провайдер, которому ключ нужен: проверяем, что он его получает, а ответ — нет."""

    seen = ""

    def __init__(self, registry):
        pass

    def generate(self, request):
        from core.providers import MediaOutcome
        _FakeKeyed.seen = request.api_key
        if request.input == "отказ":
            raise ProviderError("PROVIDER_FAILED",
                                f"провайдер ответил: invalid api key '{request.api_key}'",
                                reason=f"ключ {request.api_key} отклонён")
        request.target.write_bytes(b"RIFF" + b"audio")
        return MediaOutcome(files=[request.target])


_fake._FakeKeyed = _FakeKeyed
(_ws / "k").mkdir()
_krow = {"resource_type": "tts_characters", "provider": "Обл_ключ", "fallback_provider": "",
         "daily_limit": 1000, "current_usage": 0, "warning_threshold": 900,
         "model": "облачная", "response_format": "wav", "usage_unit": "character"}
(_ws / "k" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
    "K1": dict(_krow)}}}), encoding="utf-8")
_cfg_was = _srv.CONFIG_PATH
try:
    _srv.CONFIG_PATH = _kcfg_dir
    # Журнал фактов этого сервера — свой, во временном каталоге: проверка «ключ не в журнале» читает то, что он пишет.
    from core.observability import Journal as _Journal
    _klog = Path(tempfile.mkdtemp(prefix="klog_")) / "session.md"
    _sm_k = _SM(_ws, journal=_Journal(_klog, 1 << 20, 1))
    _eng_k = _Eng(reactions=_REACTIONS, state_manager=_sm_k)
    _srv.register_basic_tools(_eng_k, _IDG(), _sm_k)
    _kcall = lambda tool, **p: _aio.run(_eng_k.call(tool, p))  # локальный хелпер

    _nokey = _kcall("media_generate", table="k", resource_type="tts_characters",
                    input="текст", scene_id="s1", video_slug="demo")
    ok(_nokey.status == "error" and _nokey.error.code == "PROVIDER_KEY_MISSING",
       f"нет ключа → PROVIDER_KEY_MISSING, а не «внутренняя ошибка» ({_nokey.error.code if _nokey.error else 'success'})")
    ok("вручную" in _nokey.error.recovery.reason and "set_provider_key" in _nokey.error.recovery.reason
       and "table_update" in _nokey.error.recovery.reason,
       "сервер говорит: прав нет, вставляет владелец вручную — и называет обходной путь без ключа")

    _status_k = _kcall("media_provider_status", table="k", resource_type="tts_characters")
    _kstate = _status_k.data["resolved"][0]["key"]
    ok(_kstate["required"] and not _kstate["present"] and "set_provider_key" in _kstate["how_to_set"],
       f"в статусе видно: ключ нужен, его нет, и чем его вносят ({_kstate['required']}/{_kstate['present']})")

    ChannelSecrets(_ws, _kcfg_dir.parent).set("k", "Обл_ключ", _KEY)
    _withkey = _kcall("media_generate", table="k", resource_type="tts_characters",
                      input="текст", scene_id="s1", video_slug="demo")
    ok(_withkey.status == "success" and _FakeKeyed.seen == _KEY,
       "ключ дошёл до провайдера в исходящем вызове")
    _dump = _json.dumps({"data": _withkey.data, "facts": [f.model_dump() for f in _withkey.facts]},
                        ensure_ascii=False)
    ok(_KEY not in _dump and "sk-" not in _dump,
       "и не появился ни в данных ответа, ни в фактах контракта")
    ok(_klog.is_file() and _KEY not in _klog.read_text(encoding="utf-8"),
       f"и не попал в журнал фактов, который этот сервер пишет ({_klog.stat().st_size if _klog.exists() else 'журнала нет'} байт)")

    _reject = _kcall("media_generate", table="k", resource_type="tts_characters",
                     input="отказ", scene_id="s2", video_slug="demo")
    ok(_KEY not in _json.dumps(_reject.error.model_dump(), ensure_ascii=False),
       "провайдер процитировал ключ в тексте отказа — наружу он не ушёл (последний рубеж redact)")
finally:
    _srv.CONFIG_PATH = _cfg_was

ok(redact(f"ключ {_KEY} отклонён", [_KEY]) == f"ключ <ключ скрыт {fingerprint(_KEY)}> отклонён",
   "redact подменяет значение отпечатком, а не многоточием — видно, КАКОЙ ключ отвергли")

print("== 15. Каталог моделей: список живой, установка проверяет ЧТО кладёт на диск ==")
from core.providers.catalog import ModelCatalog, OnlineCatalog

_online = OnlineCatalog().available("tts", limit=0)
ok(_online and all(r["mode"] == "audio_speech" for r in _online),
   f"онлайн-список отфильтрован по виду ресурса ({len(_online)} моделей озвучки)")
ok(any(r["endpoints"] for r in _online) and any(r["cost_per_character"] for r in _online),
   "у моделей видны эндпоинт и цена — за этим в документацию ходить не нужно")
ok(len(OnlineCatalog().providers("image")) > 3,
   f"видно, кто вообще умеет картинки, ДО того как заводить ключ ({OnlineCatalog().providers('image')[:4]}…)")
ok(_KEY not in _json.dumps(_online, ensure_ascii=False),
   "список онлайн-моделей виден без ключа — ключ нужен для вызова, а не для списка")

_mcat = ModelCatalog(CFG, ROOT / "vendor" / "models")
# Правила установки — чистая проверка, сети не требует.
_keep, _refused = _mcat.allowed_files(["model.safetensors", "voice.onnx", "weights.bin",
                                       "old.ckpt", "config.json"])
ok(_keep == ["model.safetensors", "voice.onnx", "config.json"],
   f"ставим только форматы, кодом не являющиеся ({_keep})")
ok({r["file"] for r in _refused} == {"weights.bin", "old.ckpt"}
   and all("pickle" in r["reason"] for r in _refused),
   "pickle-веса отбиты с названной причиной: их загрузка выполняет код из файла")
try:
    _mcat.check_size(999999, "огромная")
    ok(False, "превышение предела размера должно падать")
except ProviderError as e:
    _shown = _eng.refusal(e).error
    ok(e.code == "MODEL_TOO_LARGE" and e.facts["model"] == "огромная" and "max_total_mb" in _shown.recovery.reason,
       f"размер сверх объявленного предела — свой код, факты, и клиент видит, где предел правится ({_shown.message})")
try:
    _mcat.check_repo("https://civitai.example/api/download/123")
    ok(False, "чужой источник должен отбиваться")
except ProviderError as e:
    _shown = _eng.refusal(e).error
    ok(e.code == "MODEL_SOURCE_FORBIDDEN" and "civitai" in _shown.message and "allow_sources" in _shown.recovery.reason,
       f"чужой САЙТ как источник весов не берётся: свой код, источник назван ({_shown.message})")

# Опись поставленного лежит рядом с весами, а конфиг машина не переписывает.
_cfg_before = CFG.read_text(encoding="utf-8")
ok(_mcat.inventory_file == ROOT / "vendor" / "models" / "installed.yaml",
   "опись лежит рядом с весами, а не в рукописном конфиге (иначе первая запись стёрла бы объяснения)")
ok(all(e.get("id") for e in _mcat.entries()),
   "объявленные в конфиге и поставленные скриптом видны одним списком")
ok(CFG.read_text(encoding="utf-8") == _cfg_before, "чтение каталога конфиг не трогает")

_installed_ids = {r["id"] for r in _mcat.installed()}
if "ru_RU-irina-medium" in _installed_ids:
    ok(_mcat.path_of("ru_RU-irina-medium").endswith(".onnx"),
       "поставленная скриптом модель находится по имени — канал может исполнять ею сразу")
else:
    print("  ⚠ вторая локальная модель не ставилась — проверка описи пропущена")

_inst = _call("media_model_install", model_id="ru_RU-denis-medium", kind="tts")
ok(_inst.status == "error" and _inst.error.code == "CONFIRM_REQUIRED",
   f"установка весов подтверждается явно: гигабайты с внешнего источника ({_inst.error.code if _inst.error else 'success'})")
_lst = _call("media_models", scope="installed")
# Эталон — сама опись, а не «больше нуля»: на чистой машине моделей нет вовсе, и инструмент
# обязан честно показать пустой список, а не считаться сломанным.
ok(_lst.status == "success"
   and ([m["id"] for m in _lst.data["models"]]
        == [e["id"] for e in ModelCatalog(CFG, ROOT / "vendor" / "models").inventory()]),
   f"инструмент показывает ровно то, что в описи ({len(_lst.data['models'])} моделей)")
_lst_on = _call("media_models", scope="online", kind="image", limit=5)
ok(all(m["mode"] == "image_generation" for m in _lst_on.data["models"]),
   "и что умеет шлюз онлайн — с провайдером и эндпоинтом")

print("== 16. Установка идёт долго: за ней наблюдают, и молчания нет ни в одном исходе ==")
import time as _time
from core.providers.installer import ModelInstaller


class _SlowCatalog:
    """Двойник каталога: установка длится, чтобы прозвонка была не теорией."""

    def __init__(self, models_dir, seconds=0.6, fail=None):
        self.models_dir = Path(models_dir)
        self.config_file = CFG                  # реестр отказов лежит рядом с декларацией, как у сервера
        self.seconds = seconds
        self.fail = fail
        self.install_rules = {}

    def installed_as(self, model_id, kind):
        return None                             # здесь судится прозвонка, а не «уже стоит»

    def место_под(self, model_id, kind, force=False):
        # Здесь судится прозвонка, а не диск: места заведомо больше, чем ляжет весов.
        return {"нужно_мб": 0.002, "свободно_мб": 1e6, "entry": {"id": model_id, "kind": kind}}

    def install_planned(self, место, progress=lambda _m: None, mark=None, held=lambda: None):
        held()                                  # замок модели взят — как у настоящего каталога
        if mark:
            mark()                              # признак работы доходит от установщика до каталога
        _time.sleep(self.seconds)
        if self.fail:
            raise self.fail
        (self.models_dir / "весы.onnx").write_bytes(b"x" * 2048)
        return {**место["entry"], "path": "весы.onnx", "mb": 0.002, "refused": []}

    def silence_sec(self):
        return 0.0                              # здесь судится прозвонка, а не тишина загрузчика

    def speed(self):
        return dict(self.measure)               # мера скорости: по умолчанию выключена, тик-проверки NI-01 задают свою

    measure = {"floor": 0, "window": 0}

    def on_disk(self, место):
        return self.landed                      # сколько «легло» — задаёт проверка

    landed = 0


_idir = Path(tempfile.mkdtemp(prefix="inst_"))
_inst_ok = ModelInstaller(_SlowCatalog(_idir), heartbeat_sec=0.1, stale_after_sec=5)
_st = _inst_ok.start("модель-1", "tts")
try:
    _seen_now = _inst_ok.status(_st["task_id"])[0]["status"]
except ProviderError as e:
    _seen_now = e.code
ok(_seen_now == "processing",
   f"установка видна прозвонкой сразу после ответа start — файл состояния лёг до ответа ({_seen_now})")
ok(_st["status"] == "processing" and _st["task_id"] and _st["params"] == {"model_id": "модель-1", "kind": "tts"},
   "установка запущена и вернула статус задачи с идентификатором, а не ждала гигабайты в одном вызове")
for _ in range(50):
    _cur = _inst_ok.status(_st["task_id"])[0]
    if _cur["status"] != "processing":
        break
    _time.sleep(0.1)
ok(_cur["status"] == "completed" and _cur["result"]["path"] == "весы.onnx",
   f"успех виден прозвонкой, с путём результата ({_cur['status']})")
ok(_cur["progress"]["elapsed_sec"] > 0, "видно, сколько заняла установка")
ok(_st["task_id"] in _inst_ok._marks, "признак работы каталога доходит до наблюдателя установки (отметка по task_id)")

_inst_bad = ModelInstaller(
    _SlowCatalog(Path(tempfile.mkdtemp(prefix="instf_")), seconds=0.2,
                 fail=ProviderError("LOCAL_MODEL_MISSING", "источник оборвал соединение")),
    heartbeat_sec=0.1, stale_after_sec=5)
_bad_id = _inst_bad.start("модель-2", "tts")["task_id"]
for _ in range(50):
    _bad_state = _inst_bad.status(_bad_id)[0]
    if _bad_state["status"] != "processing":
        break
    _time.sleep(0.1)
ok(_bad_state["status"] == "failed" and _bad_state["error"]["code"] == "LOCAL_MODEL_MISSING"
   and "оборвал" in _bad_state["error"]["message"] and _bad_state["params"]["model_id"] == "модель-2",
   f"сбой посреди установки виден кодом и причиной, а не тишиной ({_bad_state['status']})")

_inst_crash = ModelInstaller(
    _SlowCatalog(Path(tempfile.mkdtemp(prefix="instc_")), seconds=0.2, fail=RuntimeError("сырой-текст-исключения")),
    heartbeat_sec=0.1, stale_after_sec=5)
_crash_id = _inst_crash.start("модель-4", "tts")["task_id"]
for _ in range(50):
    _crash_state = _inst_crash.status(_crash_id)[0]
    if _crash_state["status"] != "processing":
        break
    _time.sleep(0.1)
ok(_crash_state["status"] == "failed" and _crash_state["error"]["code"] == "INTERNAL_ERROR"
   and _crash_state["error"]["facts"].get("event") and "сырой-текст" not in _json.dumps(_crash_state, ensure_ascii=False),
   f"неожиданное падение — код с номером события, сырой текст исключения клиенту не уходит ({_crash_state['error']})")

import threading as _thr16


class _LateTick(_SlowCatalog):
    """Двойник: тик наблюдателя прочёл «идёт» и держится в счёте, пока итог «failed» не ляжет на диск (не дольше 2 с)."""

    def __init__(self, models_dir):
        super().__init__(models_dir, seconds=0, fail=ProviderError("LOCAL_MODEL_MISSING", "оборвано"))
        self.entered, self.state_file = _thr16.Event(), None

    def on_disk(self, место):
        self.entered.set()
        for _ in range(40):
            if self.state_file and '"failed"' in self.state_file.read_text(encoding="utf-8"):
                break
            _time.sleep(0.05)
        return 7

    def install_planned(self, место, progress=lambda _m: None, mark=None, held=lambda: None):
        held()                                  # замок взят: файл состояния и наблюдатель уже есть
        self.entered.wait(2)
        raise self.fail


_late = _LateTick(Path(tempfile.mkdtemp(prefix="instl_")))
_inst_late = ModelInstaller(_late, heartbeat_sec=0.01, stale_after_sec=30)
_late_id = _inst_late.start("модель-5", "tts")["task_id"]
_late.state_file = _inst_late.state_dir / f"{_late_id}.json"
for _ in range(100):
    _late_state = _inst_late.status(_late_id)[0]
    if _late_state["status"] != "processing":
        break
    _time.sleep(0.05)
ok(_late_state["status"] == "failed" and _late_state["error"]["code"] == "LOCAL_MODEL_MISSING"
   and _late_state["progress"]["bytes"] == 7,
   f"итог отказа не затёрт последним тиком наблюдателя, а байты тика сохранены ({_late_state['status']}, "
   f"{_late_state['progress']['bytes']})")

# Простой — по признакам работы, а не по тику наблюдателя: живой поток при мёртвой сети — «прервано», а не вечное
# «идёт». Тик судится с переданным «сейчас», без часов.
_tick_cat = _SlowCatalog(Path(tempfile.mkdtemp(prefix="insts_")))
_inst_tick = ModelInstaller(_tick_cat, heartbeat_sec=1, stale_after_sec=100)
_inst_tick._write({"install_id": "t16", "model_id": "модель-3", "kind": "tts", "phase": "running",
                   "started": 1000.0, "heartbeat": 1000.0, "bytes": 0})
_место16 = {"entry": {"id": "модель-3", "kind": "tts"}, "plan": []}
for _now in range(1010, 1200, 10):                      # поток жив и тикает, байты не растут, загрузчик молчит
    _inst_tick._tick("t16", _место16, float(_now))
_dead = _inst_tick._read("t16")
ok(_dead["heartbeat"] == 1000.0 and _inst_tick._stalled(_dead, now=1101.0) and not _inst_tick._stalled(_dead, now=1099.0),
   f"сеть мертва, поток жив: тики не двигают «последнюю работу» — за пределом простой, до него идёт ({_dead['heartbeat']})")
_tick_cat.landed = 4096
_inst_tick._tick("t16", _место16, 1200.0)
_grown = _inst_tick._read("t16")
ok(_grown["heartbeat"] == 1200.0 and _grown["bytes"] == 4096,
   f"рост байтов плана двигает «последнюю работу» на время тика ({_grown['heartbeat']}, {_grown['bytes']})")
_inst_tick._marks["t16"] = 1250.0                       # отметка загрузчика: пауза повтора или блок сверки
_inst_tick._tick("t16", _место16, 1290.0)
_marked = _inst_tick._read("t16")
ok(_marked["heartbeat"] == 1250.0 and not _inst_tick._stalled(_marked, now=1340.0),
   f"законный: долгая пауза или сверка с отметками по ходу — «идёт», хотя байты не растут ({_marked['heartbeat']})")
_inst_tick._write({"install_id": "t16a", "model_id": "модель-a", "kind": "tts", "phase": "running",
                   "started": 2000.0, "heartbeat": 2000.0, "bytes": 4096})
_inst_tick._marks["t16b"] = 2100.0                      # отметка соседней установки другой модели, у t16a отметок нет
_inst_tick._tick("t16a", _место16, 2150.0)
_alone = _inst_tick._read("t16a")
ok(_alone["heartbeat"] == 2000.0 and _inst_tick._stalled(_alone, now=2101.0),
   f"отметка соседней установки не двигает «последнюю работу» этой: мёртвая сеть — простой ({_alone['heartbeat']})")
_inst_tick._write({"install_id": "t16c", "model_id": "модель-c", "kind": "tts", "phase": "running",
                   "started": 3000.0, "heartbeat": 3000.0, "bytes": 4096})
_tick_cat.landed = 0                                    # остаток снят (отпечаток разошёлся) — байты плана упали
_inst_tick._tick("t16c", _место16, 3010.0)
_dropped = _inst_tick._read("t16c")
_tick_cat.landed = 1024
_inst_tick._tick("t16c", _место16, 3020.0)
_regrown = _inst_tick._read("t16c")
ok(_dropped["bytes"] == 0 and _regrown["heartbeat"] == 3020.0 and _regrown["bytes"] == 1024,
   f"байты упали и растут заново ниже прежнего пика — это работа, «последняя работа» движется "
   f"({_dropped['bytes']} → {_regrown['bytes']}, {_regrown['heartbeat']})")
# NI-01: рост — работа, только если не медленнее меры загрузчика (байт/с) с прошлого тика; капля работой не считается.
_drip_cat = _SlowCatalog(Path(tempfile.mkdtemp(prefix="instd_")))
_drip_cat.measure = {"floor": 100, "window": 30}
_inst_drip = ModelInstaller(_drip_cat, heartbeat_sec=1, stale_after_sec=100)
_inst_drip._write({"install_id": "t16d", "model_id": "модель-d", "kind": "tts", "phase": "running",
                   "started": 4000.0, "heartbeat": 4000.0, "bytes": 0})
for _i, _now in enumerate(range(4010, 4200, 10), 1):    # 10 байт за 10 с — 1 байт/с при мере 100
    _drip_cat.landed = _i * 10
    _inst_drip._tick("t16d", _место16, float(_now))
_drip = _inst_drip._read("t16d")
ok(_drip["heartbeat"] == 4000.0 and _drip["bytes"] == 190 and _inst_drip._stalled(_drip, now=4101.0),
   f"капля ниже меры: байты растут, а «последняя работа» стоит со старта — за пределом простой "
   f"({_drip['heartbeat']}, {_drip['bytes']})")
_drip_cat.landed = 190 + 1000                           # 1000 байт за 10 с — ровно мера
_inst_drip._tick("t16d", _место16, 4200.0)
_paced = _inst_drip._read("t16d")
ok(_paced["heartbeat"] == 4200.0 and _paced["bytes"] == 1190,
   f"законный: рост не медленнее меры с прошлого тика двигает «последнюю работу» ({_paced['heartbeat']})")


def _built(stale_after: float) -> str:
    try:
        ModelInstaller(_drip_cat, heartbeat_sec=1, stale_after_sec=stale_after)
        return ""
    except _CE13 as e:
        return f"{e.code}: {e.message}"


_drip_cat.measure = {"floor": 100, "window": 150}
ok(_built(150).startswith("SCHEMA_INVALID") and "speed_window_sec 150" in _built(150) and _built(151) == "",
   f"простой не короче окна меры скорости: 150 с — отказ кодом с ключом окна, 151 с — строится ({_built(150)[:80]})")
_drip_cat.measure = {"floor": 100, "window": 30}
_stale_state = _inst_tick.status("t16")[0]
ok(_stale_state["status"] == "failed" and _stale_state["error"]["code"] == "INSTALL_STALLED"
   and "Повтори" in _stale_state["error"]["recovery"]["reason"],
   f"замершая установка — отказ кодом простоя и зовёт повторить ({_stale_state['status']}, {_stale_state['error']})")
ok("докачается с места обрыва, если источник не сменил файл" in _json.dumps(_stale_state, ensure_ascii=False),
   "статус обещает докачку с условием: остаток постоянен по модели, If-Range с валидатором ответившего сервера (M3c)")

_status_all = _call("media_install_status")
ok(_status_all.status == "success" and isinstance(_status_all.data["installs"], list),
   "инструмент показывает все установки разом — не нужно помнить идентификаторы")

print("== 16b. Чем исполнять — сервер СОВЕТУЕТ, а решает канал ==")
_adv_res = _call("media_provider_status", table="ch")
ok(_adv_res.status == "success", f"статус провайдеров отвечает ({_adv_res.error.code if _adv_res.error else 'ok'}: {_adv_res.error.message if _adv_res.error else ''})")
_adv = (_adv_res.data or {}).get("recommendations") or []
_ids = [a["id"] for a in _adv]
ok("image_openai" in _ids and "audio_elevenlabs" in _ids and "local_first" in _ids,
   f"совет по каждому виду ресурса приходит вместе со статусом ({_ids})")
ok(all(a["tool"] for a in _adv) and any("table_update" == a["tool"] for a in _adv),
   "совет исполним существующей дверью (table_update), а не прозой")
# В §14 ключи канала «ch» записаны ключом ДРУГОГО инстанса (как при восстановлении из чужого
# бэкапа). Такой файл не читается — но ослепить весь отчёт о провайдерах он не вправе.
_unread = [r["key"] for r in _adv_res.data["resolved"] if r["key"].get("unreadable")]
ok(_unread and _unread[0]["code"] == "SECRET_UNREADABLE",
   "нечитаемый файл ключей назван по имени и не роняет статус остальных провайдеров")
_rec_src = (ROOT / "config" / "recommendations.yaml").read_text(encoding="utf-8")
ok("gpt-image-2" in _rec_src and "eleven_v3" in _rec_src,
   "имена моделей стоят в декларации: они устаревают быстрее релизов сервера")
for _mod in ("tools/media/__init__.py", "core/providers/resolver.py", "core/providers/catalog.py"):
    ok("gpt-image" not in (ROOT / _mod).read_text(encoding="utf-8"),
       f"{Path(_mod).name}: ни одного имени модели в коде")

print("== 17. Список моделей у САМОГО провайдера: реестр шлюза стареет, провайдер — нет ==")


class _Models(_http.BaseHTTPRequestHandler):
    """Двойник провайдера: отвечает как OpenAI /v1/models и запоминает, чем его звали."""

    seen_auth = ""
    # gpt-image-2 реестр шлюза знает; «gpt-image-99-preview» — заведомо нет, на нём и проверяем
    # поведение с моделью свежее реестра.
    payload = {"data": [{"id": "gpt-image-2"}, {"id": "gpt-image-99-preview"},
                        {"id": "gpt-4o-mini-tts"}, {"id": "gpt-5-chat"}, {"id": "whisper-1"}]}

    def do_GET(self):
        _Models.seen_auth = self.headers.get("Authorization", "")
        if not _Models.seen_auth.endswith("RIGHT-KEY"):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"invalid api key"}')
            return
        body = _json.dumps(_Models.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


_mserv = _http.HTTPServer(("127.0.0.1", 0), _Models)
_thr.Thread(target=_mserv.serve_forever, daemon=True).start()

def _twin(scheme: str) -> Path:
    """providers.yaml копии деклараций с двойником живого списка по `scheme`; боевой запрет цел."""
    return declarations_copy({"providers.yaml": {"online.live.Двойник": {
        "url": f"{scheme}://127.0.0.1:{_mserv.server_port}/v1/models", "auth": "bearer", "items": "data",
        "id_field": "id"}}}) / "providers.yaml"


from core.providers.catalog import OnlineCatalog as _OC

# https обязателен с ключом — для двойника послабление объявлено в КОПИИ конфига.
_oc = _OC(_twin("https"))
try:
    _oc.live("Двойник", "ключ")
    ok(False, "https-двойник без сертификата должен падать по сети, а не по проверке схемы")
except ProviderError as e:
    ok(e.code == "PROVIDER_FAILED", f"недоступный адрес → PROVIDER_FAILED, не молчание ({e.code})")

# Тот же двойник по http: проверяем, что схема ловится ДО того, как ключ уйдёт в открытый канал.
_oc2 = _OC(_twin("http"))
try:
    _oc2.live("Двойник", "ключ")
    ok(False, "http с ключом должен отбиваться")
except ProviderError as e:
    ok(e.code == "DOWNLOAD_FORBIDDEN" and not _Models.seen_auth,
       f"с ключом ходим только по https — запрос НЕ ушёл ({e.code})")
    _said_live = _Eng(reactions=_REACTIONS).refusal(e).error.message
    ok("127.0.0.1" in _said_live and "https" in _said_live,
       f"клиент видит, какой адрес не пропущен и какая схема разрешена ({_said_live})")

# Разрешаем http только в копии конфига, чтобы проверить сам разбор ответа живым запросом.
import core.providers.catalog as _catmod
_orig_live = _catmod.OnlineCatalog.live


def _live_http(self, provider, api_key, kind=""):
    decl = (self.config.get("live") or {}).get(provider) or {}
    if decl.get("url", "").startswith("http://127.0.0.1"):
        decl = dict(decl, url=decl["url"].replace("http://", "https://", 1))
        self._decl._data["online"]["live"][provider] = dict(decl, url=decl["url"])
    return _orig_live(self, provider, api_key, kind)


_rows_live = []
try:
    import httpx as _httpx
    _resp = _httpx.get(f"http://127.0.0.1:{_mserv.server_port}/v1/models",
                       headers={"Authorization": "Bearer RIGHT-KEY"}, timeout=10)
    _rows_live = [{"id": m["id"], "provider": "Двойник", "kind": _oc2._kind_of(m["id"]),
                   "source": "provider"} for m in _resp.json()["data"]]
except Exception as e:
    print(f"  ⚠ двойник не поднялся ({e}) — разбор ответа не проверен")

if _rows_live:
    ok({r["id"] for r in _rows_live if r["kind"] == "image"} == {"gpt-image-2", "gpt-image-99-preview"},
       "сырой список провайдера разложен по видам ресурса ПО ИМЕНИ — режима вызова он не сообщает")
    _merged = _oc2.merge([r for r in _rows_live if r["kind"] == "image"], "image")
    _by_id = {m["id"]: m for m in _merged}
    ok(_by_id["gpt-image-99-preview"]["known_to_gateway"] is False,
       "модель, которой реестр не знает, НЕ выброшена, а помечена — иначе новинка была бы невидима")
    ok(_by_id["gpt-image-2"]["known_to_gateway"] and _by_id["gpt-image-2"]["mode"] == "image_generation",
       "знакомой реестр добавляет смысл: режим вызова и эндпоинт")
_mserv.shutdown()

(_ws / "clean").mkdir(exist_ok=True)
_no_key = _call("media_models", scope="online", kind="image", provider="OpenAI", table="clean", limit=3)
ok(_no_key.status == "success" and _no_key.data["live_error"]["code"] == "PROVIDER_KEY_MISSING"
   and _no_key.data["source"] == "gateway_registry",
   "без ключа список приходит из реестра шлюза, и НАЗВАНО, почему он может быть старее")
ok("реестр" in _no_key.data["note"], "частичность помечена текстом, а не молчанием")

print("== 18. Влезет ли модель: параметры + железо, и всё это БЕЗ root ==")
from core.providers.hardware import FitEstimator, probe

_gpu_rules = (yaml.safe_load(CFG.read_text(encoding="utf-8"))["local"] or {}).get("gpu") or {}
_hw = probe(ROOT, _gpu_rules)
ok(_hw["ram_total_mb"] > 0 and _hw["ram_available_mb"] > 0 and _hw["cpu_count"] > 0,
   f"память и ядра прочитаны без root ({_hw['ram_available_mb']} из {_hw['ram_total_mb']} МБ, "
   f"{_hw['cpu_count']} ядер)")
_own_cpus = getattr(os, "process_cpu_count", None)
_sys_cpus = os.cpu_count
_affinity = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
_empty = Path(tempfile.mkdtemp())                       # корни без /proc и /sys: молчат ступени-файлы и cgroup
_psutil_was = sys.modules.get("psutil")
try:
    os.process_cpu_count = lambda: 3                    # процесс привязан к 3 ядрам — подставной факт
    _bound = probe(ROOT, _gpu_rules)
    os.process_cpu_count = lambda: None                 # привязку не определить — по документации CPython None
    _stepped = probe(ROOT, _gpu_rules)
    _portable = probe(ROOT, _gpu_rules, proc=_empty, fs_root=_empty)   # нет /proc/meminfo — память через psutil
    sys.modules["psutil"] = None                        # psutil нет — следующая ступень, ядра системы
    _no_psutil = probe(ROOT, _gpu_rules)
    os.cpu_count = lambda: None                         # и ядра системы не определить, файлов ядра нет
    _blind = probe(ROOT, _gpu_rules, proc=_empty, fs_root=_empty)
    os.cpu_count = _sys_cpus
    del os.process_cpu_count                            # Python < 3.13: функции нет
    _system = probe(ROOT, _gpu_rules)
finally:
    os.cpu_count = _sys_cpus
    if _own_cpus:
        os.process_cpu_count = _own_cpus
    if _psutil_was is not None:
        sys.modules["psutil"] = _psutil_was
    else:
        sys.modules.pop("psutil", None)
ok(_bound["cpu_count"] == 3 and _bound["cpu_source"] == "os.process_cpu_count"
   and _system["cpu_count"] == os.cpu_count() and _system["cpu_source"] == "os.cpu_count",
   f"ядра — доступные процессу, источник назван; без process_cpu_count и psutil — ядра системы "
   f"({_bound['cpu_count']} {_bound['cpu_source']} / {_system['cpu_count']} {_system['cpu_source']})")
ok(_stepped["cpu_count"] == _affinity,
   f"process_cpu_count вернул None — число со ступени ниже, привязка через psutil, а не 0 ({_stepped['cpu_count']})")
ok(_stepped["cpu_source"] == "psutil.Process().cpu_affinity",
   f"источник — ступень, которая ответила, а не та, что промолчала ({_stepped['cpu_source']})")
ok(_no_psutil["cpu_count"] == os.cpu_count() and _no_psutil["cpu_source"] == "os.cpu_count",
   f"psutil нет — ступень ниже, ядра системы ({_no_psutil['cpu_count']} {_no_psutil['cpu_source']})")
ok(_blind["cpu_count"] == 1 and _blind["cpu_source"].startswith("минимум 1"),
   f"не ответила ни одна ступень — консервативный минимум 1 (HW-05), а не 0 и не отказ "
   f"({_blind['cpu_count']} {_blind['cpu_source']})")
_blind_cores = [g for g in _blind["unknown"] if g.startswith("число ядер")]
ok(len(_blind_cores) == 1 and all(s in _blind_cores[0] for s in ("os.process_cpu_count", "psutil", "os.cpu_count",
                                                                   "/sys/devices/system/cpu/online", "/proc/stat cpuN")),
   f"минимум назван в unknown вместе со всеми спрошенными ступенями ({_blind_cores or '—'})")
ok(_blind["ram_available_mb"] == 0 and any(g.startswith("объём памяти") for g in _blind["unknown"]),
   f"память не назвала ни одна ступень — консервативный 0 с причиной: «не влезет», а не «влезет» "
   f"({_blind['ram_available_mb']} МБ, {_blind['ram_source']})")
ok(_portable["ram_source"] == "psutil.virtual_memory" and _portable["ram_total_mb"] > 0,
   f"нет /proc/meminfo (не Linux) — память через psutil, а не «неизвестно» ({_portable['ram_source']})")
_cores_named = [g for s in (_hw, _bound, _stepped, _no_psutil, _system) for g in s["unknown"] if g.startswith("число ядер")]
ok(not _cores_named,
   f"число ядер известно — в unknown о ядрах ни строки, иначе ответ спорит сам с собой ({_cores_named or '—'})")


def _machine(cgroup: str, mounts: list[str], files: dict[str, str],
             meminfo: str = "MemTotal: 31250000 kB\nMemAvailable: 19531250 kB\n") -> dict:
    """Снимок на подставной машине: <корень>/proc/self/{cgroup,mountinfo}, <корень>/proc/meminfo и файлы групп."""
    root = Path(tempfile.mkdtemp())
    (root / "proc" / "self").mkdir(parents=True)
    (root / "proc" / "self" / "cgroup").write_text(cgroup, encoding="utf-8")
    (root / "proc" / "self" / "mountinfo").write_text("\n".join(mounts) + "\n", encoding="utf-8")
    (root / "proc" / "meminfo").write_text(meminfo, encoding="utf-8")
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return probe(ROOT, _gpu_rules, proc=root / "proc", fs_root=root)


_V2 = ["30 25 0:26 / /sys/fs/cgroup rw,nosuid - cgroup2 cgroup2 rw"]
_CG = "sys/fs/cgroup"
_nested = {f"{_CG}/cgroup.controllers": "cpu memory", f"{_CG}/a/b/memory.max": "max", f"{_CG}/a/b/cpu.max": "max 100000",
           f"{_CG}/a/memory.max": "2000000000", f"{_CG}/a/memory.current": "1500000000",
           f"{_CG}/a/memory.stat": "anon 1\ninactive_file 500000000\n", f"{_CG}/a/cpu.max": "150000 100000"}
_live_cores = (getattr(os, "process_cpu_count", None) or os.cpu_count)() or 1
_m1 = _machine("0::/a/b\n", _V2, _nested)
ok(_m1["ram_total_mb"] == 2000 and _m1["container_limit_mb"] == 2000 and _m1["ram_available_mb"] == 1000
   and "/a:" in _m1["ram_source"] and "inactive_file" in _m1["ram_source"],
   f"предел — у предка, своя группа «max»: доступно = предел − (memory.current − inactive_file) "
   f"({_m1['ram_available_mb']} из {_m1['ram_total_mb']} МБ, {_m1['ram_source']})")
ok(_m1["cpu_count"] == min(_live_cores, 2) and ("квота" in _m1["cpu_source"]) == (_live_cores > 2),
   f"квота у предка «150000 100000» — ceil, 2 ядра, меньшее из привязки и квоты ({_m1['cpu_count']} {_m1['cpu_source']})")
_m2 = _machine("0::/a/b\n", _V2, {**_nested, f"{_CG}/a/memory.stat": "anon 1\n"})
ok(_m2["ram_available_mb"] == 500 and "кэш не вычтен" in _m2["ram_source"],
   f"нет inactive_file — занятое как есть, и это названо ({_m2['ram_available_mb']} МБ, {_m2['ram_source']})")
_m3 = _machine("0::/\n", _V2, {f"{_CG}/cgroup.controllers": "memory", f"{_CG}/memory.max": "1000000000",
                               f"{_CG}/memory.current": "200000000", f"{_CG}/memory.stat": "inactive_file 100000000\n"})
ok(_m3["ram_total_mb"] == 1000 and _m3["ram_available_mb"] == 900 and "cgroup v2 /:" in _m3["ram_source"],
   f"контейнер с cgroupns («0::/») — предел у корня монтирования ({_m3['ram_available_mb']} из {_m3['ram_total_mb']} МБ)")
_m4 = _machine("0::/a/b (deleted)\n", _V2, {f"{_CG}/cgroup.controllers": "memory", f"{_CG}/a/b/memory.max": "1000000000",
                                            f"{_CG}/a/b/memory.current": "300000000",
                                            f"{_CG}/a/b/memory.stat": "inactive_file 100000000\n"})
ok(_m4["ram_available_mb"] == 800 and "/a/b:" in _m4["ram_source"],
   f"« (deleted)» снят с пути группы — предел своей группы найден ({_m4['ram_available_mb']} МБ, {_m4['ram_source']})")
_m5 = _machine("4:memory:/x\n3:cpu,cpuacct:/x\n",
               ["40 25 0:30 / /sys/fs/cgroup/memory rw - cgroup cgroup rw,memory",
                "41 25 0:31 / /sys/fs/cgroup/cpu,cpuacct rw - cgroup cgroup rw,cpu,cpuacct"],
               {f"{_CG}/memory/x/memory.limit_in_bytes": "1000000000", f"{_CG}/memory/x/memory.usage_in_bytes": "500000000",
                f"{_CG}/memory/x/memory.stat": "total_inactive_file 100000000\n",
                f"{_CG}/cpu,cpuacct/x/cpu.cfs_quota_us": "100000", f"{_CG}/cpu,cpuacct/x/cpu.cfs_period_us": "100000"})
ok(_m5["ram_total_mb"] == 1000 and _m5["ram_available_mb"] == 600 and "v1" in _m5["ram_source"],
   f"cgroup v1 — memory.limit_in_bytes − (usage_in_bytes − total_inactive_file) ({_m5['ram_source']})")
ok(_m5["cpu_count"] == 1 and "v1" in _m5["cpu_source"],
   f"cgroup v1 — квота cfs_quota_us / cfs_period_us ({_m5['cpu_count']} {_m5['cpu_source']})")
_m6 = _machine("0::/\n", _V2, {f"{_CG}/cgroup.controllers": "memory"},
               meminfo="MemTotal: 31250000 kB\nMemFree: 1000000 kB\nBuffers: 500000 kB\nCached: 2500000 kB\n")
ok(_m6["ram_available_mb"] == 4096 and "MemFree + Buffers + Cached" in _m6["ram_source"],
   f"нет MemAvailable — запасная формула названа ({_m6['ram_available_mb']} МБ, {_m6['ram_source']})")
_m7 = _machine("4:memory:/x\n", ["40 25 0:30 / /sys/fs/cgroup/memory rw - cgroup cgroup rw,memory"],
               {f"{_CG}/memory/x/memory.limit_in_bytes": "9223372036854771712",
                f"{_CG}/memory/x/memory.usage_in_bytes": "500000000",
                f"{_CG}/memory/x/memory.stat": "total_inactive_file 100000000\n"})
ok(_m7["container_limit_mb"] == 0 and _m7["ram_total_mb"] == 32000 and _m7["ram_available_mb"] == 20000,
   f"v1 «без предела» (число выше памяти машины) — не предел ({_m7['container_limit_mb']} МБ предела, "
   f"{_m7['ram_available_mb']} из {_m7['ram_total_mb']} МБ)")
_m8 = _machine("0::/a/b\n", _V2, {f"{_CG}/cgroup.controllers": "cpu", f"{_CG}/a/b/cpu.max": "200000 100000",
                                  f"{_CG}/a/cpu.max": "100000 100000"})
ok(_m8["cpu_count"] == 1 and "/a:" in _m8["cpu_source"],
   f"квоты на двух уровнях — берётся самая тесная, у предка ({_m8['cpu_count']} {_m8['cpu_source']})")
_ladder = Path(tempfile.mkdtemp())
(_ladder / "proc").mkdir()
try:
    os.process_cpu_count = lambda: None
    os.cpu_count = lambda: None
    sys.modules["psutil"] = None
    (_ladder / "sys/devices/system/cpu").mkdir(parents=True)
    (_ladder / "sys/devices/system/cpu/online").write_text("0-3,8\n", encoding="utf-8")
    (_ladder / "proc/cpuinfo").write_text("processor\t: 0\nprocessor\t: 1\nprocessor\t: 2\nprocessor\t: 3\n",
                                          encoding="utf-8")
    (_ladder / "proc/stat").write_text("cpu  1 2 3\ncpu0 1\ncpu1 1\ncpu2 1\nintr 5\n", encoding="utf-8")
    _rungs = []
    for _drop in ("sys/devices/system/cpu/online", "proc/cpuinfo", "proc/stat"):
        _h = probe(ROOT, _gpu_rules, proc=_ladder / "proc", fs_root=_ladder)
        _rungs.append((_h["cpu_count"], _h["cpu_source"]))
        (_ladder / _drop).unlink()
finally:
    os.cpu_count = _sys_cpus
    if _own_cpus:
        os.process_cpu_count = _own_cpus
    if _psutil_was is not None:
        sys.modules["psutil"] = _psutil_was
    else:
        sys.modules.pop("psutil", None)
ok(_rungs == [(5, "/sys/devices/system/cpu/online"), (4, "/proc/cpuinfo processor"), (3, "/proc/stat cpuN")],
   f"файлы ядра — ступени по очереди: online «0-3,8», затем cpuinfo, затем stat ({_rungs})")
ok(_hw["ram_available_mb"] <= _hw["ram_total_mb"],
   "доступно не больше общего — читается MemAvailable, а не выдуманное число")
ok(_hw["disk_free_mb"] is not None and _hw["disk_free_mb"] > 0, "свободное место под веса известно")
ok(_hw["gpu"] or any("НЕ проверено" in g for g in _hw["unknown"]),
   "видеокарты нет в ответе — значит сказано, что она НЕ проверена, а не «её нет»")
ok(all({"name", "driver", "vram_total_mb", "vram_free_mb", "usable", "why"} <= set(c)
       for c in _hw["gpu"]),
   f"карта описана целиком: имя, драйвер, её память и доступна ли она расчёту ({_hw['gpu']})")
ok(all(c["why"] for c in _hw["gpu"]),
   "у каждой карты названа ПРИЧИНА вердикта доступности — «нет» без причины неотличимо от сбоя")
ok(all(c["vram_free_mb"] <= c["vram_total_mb"] for c in _hw["gpu"] if c["vram_total_mb"])
   and all(c["vram_total_mb"] or any(c["name"] in g for g in _hw["unknown"]) for c in _hw["gpu"]),
   "объём памяти карты либо прочитан из sysfs без root, либо назван непрочитанным")

_est = FitEstimator({"dtype_bytes": {"F32": 4, "F16": 2}, "overhead": 1.0, "tight_ratio": 0.8})
ok(_est.bytes_for({"F32": 1_000_000_000}) == 4_000_000_000
   and _est.bytes_for({"F16": 1_000_000_000}) == 2_000_000_000,
   "разрядность решает: одна и та же модель в fp16 требует вдвое меньше")
ok(_est.bytes_for({}, params_total=1_000_000_000) == 4_000_000_000,
   "разрядность не названа → считаем по худшему, а не по удобному")
ok(_est.verdict(1_000_000_000, 8000)["verdict"] == "fits"
   and _est.verdict(7_000_000_000, 8000)["verdict"] == "tight"
   and _est.verdict(9_000_000_000, 8000)["verdict"] == "no",
   "три исхода: влезает / впритык / не влезает — пороги из декларации")
ok(_est.verdict(0, 8000)["verdict"] == "unknown"
   and "нечем" in _est.verdict(0, 8000)["why"],
   "нет числа параметров → «не знаю», а не «влезет»")
_zero_ram = _est.verdict(1_000_000_000, 0)
ok(_zero_ram["verdict"] == "no" and "при доступных 0 МБ" in _zero_ram["why"],
   f"доступно 0 МБ — «не влезает» с числами, а не «впритык» и не «неизвестно» ({_zero_ram['verdict']}: {_zero_ram['why']})")
ok("ОЦЕНКА" in _est.verdict(1_000_000_000, 8000)["why"],
   "вердикт назван оценкой, а не замером — иначе ему поверят как факту")

# Чем считать: карта или ОЗУ. Железо синтетическое — вердикт не должен зависеть от этой машины.
_amd = {"ram_available_mb": 30000, "gpu": [{"name": "карта", "driver": "amdgpu", "usable": False,
                                            "vram_total_mb": 16000, "vram_free_mb": 15000,
                                            "why": "сборка под другой ускоритель"}]}
_ok_gpu = {"ram_available_mb": 30000, "gpu": [{**_amd["gpu"][0], "usable": True, "why": "ок"}]}
ok(_est.pool(_amd) == {"target": "cpu", "device": "ОЗУ", "available_mb": 30000},
   "карта видна, но расчёту недоступна → считаем по ОЗУ: чужую память не обещаем")
ok(_est.pool(_ok_gpu)["target"] == "gpu" and _est.pool(_ok_gpu)["available_mb"] == 15000,
   "карта доступна → вердикт считается по ЕЁ памяти, а не по ОЗУ")
ok(_est.pool({"ram_available_mb": 8000})["available_mb"] == 8000,
   "карты нет вовсе → прежнее поведение, ОЗУ")
ok(_est.idle_gpu(_amd)["vram_free_mb"] == 15000 and not _est.idle_gpu(_ok_gpu),
   "простаивающая карта возвращается отдельно — иначе про её 15 ГБ никто не узнает")
ok("карта" in _est.verdict(1_000_000_000, 15000, "карта")["why"],
   "в вердикте названо, ПО КАКОЙ памяти он посчитан")

from core.providers.hardware import compute_device

_live = compute_device(_gpu_rules, hw=_ok_gpu | {"gpu": [{**_ok_gpu["gpu"][0], "driver": "amdgpu"}]})
ok(_live["device"] == "cuda" and _live["dtype"] == "float16",
   f"карта доступна → считаем на ней и в объявленной разрядности ({_live})")
ok(compute_device(_gpu_rules, hw=_amd)["device"] == "cpu"
   and "сборка" in compute_device(_gpu_rules, hw=_amd)["why"],
   "карта недоступна → процессор, и названа причина, а не молчание")
ok(compute_device(_gpu_rules, want="cpu", hw=_ok_gpu)["device"] == "cpu"
   and compute_device(_gpu_rules, want="cpu", hw=_ok_gpu)["source"] == "строка канала",
   "столбец строки перекрывает пробу: оператор знает про машину то, чего проба не видит")
ok(compute_device({}, hw=_ok_gpu)["device"] == "cpu",
   "драйвер не объявлен в декларации → процессор, а не угаданное имя устройства")

from core.providers.hardware import placement

_where_gpu = compute_device(_gpu_rules, hw=_ok_gpu | {"gpu": [{**_ok_gpu["gpu"][0], "driver": "amdgpu"}]})
_fit_rules = yaml.safe_load(CFG.read_text(encoding="utf-8"))["local"]["fit"]
_small = {"params_total": 500_000_000}
_huge = {"params_total": 20_000_000_000}
ok(placement(_gpu_rules, _fit_rules, _small, _where_gpu)["mode"] == "device",
   "влезает → кладём целиком, без лишних механизмов")
ok(placement(_gpu_rules, _fit_rules, _huge, _where_gpu)["mode"] == "model_offload",
   "не влезает → режим выгрузки ИЗ ДЕКЛАРАЦИИ, а не «возьми модель полегче»")
ok(placement({**_gpu_rules, "oversize": "error"}, _fit_rules, _huge, _where_gpu)["mode"] == "error",
   "объявили отказ — получаем отказ: молча считать вдесятеро дольше хуже, чем не начать")
ok(placement(_gpu_rules, _fit_rules, {}, _where_gpu)["mode"] == "device"
   and "неизвестен" in placement(_gpu_rules, _fit_rules, {}, _where_gpu)["why"],
   "размер неизвестен → кладём целиком и говорим об этом: гадать «не влезет» не лучше")

_gpu_rows = ModelCatalog(CFG, ROOT / "vendor" / "models").with_fit(
    [{"id": "тяжёлая", "params_by_dtype": {"F32": 3_700_000_000}}], _amd)
_f = _gpu_rows[0]["fit"]
ok(_f["device"] == "ОЗУ" and _f["on_gpu"]["available_mb"] == 15000 and _f["on_gpu"]["blocked_by"],
   f"строка каталога несёт оба числа: что на ОЗУ и что было бы на карте — с причиной ({_f})")
ok(_f["dtype"] == "float32" and _f["on_gpu"]["dtype"] == "float16"
   and _f["on_gpu"]["need_mb"] * 2 == _f["need_mb"],
   "память считается в той разрядности, в какой модель ПОДНИМУТ: на карте fp16 — ровно вдвое "
   f"меньше, чем fp32 на процессоре ({_f['need_mb']} → {_f['on_gpu']['need_mb']} МБ)")

_mc = ModelCatalog(CFG, ROOT / "vendor" / "models")
_fit_rows = _mc.with_fit([{"id": "тяжёлая", "params_by_dtype": {"F32": 20_000_000_000},
                           "params_total": 20_000_000_000},
                          {"id": "лёгкая", "params_by_dtype": {"F16": 100_000_000},
                           "params_total": 100_000_000}], _hw)
ok(_fit_rows[0]["fit"]["verdict"] == "no" and _fit_rows[1]["fit"]["verdict"] == "fits",
   f"вердикт приходит строкой каталога ({[r['fit']['verdict'] for r in _fit_rows]})")

# Единственный источник: опись. Рукописного каталога в конфиге больше нет.
_cfg_local = yaml.safe_load(CFG.read_text(encoding="utf-8"))["local"]
ok("catalog" not in _cfg_local,
   "каталога-декларации в конфиге нет: локальные модели приходят одним путём")
ok(_mc.entries() == _mc.inventory(),
   "что знает сервер о локальных моделях = опись поставленного, второго списка нет")

# Протокол загрузки весов — политика загрузчика `local.install.fetch`, а не переключатель клиента HF (`transfer`).
from core.declaration_schemas import FetchPolicy as _FetchPolicy

_fetch_decl = _cfg_local["install"]["fetch"]
ok(_fetch_decl["schemes"] == ["https"] and "transfer" not in _cfg_local["install"]
   and _mc.fetcher().policy == _FetchPolicy.model_validate(_fetch_decl),
   "веса тянет загрузчик по объявленной политике: только https, хосты точные, переключателя Xet нет")
_bare_cfg = _ws.parent / "providers_nofetch.yaml"
_bare_data = yaml.safe_load(CFG.read_text(encoding="utf-8"))
del _bare_data["local"]["install"]["fetch"]
_bare_cfg.write_text(yaml.safe_dump(_bare_data, allow_unicode=True), encoding="utf-8")
try:
    ModelCatalog(_bare_cfg, ROOT / "vendor" / "models").fetcher()
    ok(False, "без объявленной политики загрузчик не строится")
except ProviderError as _e_nofetch:
    ok(_e_nofetch.code == "DECLARATION_INVALID",
       f"нет `local.install.fetch` — отказ кодом, а не умолчание в коде ({_e_nofetch.code})")

_inst_tool = _call("media_models", scope="installed")
ok(_inst_tool.data["hardware"]["ram_available_mb"] > 0,
   "инструмент отдаёт снимок железа рядом со списком — иначе числа параметров не с чем сравнить")
ok(all("fit" in m for m in _inst_tool.data["models"]),
   "у каждой поставленной модели есть вердикт пригодности")

print("== 12. Переработка готового файла: удаление фона и апскейл (S24) ==")
_pcfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
ok(_pcfg["resources"]["by_resource"]["bg_removals"]["input"] == "file"
   and _pcfg["resources"]["by_resource"]["upscales"]["input"] == "file",
   "чем является input для вида ресурса — объявлено, а не разобрано ветками в коде")
for _kind in ("bg_removal", "upscale"):
    _src = _pcfg["local"]["sources"][_kind]
    ok(".onnx" in (_src.get("require_formats") or []),
       f"{_kind}: показываем только то, что установщик вправе поставить (формат объявлен)")

from PIL import Image as _PILImage

from core.providers.catalog import ModelCatalog as _MC

_cat12 = _MC(CFG, _reg.models_dir)
ok(_cat12.prefer(["onnx/model_fp16.onnx", "onnx/model.onnx"]) == "onnx/model.onnx",
   "из одинаковых по смыслу файлов берётся объявленный первым, а не первый попавшийся")
ok(str(_cat12.cache_dir).startswith(str(_reg.models_dir)),
   f"загрузчик складывает скачанное внутрь каталога зависимостей проекта ({_cat12.cache_dir})")

_installed12 = {m["id"]: m for m in _cat12.installed() if m["disk"]["verdict"] == "ready"}
_bg_model = next((m for m in _installed12.values() if m["kind"] == "bg_removal"), None)
_up_model = next((m for m in _installed12.values() if m["kind"] == "upscale"), None)

(_ws / "pic").mkdir()
_PILImage.new("RGB", (64, 48), "#3070c0").save(_ws / "pic" / "frame.png")
_row_bg = {"resource_type": "bg_removals", "provider": "Local_onnx_bg", "fallback_provider": "",
           "daily_limit": -1, "current_usage": 0, "warning_threshold": -1,
           "model": (_bg_model or {}).get("id", ""), "input_size": 320, "usage_unit": "call"}
_row_up = {"resource_type": "upscales", "provider": "Local_onnx_upscale", "fallback_provider": "",
           "daily_limit": -1, "current_usage": 0, "warning_threshold": -1,
           "model": (_up_model or {}).get("id", ""), "tile": 0, "usage_unit": "call"}
(_ws / "pic" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
    "B1": _row_bg, "U1": _row_up}}}), encoding="utf-8")

# Путь исходника приходит от ИИ: он обязан пройти containment той же дверью, что и запись.
_esc12 = _call("media_generate", table="pic", resource_type="bg_removals",
               input="../../../../etc/passwd", scene_id="s1")
ok(_esc12.status == "error" and _esc12.error.code in ("PATH_ESCAPE", "FILE_NOT_FOUND"),
   f"путь исходника наружу рабочей области не читается ({_esc12.error.code if _esc12.error else 'успех!'})")

_miss12 = _call("media_generate", table="pic", resource_type="bg_removals",
                input="pic/нет-такой.png", scene_id="s1")
ok(_miss12.status == "error" and _miss12.error.code == "FILE_NOT_FOUND",
   f"нет исходника — отказ кодом реестра, а не пустой файл ({_miss12.error.code if _miss12.error else 'успех!'})")

if _bg_model:
    _bg12 = _call("media_generate", table="pic", resource_type="bg_removals",
                  input="pic/frame.png", scene_id="s1")
    ok(_bg12.status == "success", f"фон снят локальной моделью ({_bg12.error.code if _bg12.error else 'ok'})")
    if _bg12.status == "success":
        _out12 = _ws / _bg12.data["files"][0]
        ok(_out12.name == "frame_nobg.png",
           f"имя собрано от ИСХОДНИКА, а не от сцены ({_out12.name})")
        ok(_PILImage.open(_out12).mode == "RGBA",
           "результат несёт прозрачность — вырезан фон, а не перерисован кадр")
        ok(_bg12.data["compute"].get("device") == "cpu" and _bg12.data["compute"].get("runtime"),
           f"сказано, где считалось ({_bg12.data['compute'].get('runtime')})")
        _u12 = _json.loads((_ws / "pic" / "read.json").read_text())["RESOURCE_LIMITS"]["rows"]["B1"]
        ok(_bg12.data["usage"]["charged"] and _u12["current_usage"] == 1,
           f"расход переработки учтён строкой канала ({_u12['current_usage']})")
else:
    print("  ⚠ модель удаления фона не поставлена (media_model_install) — живой прогон пропущен")

if _up_model:
    _up12 = _call("media_generate", table="pic", resource_type="upscales",
                  input="pic/frame.png", scene_id="s1")
    ok(_up12.status == "success", f"апскейл исполнен ({_up12.error.code if _up12.error else 'ok'})")
    if _up12.status == "success":
        _big = _PILImage.open(_ws / _up12.data["files"][0])
        ok(_big.size[0] > 64 and _big.size[0] % 64 == 0,
           f"картинка выросла кратно, а не «как получилось» ({_big.size})")
else:
    print("  ⚠ модель апскейла не поставлена (media_model_install) — живой прогон пропущен")

# Платный онлайн: ключ — свойство провайдера, объявленное сервером, а не решение данных канала.
_http12 = _reg.load("RemoveBg", "bg_removals")
ok(_reg.requires_key("RemoveBg", "bg_removals") and _reg.requires_key("Fal", "upscales"),
   "платным «файл → файл» ключ объявлен обязательным на стороне сервера")
try:
    _http12.generate(MediaRequest(input="pic/frame.png", params={}, target=_ws / "x.png",
                                   models_dir=_reg.models_dir, source=_ws / "pic" / "frame.png",
                                   provider="RemoveBg"))
    ok(False, "вызов без ключа обязан отказать, а не уйти в сеть")
except ProviderError as _e12:
    ok(_e12.code == "PROVIDER_KEY_MISSING",
       f"без ключа — отказ до обращения к провайдеру ({_e12.code})")
try:
    _http12._decl("Неизвестный")
    ok(False, "неизвестный провайдер обязан отказать")
except ProviderError as _e13:
    ok(_e13.code == "PROVIDER_ADAPTER_MISSING",
       f"необъявленного провайдера сервер не вызывает ({_e13.code})")

print("== 13. Платные агрегаторы: стенд по их контракту (ключей нет — есть их API-контракт) ==")
# Живой платный вызов упирается в аккаунт, а не в код, поэтому проверяется НАШ путь против
# стенда, отвечающего ровно так, как описано у самих провайдеров (эндпоинты, поля и статусы взяты
# из официального клиента Replicate и fal_client). Мок здесь законен: это истинно внешний сервис.
import httpx as _httpx

_seen = {"requests": [], "polls": 0}
_PNG = (_ws / "pic" / "frame.png").read_bytes()


class _Resp:
    def __init__(self, status=200, data=None, content=b"", headers=None):
        self.status_code, self._data = status, data
        self.content, self.headers = content, headers or {}

    def json(self):
        if self._data is None:
            raise ValueError("не JSON")
        return self._data

    @property
    def text(self):
        return _json.dumps(self._data or {})

    # Потоком — как `httpx.stream`: ответ статуса читается по ходу, с пределом байт.
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.content

    def iter_bytes(self, size=0):
        yield self.content if self._data is None else _json.dumps(self._data).encode()


class _Stream:
    def __init__(self, payload): self.status_code, self._payload = 200, payload
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def iter_bytes(self, size=0): yield self._payload


_real = {"request": _httpx.request, "get": _httpx.get, "stream": _httpx.stream}


def _stand_request(method, url, **kw):
    _seen["requests"].append({"method": method, "url": url, "headers": kw.get("headers") or {},
                              "json": kw.get("json"), "files": kw.get("files")})
    if "replicate.com" in url:
        return _Resp(201, {"id": "pred1", "status": "starting",
                           "urls": {"get": "https://api.replicate.com/v1/predictions/pred1"}})
    if "queue.fal.run" in url:
        return _Resp(200, {"request_id": "req1",
                           "status_url": "https://queue.fal.run/req1/status",
                           "response_url": "https://queue.fal.run/req1"})
    return _Resp(404, {"detail": "нет такого"})


def _stand_get(url, **kw):
    # Адреса — точно те, что стенд выдал в ответе задачи: подстрока узнала бы и не-адрес.
    _seen.setdefault("gets", []).append({"url": url, "headers": kw.get("headers") or {}})
    if url == "https://queue.fal.run/req1/status":
        return _Resp(200, {"status": "COMPLETED"})
    if url == "https://api.replicate.com/v1/predictions/pred1":
        _seen["polls"] += 1
        return _Resp(200, {"status": "processing"} if _seen["polls"] < 2 else
                     {"status": "succeeded", "output": ["https://replicate.delivery/out.png"]})
    if url == "https://queue.fal.run/req1":
        return _Resp(200, {"image": {"url": "https://fal.media/out.png"}})
    if url == "https://api.replicate.com/v1/predictions/huge":
        return _Resp(200, {"status": "processing", "logs": "x" * 1_100_000})
    if url == "https://api.replicate.com/v1/predictions/gone":
        return _Resp(404, {"detail": "prediction not found"})
    if url == "https://api.replicate.com/v1/predictions/moved":     # переход идёт, только если вызов его просит
        moved = _Resp(302, headers={"location": "http://127.0.0.1:9/internal"})
        return _stand_get(moved.headers["location"], **kw) if kw.get("follow_redirects") else moved
    if url == "http://127.0.0.1:9/internal":
        return _Resp(200, {"status": "succeeded", "output": ["внутреннее"]})
    return _Resp(404, {"detail": f"стенд не выдавал адрес {url[:80]}"})


def _stand_file(request):
    """CDN результата поставщика: файл по ссылке из ответа задачи; журнал — хост и заголовок ключа."""
    _seen.setdefault("files", []).append({"host": request.headers.get("host"), "auth": request.headers.get("authorization")})
    return _httpx.Response(200, content=_PNG)


from core.providers.img.http_image import HttpImageAPI as _HI13

_real_seams13 = (_HI13.__dict__["transport"], _HI13.__dict__["resolve"])
_HI13.transport = _httpx.MockTransport(_stand_file)
_HI13.resolve = staticmethod(lambda host, port, *a, **k: [(2, 1, 6, "", ("151.101.1.1", port))])
_httpx.request, _httpx.get, _httpx.stream = (
    _stand_request, _stand_get, lambda method, url, **kw: _stand_get(url, **kw))
try:
    _rows13 = {"resource_type": "upscales", "provider": "Replicate", "fallback_provider": "",
               "daily_limit": 100, "current_usage": 0, "warning_threshold": -1,
               "model": "owner/real-esrgan:abc123", "scale": 2, "response_format": "png",
               "retry_count": 1, "retry_delay": 0, "usage_unit": "image"}
    (_ws / "pic" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
        "R13": _rows13}}}), encoding="utf-8")
    _ad13 = _reg.load("Replicate", "upscales")
    _out13 = _ad13.generate(MediaRequest(input="pic/frame.png", params=_rows13,
                                         target=_ws / "up13.png", models_dir=_reg.models_dir,
                                         source=_ws / "pic" / "frame.png", api_key="secret-key",
                                         provider="Replicate"))
    _req13 = _seen["requests"][-1]
    ok(_out13.task_id == "pred1" and not _out13.meta["sync"],
       f"ответ-задача разобран: id и адрес прозвонки взяты из ответа ({_out13.task_id})")
    ok(_req13["headers"].get("Authorization") == "Bearer secret-key",
       "ключ уходит в объявленной форме заголовка")
    ok(_req13["json"]["version"] == "owner/real-esrgan:abc123"
       and str(_req13["json"]["input"]["image"]).startswith("data:image/png;base64,")
       and _req13["json"]["input"]["scale"] == 2,
       "тело собрано по контракту провайдера: вложенный input, файл как data-URI, поля из строки")

    _cycle13 = TaskCycle(ROOT / "config" / "media_tasks.yaml", sleep=lambda _s: None)
    _waited13 = _cycle13.wait(_ad13.poll, _out13.task_id)
    ok(_waited13["outcome"] == "done" and _waited13["attempts"] == 2,
       f"промежуточный статус не выдан за успех — ждали до конца ({_waited13['attempts']} опроса)")
    _gets13 = len(_seen["gets"])
    try:
        _got13 = _ad13.fetch(_waited13["answer"], _ws / "up13.png")
    except ProviderError as e:
        _got13 = e
    ok(isinstance(_got13, Path) and _got13.read_bytes() == _PNG and len(_seen["gets"]) == _gets13,
       "результат достан по объявленному пути output[0] и скачан общими правилами загрузки; "
       f"отдельного адреса результата у Replicate нет — лишних запросов {len(_seen['gets']) - _gets13}"
       f"{'' if isinstance(_got13, Path) else f', отказ {_got13.code} {_got13.facts}'}")
    _files13 = _seen.get("files") or [{}]
    ok(_files13[-1].get("host") == "replicate.delivery" and _files13[-1].get("auth") is None,
       f"файл результата забран по ссылке поставщика общим загрузчиком и без ключа ({_files13[-1]})")

    # Очередь fal: статус говорит лишь «готово», результат лежит отдельным адресом.
    _ad13f = _reg.load("Fal", "upscales")
    _outf = _ad13f.generate(MediaRequest(input="pic/frame.png",
                                         params={"model": "fal-ai/clarity-upscaler"},
                                         target=_ws / "up13f.png", models_dir=_reg.models_dir,
                                         source=_ws / "pic" / "frame.png", api_key="k",
                                         provider="Fal", ))
    ok(_seen["requests"][-1]["url"] == "https://queue.fal.run/fal-ai/clarity-upscaler",
       f"модель из строки канала подставлена в адрес ({_seen['requests'][-1]['url']})")
    ok(_seen["requests"][-1]["headers"].get("Authorization") == "Key k",
       "у fal своя форма ключа — и она объявлена, а не зашита")
    ok(_ad13f.fetch(_ad13f.poll(_outf.task_id), _ws / "up13f.png").read_bytes() == _PNG,
       "результат очереди забран вторым адресом (response_url), а не из статуса")

    # Ключ уходит только на объявленный хост: адрес статуса и результата называет сам ответ провайдера.
    _keyed13 = [g for g in _seen["gets"] if "api.replicate.com" in g["url"] or "queue.fal.run" in g["url"]]
    ok(_keyed13 and {g["headers"].get("Authorization") for g in _keyed13} == {"Bearer secret-key", "Key k"},
       f"законный: прозвонка и результат на объявленном хосте идут с ключом ({len(_keyed13)} запросов)")
    for _case13, _fal13, _foreign13, _host13 in (
            ("чужой хост", False, "https://evil.example/v1/predictions/p", "evil.example"),
            ("http вместо https", False, "http://api.replicate.com/v1/predictions/p", "api.replicate.com"),
            ("объявленный хост как имя пользователя", False, "https://api.replicate.com@evil.example/p", "evil.example"),
            ("другой порт", False, "https://api.replicate.com:8443/p", "api.replicate.com:8443"),
            ("объявленный хост как начало чужого", False, "https://api.replicate.com.evil.example/p",
             "api.replicate.com.evil.example"),
            ("результат очереди на чужом хосте", True, "https://evil.example/req1", "evil.example")):
        _seen["gets"].clear()
        _httpx.request = (lambda u: lambda method, url, **kw: _Resp(201, {
            "id": "p", "request_id": "req1", "urls": {"get": u},
            "status_url": "https://queue.fal.run/req1/status", "response_url": u}))(_foreign13)
        _prov13 = "Fal" if _fal13 else "Replicate"
        _ad = _reg.load(_prov13, "upscales")
        _task = _ad.generate(MediaRequest(input="pic/frame.png", params={"model": "m"} if _fal13 else _rows13,
                                          target=_ws / "k13.png", models_dir=_reg.models_dir,
                                          source=_ws / "pic" / "frame.png", api_key="secret-key", provider=_prov13))
        try:
            _ad.fetch(_ad.poll(_task.task_id), _ws / "k13.png") if _fal13 else _ad.poll(_task.task_id)
            _e13k = None
        except ProviderError as e:
            _e13k = e
        _leaked13 = [g["url"] for g in _seen["gets"] if "secret-key" in str(g["headers"])
                     and not g["url"].startswith("https://queue.fal.run/")]
        ok(_e13k is not None and _e13k.code == "DOWNLOAD_FORBIDDEN" and not _leaked13
           and _e13k.facts == {"host": _host13, "allowed": "queue.fal.run" if _fal13 else "api.replicate.com"},
           f"{_case13}: ключ не ушёл ({_leaked13 or 'запросов с ключом нет'}), отказ "
           f"{_e13k and _e13k.code} {_e13k and _e13k.facts}")
    _httpx.request = _stand_request

    # Ответ статуса читается по ходу и не больше объявленного предела (download.answer.max_bytes, 1 МБ).
    _httpx.request = lambda method, url, **kw: _Resp(201, {
        "id": "h", "urls": {"get": "https://api.replicate.com/v1/predictions/huge"}})
    _adh = _reg.load("Replicate", "upscales")
    _taskh = _adh.generate(MediaRequest(input="pic/frame.png", params=_rows13, target=_ws / "h13.png",
                                        models_dir=_reg.models_dir, source=_ws / "pic" / "frame.png",
                                        api_key="k", provider="Replicate"))
    try:
        _adh.poll(_taskh.task_id)
        _eh13 = None
    except ProviderError as e:
        _eh13 = e
    ok(_eh13 is not None and _eh13.code == "DOWNLOAD_TOO_LARGE" and _eh13.facts == {"limit_mb": 1.0},
       f"ответ статуса больше объявленного предела — DOWNLOAD_TOO_LARGE, а не простыня в памяти "
       f"({_eh13 and (_eh13.code, _eh13.facts)})")
    _httpx.request = lambda method, url, **kw: _Resp(201, {
        "id": "g", "urls": {"get": "https://api.replicate.com/v1/predictions/gone"}})
    _adg = _reg.load("Replicate", "upscales")
    _taskg = _adg.generate(MediaRequest(input="pic/frame.png", params=_rows13, target=_ws / "g13.png",
                                        models_dir=_reg.models_dir, source=_ws / "pic" / "frame.png",
                                        api_key="k", provider="Replicate"))
    try:
        _ansg13, _eg13 = _adg.poll(_taskg.task_id), None
    except ProviderError as e:
        _ansg13, _eg13 = None, e
    ok(_eg13 is not None and _eg13.code == "PROVIDER_FAILED" and "404" in _eg13.message,
       f"отказ на прозвонке — кодом с ответом поставщика, а не тело отказа под видом статуса "
       f"({_eg13 and (_eg13.code, _eg13.message[:60])}{'' if _eg13 else f', ответ {_ansg13}'})")
    _httpx.request = lambda method, url, **kw: _Resp(201, {
        "id": "m", "urls": {"get": "https://api.replicate.com/v1/predictions/moved"}})
    _adm = _reg.load("Replicate", "upscales")
    _taskm = _adm.generate(MediaRequest(input="pic/frame.png", params=_rows13, target=_ws / "m13.png",
                                        models_dir=_reg.models_dir, source=_ws / "pic" / "frame.png",
                                        api_key="secret-key", provider="Replicate"))
    _seen["gets"].clear()
    try:
        _ansm13, _em13 = _adm.poll(_taskm.task_id), None
    except ProviderError as e:
        _ansm13, _em13 = None, e
    _inner13 = [g["url"] for g in _seen["gets"] if "127.0.0.1" in g["url"]]
    ok(_em13 is not None and _em13.code == "PROVIDER_FAILED"
       and _em13.facts == {"host": "api.replicate.com", "status": 302} and not _inner13,
       f"прозвонка ответила 302 на внутренний адрес — отказ, по переходу с ключом не пошли "
       f"({_em13 and (_em13.code, _em13.facts)}, запросы туда: {_inner13}{'' if _em13 else f', ответ {_ansm13}'})")
    _httpx.request = _stand_request

    # Синхронный бинарный ответ (RemoveBg): тип по формату строки, поток в остаток с пределом, обрыв POST — не повтор.
    import contextlib as _ctx13
    import core.providers.img.http_image as _him13
    from core.contracts import ContractError as _CE13
    from core.providers.download import Fetcher as _F13

    _posts13: list = []

    def _stream13(chunks, kind="image/png", cut=None):
        @_ctx13.contextmanager
        def stream(method, url, **kw):
            _posts13.append((method, (kw.get("headers") or {}).get("Accept-Encoding")))

            def body():
                sent = 0
                for chunk in chunks:
                    if cut is not None and sent >= cut:
                        raise _httpx.ReadError("соединение оборвано")
                    sent += len(chunk)
                    yield chunk
            yield _httpx.Response(200, headers={"content-type": kind}, content=body(),
                                  request=_httpx.Request(method, url))
        return stream

    _adrb = _reg.load("RemoveBg", "bg_removals")

    def _remove_bg(target, fmt="png"):
        _posts13.clear()
        try:
            _adrb.generate(MediaRequest(input="pic/frame.png", params={"response_format": fmt}, target=target,
                                        models_dir=_reg.models_dir, source=_ws / "pic" / "frame.png",
                                        api_key="k", provider="RemoveBg"))
        except _CE13 as e:
            return e
        except Exception as e:  # мутант или поломка — улика проверки, а не обрыв набора
            return _CE13("НЕ_ОТКАЗ", facts={"exception": f"{type(e).__name__}: {e}"})
        return None

    def _left13(target) -> bool:
        return target.exists() or _him13.Files(target.parent).remnant(target.name, "result").exists()

    _real_stream13, _real_results13 = _httpx.stream, _him13.results
    _png13 = b"\x89PNG\r\n\x1a\n" + b"p" * 3000
    _parts13 = [_png13[:1024], _png13[1024:2048], _png13[2048:]]
    try:
        _httpx.stream = _stream13(_parts13)
        _e13b = _remove_bg(_ws / "rb13.png")
        ok(_e13b is None and (_ws / "rb13.png").read_bytes() == _png13 and not _him13.Files(_ws).remnant(
           "rb13.png", "result").exists() and _posts13 == [("POST", "identity")],
           f"бинарный ответ встал под имя целиком, остатка нет, POST один и без распаковки ({_e13b}, {_posts13})")
        _stale13 = _him13.Files(_ws).remnant("rb13s.png", "result")
        _stale13.write_bytes(b"STALE-TAIL" * 50)          # хвост прерванного прошлого приёма
        _httpx.stream = _stream13(_parts13)
        _e13b = _remove_bg(_ws / "rb13s.png")
        ok(_e13b is None and (_ws / "rb13s.png").read_bytes() == _png13 and not _stale13.exists(),
           f"чужой хвост в остатке перед приёмом — под именем ровно новое тело, байт в байт ({_e13b})")
        _httpx.stream = _stream13(_parts13, kind="text/html")
        _e13b = _remove_bg(_ws / "rb13h.png")
        ok(_e13b is not None and _e13b.code == "PROVIDER_FAILED" and _e13b.facts.get("status") == "Content-Type text/html"
           and not _left13(_ws / "rb13h.png"), f"HTML вместо картинки — отказ до записи, файла нет ({_e13b and _e13b.facts})")
        _him13.results = lambda cfg, **seams: _F13(
            _real_results13(cfg, **seams).policy.model_copy(update={"max_bytes": 1024}), **seams)
        _httpx.stream = _stream13(_parts13)
        _e13b = _remove_bg(_ws / "rb13l.png")
        ok(_e13b is not None and _e13b.code == "DOWNLOAD_TOO_LARGE" and not _left13(_ws / "rb13l.png"),
           f"тело сверх предела загрузчика результатов — отказ, ни файла, ни остатка ({_e13b and _e13b.code})")
        _him13.results = _real_results13
        _httpx.stream = _stream13(_parts13, cut=1024)
        _e13b = _remove_bg(_ws / "rb13c.png")
        ok(_e13b is not None and _e13b.code == "DOWNLOAD_INCOMPLETE" and _e13b.facts.get("got") == 1024
           and len(_posts13) == 1 and not _left13(_ws / "rb13c.png"),
           f"обрыв посреди тела POST — «не дочитан» без повтора, ни файла, ни остатка "
           f"({_e13b and (_e13b.code, _e13b.facts)}, POST: {len(_posts13)})")
        _e13b = _remove_bg(_ws / "rb13t.png", fmt="tiff")
        ok(_e13b is not None and _e13b.code == "SCHEMA_INVALID" and not _posts13,
           f"формат строки без объявленного типа — отказ объявления до запроса ({_e13b and _e13b.code}, POST: {_posts13})")
    finally:
        _httpx.stream, _him13.results = _real_stream13, _real_results13

    # Повтор: перегрузку повторяем, отказ по ключу — нет (иначе жжём платный лимит).
    _tries = {"n": 0}

    def _flaky(method, url, **kw):
        _tries["n"] += 1
        return _Resp(429, {"detail": "rate limited"}, headers={"Retry-After": "0"}) if _tries["n"] == 1 \
            else _stand_request(method, url, **kw)

    _httpx.request = _flaky
    _ad13r = _reg.load("Replicate", "upscales")
    _ad13r.generate(MediaRequest(input="pic/frame.png", params={**_rows13, "retry_count": 2},
                                 target=_ws / "up13r.png", models_dir=_reg.models_dir,
                                 source=_ws / "pic" / "frame.png", api_key="k", provider="Replicate"))
    ok(_tries["n"] == 2, f"перегрузка (429) повторена по столбцу retry_count строки канала ({_tries['n']})")

    _httpx.request = lambda method, url, **kw: _Resp(401, {"detail": "bad token"})
    _calls401 = {"n": 0}

    def _count401(method, url, **kw):
        _calls401["n"] += 1
        return _Resp(401, {"detail": "bad token"})

    _httpx.request = _count401
    try:
        _reg.load("Replicate", "upscales").generate(MediaRequest(
            input="pic/frame.png", params={**_rows13, "retry_count": 3}, target=_ws / "x.png",
            models_dir=_reg.models_dir, source=_ws / "pic" / "frame.png", api_key="bad",
            provider="Replicate"))
        ok(False, "неверный ключ обязан отказать")
    except ProviderError as _e401:
        ok(_e401.code == "AUTH_FAILED" and _calls401["n"] == 1,
           f"отказ по ключу не повторяется — платный лимит не жжём ({_e401.code}, попыток {_calls401['n']})")
finally:
    _httpx.request, _httpx.get, _httpx.stream = _real["request"], _real["get"], _real["stream"]
    _HI13.transport, _HI13.resolve = _real_seams13

# Предел встроенной отправки: base64 раздувает файл, и большой кадр так не уедет.
_big = _ws / "pic" / "big.png"
_PILImage.new("RGB", (2000, 2000), "#808080").save(_big)
_decl13 = yaml.safe_load(CFG.read_text(encoding="utf-8"))["online"]["http"]["Replicate"]
ok(float(_decl13.get("max_inline_mb") or 0) > 0, "у встроенной отправки объявлен предел размера")
try:
    _reg.load("Replicate", "upscales")._body(
        {**_decl13, "max_inline_mb": 0.001},
        MediaRequest(input="x", params={}, target=_ws / "x.png", models_dir=_reg.models_dir,
                     source=_big, api_key="k", provider="Replicate"))
    ok(False, "файл сверх предела обязан отказать до отправки")
except ProviderError as _ebig:
    ok(_ebig.code == "CONTENT_REJECTED", f"файл сверх предела отклонён до сети ({_ebig.code})")

print("== 14. Что модель умеет: спека параметров и отказ ДО вызова (S24) ==")
from core.providers import ModelSpec

_ms = ModelSpec(_reg)

# Адрес спеки объявлен не https — отказ до сети: ключ в открытый канал не уходит.
import httpx as _hx14
import types as _ty14

_ms_http = ModelSpec(_ty14.SimpleNamespace(models_dir=_reg.models_dir, config={"capabilities": {"online": {
    "Двойник": {"url": "http://spec.example/{model}", "auth": "bearer"}}}}))
_asked14, _real_get14, _e14 = [], _hx14.get, None
_hx14.get = lambda *a, **k: _asked14.append(a)
try:
    _ms_http.of("Двойник", "модель", api_key="ключ", refresh=True)
except ProviderError as e:
    _e14 = e
finally:
    _hx14.get = _real_get14
_said14 = _Eng(reactions=_REACTIONS).refusal(_e14).error.message if _e14 else ""
ok(_e14 is not None and _e14.code == "DOWNLOAD_FORBIDDEN" and not _asked14,
   f"адрес спеки не https — отказ до сети, ключ не ушёл ({_e14 and _e14.code}, запросов {len(_asked14)})")
ok("spec.example" in _said14 and "https" in _said14,
   f"клиент видит, какой адрес спеки не пропущен и какая схема разрешена ({_said14})")

# Локальная модель описывает себя сама — сетью для этого ходить незачем.
# Веса вне git: на машине без них проверять нечего, и это ПРОПУСК с причиной, а не провал —
# иначе набор зелёный только там, где модель уже поставлена (в CI он падал именно так).
_sp_local = _ms.of("Local_diffusers", "sdxl-turbo")
if _sp_local["source"] == "model_files":
    ok(_sp_local["known"], f"локальная модель описана своими файлами, без сети ({_sp_local['source']})")
    _props = _sp_local["schema"].get("properties") or {}
    ok("512x512" in str(_props.get("img_size", {}).get("default")),
       f"нативное разрешение прочитано из модели ({_props.get('img_size', {}).get('default')})")
else:
    print("  ⚠ веса sdxl-turbo не поставлены (media_model_install) — спека из файлов не проверена")

# Неизвестное НЕ выдаётся за «ограничений нет» — иначе пустота выглядела бы как разрешение.
_sp_none = _ms.of("Local_piper", "нет-такой-модели")
ok(_sp_none["source"] == "none" and _sp_none["known"] is False and _sp_none["why"],
   "нет спеки — так и сказано, а не «ограничений нет»")
ok(_ms.check(_sp_none, {"что_угодно": 4096}) == [],
   "без спеки сверка никого не блокирует: неизвестность не повод отказывать")

# Сверка по схеме: пределы модели против того, что просит строка канала.
_schema14 = {"schema": {"type": "object", "properties": {
    "operating_resolution": {"type": "string", "enum": ["1024x1024", "2048x2048"]},
    "steps": {"type": "integer", "minimum": 1, "maximum": 8}}}, "source": "book", "known": True}
_gaps14 = _ms.check(_schema14, {"operating_resolution": "4096x4096", "steps": 50, "tile": 256})
ok(len(_gaps14) == 2 and {g["param"] for g in _gaps14} == {"operating_resolution", "steps"},
   f"расхождения найдены по обоим полям ({[g['param'] for g in _gaps14]})")
ok(all(g["allowed"] for g in _gaps14),
   "в отказе названо ДОПУСТИМОЕ, иначе ИИ чинит вслепую")
ok(not any(g["param"] == "tile" for g in _gaps14),
   "наши служебные столбцы строки не объявлены моделью и не считаются нарушением")

# Сквозь инструмент: спека приходит ИИ с источником и параметрами. Спека читается ИЗ ФАЙЛОВ
# модели, поэтому без поставленных весов проверять нечего — пропуск с причиной.
_spec_tool = _call("media_models", scope="spec", provider="Local_diffusers", model="sdxl-turbo")
if _spec_tool.status == "success" and _spec_tool.data.get("source") == "model_files":
    ok(bool(_spec_tool.data["parameters"]),
       f"инструмент отдаёт спеку с источником ({_spec_tool.data.get('source')})")
    ok(_spec_tool.facts and _spec_tool.facts[0].type == "ModelSpecRead",
       "чтение спеки приходит фактом контракта (тип заведён в KNOWN_FACT_TYPES)")

    # И главное: вызов не состоится, если строка канала просит невозможное.
    (_ws / "spec14").mkdir()
    _PILImage.new("RGB", (32, 32), "#101010").save(_ws / "spec14" / "in.png")
    (_ws / "spec14" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
        "S1": {"resource_type": "image_generations", "provider": "Local_diffusers",
               "fallback_provider": "", "daily_limit": -1, "current_usage": 0,
               "warning_threshold": -1, "model": "sdxl-turbo", "img_size": 4096}}}}), encoding="utf-8")
    _deny14 = _call("media_generate", table="spec14", resource_type="image_generations",
                    input="кадр", scene_id="s1")
    ok(_deny14.status == "error" and _deny14.error.code == "VALIDATION_ERROR",
       f"строка канала просит невозможное — отказ ДО вызова ({_deny14.error.code if _deny14.error else 'успех!'})")
    ok("4096" in (_deny14.error.message if _deny14.error else ""),
       "в отказе названо, ЧТО именно не подошло")
else:
    print("  ⚠ веса sdxl-turbo не поставлены — спека сквозь инструмент и отказ ДО вызова не проверены")

print("== 15. Поднятая модель переживает вызов: пул с бюджетом (S24) ==")
import time as _time

from core.providers.pool import ModelPool as _Pool
import core.providers.pool as _pool_mod

_pool_mod._ENTRIES.clear()
_hw15 = {"ram_available_mb": 1000, "gpu": []}
_lifts = {"n": 0}


def _lift(tag="x"):
    def _loader():
        _lifts["n"] += 1
        return {"model": tag, "n": _lifts["n"]}
    return _loader


_p15 = _Pool({"enabled": True, "budget_ratio": 0.5, "idle_ttl_sec": 0, "max_entries": 0}, _hw15)
_a = _p15.get("m1", _lift("a"), need_bytes=100_000_000, device="cpu")
_b = _p15.get("m1", _lift("a"), need_bytes=100_000_000, device="cpu")
ok(_a is _b and _lifts["n"] == 1,
   f"вторая просьба о той же модели не поднимает её заново ({_lifts['n']} подъём)")

# Бюджет — доля СВОБОДНОЙ памяти: 1000 МБ × 0.5 = 500 МБ, третья модель по 300 МБ не влезет.
_pool_mod._ENTRIES.clear(); _lifts["n"] = 0
_p15.get("big1", _lift("b1"), need_bytes=300_000_000, device="cpu")
_p15.get("big2", _lift("b2"), need_bytes=300_000_000, device="cpu")
_held = {r["key"] for r in _p15.stats()["models"]}
ok(_held == {"big2"},
   f"не влезающее вытеснено по бюджету, а не сложено сверх памяти ({sorted(_held)})")
ok(_p15.stats()["budget_mb"]["cpu"] == 500,
   f"бюджет считается от свободной памяти и объявленной доли ({_p15.stats()['budget_mb']})")

# Свободно 0 — бюджет 0, а не «без предела»: прежняя модель выгружается до подъёма новой.
_pool_mod._ENTRIES.clear(); _lifts["n"] = 0
_p_zero = _Pool({"enabled": True, "budget_ratio": 0.5, "idle_ttl_sec": 0, "max_entries": 0},
                {"ram_available_mb": 0, "gpu": []})
_p_zero.get("z1", _lift("z1"), need_bytes=100_000_000, device="cpu")
_p_zero.get("z2", _lift("z2"), need_bytes=100_000_000, device="cpu")
_held0 = {r["key"] for r in _p_zero.stats()["models"]}
ok(_held0 == {"z2"},
   f"свободной памяти 0 — бюджет 0 судится: прежняя модель выгружена, а не держится без предела ({sorted(_held0)})")

# Простой: залежавшееся убирается при СЛЕДУЮЩЕМ обращении, а не демоном в фоне.
_pool_mod._ENTRIES.clear(); _lifts["n"] = 0
_p_idle = _Pool({"enabled": True, "budget_ratio": 0.9, "idle_ttl_sec": 0.05}, _hw15)
_p_idle.get("old", _lift("old"), need_bytes=1000, device="cpu")
_time.sleep(0.1)
_p_idle.get("new", _lift("new"), need_bytes=1000, device="cpu")
ok({r["key"] for r in _p_idle.stats()["models"]} == {"new"},
   "модель, которой давно не пользовались, выгружена при следующем обращении")

# Выключатель обязан выключать: без пула модель поднимается каждый раз.
_pool_mod._ENTRIES.clear(); _lifts["n"] = 0
_p_off = _Pool({"enabled": False}, _hw15)
_p_off.get("m", _lift("m"), need_bytes=1, device="cpu")
_p_off.get("m", _lift("m"), need_bytes=1, device="cpu")
ok(_lifts["n"] == 2 and not _p_off.stats()["models"],
   f"enabled=false действительно отключает пул ({_lifts['n']} подъёма, ничего не держим)")

# Сквозь инструмент: занятое видно рядом со свободным.
_pool_mod._ENTRIES.clear()
_inst15 = _call("media_models", scope="installed")
ok(isinstance(_inst15.data.get("pool"), dict) and "budget_mb" in _inst15.data["pool"],
   "инструмент показывает, что держится поднятым, вместе с бюджетом")

# И живьём: та же модель через два вызова инструмента поднимается один раз.
if _bg_model:
    _pool_mod._ENTRIES.clear()
    # Строку канала вернули: предыдущие секции переписывали этот лист под свои сценарии.
    (_ws / "pic" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
        "B1": _row_bg}}}), encoding="utf-8")
    _t1 = _time.time(); _call("media_generate", table="pic", resource_type="bg_removals",
                              input="pic/frame.png", scene_id="p1"); _first = _time.time() - _t1
    _t2 = _time.time(); _call("media_generate", table="pic", resource_type="bg_removals",
                              input="pic/frame.png", scene_id="p2"); _second = _time.time() - _t2
    _held15 = next((r for r in _reg.pool.stats()["models"] if r["key"].startswith("onnx|")), None)
    ok(_held15 is not None and _held15["hits"] >= 1,
       "модель осталась поднятой между вызовами инструмента — второй вызов ПОПАЛ в пул "
       f"(обращений к записи: {(_held15 or {}).get('hits', '—')}; по часам {_first:.2f} с → {_second:.2f} с)")

print("== 16. Кто формирует задачу и куда кладёт результат (инварианты под раннер, S24) ==")
import re as _re16

# Задачу провайдеру (а завтра — раннеру) собирает СЕРВЕР из строки канала. Клиент не может
# передать ни адрес вызова, ни путь сохранения: иначе Claude выбирал бы, куда ходить и куда писать.
_schema16 = _eng.get_tool("media_generate").input_schema["properties"]
_addressy = [k for k in _schema16 if _re16.search(r"url|endpoint|host|target|dest|save|path", k, _re16.I)]
ok(not _addressy,
   f"инструмент не принимает от клиента адрес вызова или путь сохранения ({_addressy or 'таких полей нет'})")

# Куда класть — решает сервер: сущность выбирает ИИ (table), раскладку внутри неё — декларация.
if _bg_model:
    (_ws / "pic" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
        "B1": _row_bg}}}), encoding="utf-8")
    _r16 = _call("media_generate", table="pic", resource_type="bg_removals",
                 input="pic/frame.png", scene_id="s16")
    _p16 = _r16.data["files"][0]
    ok(_p16.startswith("pic/"),
       f"результат лёг внутрь указанной сущности, а не в общее место ({_p16})")
    _assets16 = yaml.safe_load(CFG.read_text(encoding="utf-8"))["assets"]["by_resource"]["bg_removals"]
    ok(f"/{_assets16['dir']}/" in _p16 and _p16.endswith(f".{_assets16['ext']}"),
       f"раскладка и расширение взяты из декларации, а не придуманы вызовом ({_p16})")
    ok((_ws / _p16).is_file(),
       "файл лежит по этому пути сразу — без промежуточного места и последующего переноса")

print("== 17. Раннер: инференс в отдельном процессе (S24) ==")
import socket as _sock17
import subprocess as _sp17
import httpx as _hx17
from core.providers.img.http_image import HttpImageAPI as _HTTP17
from core.runner import RunnerSupervisor as _Sup17

# Послабление на петлю объявлено ДАННЫМИ и действует только на объявленный хост: чужой адрес по
# http обязан остаться отказом, иначе «разрешили раннер» означало бы «разрешили открытый канал».
_api17 = _HTTP17(_reg)
_loop17 = {"url": "http://127.0.0.1:8770/run", "allow_insecure_host": "127.0.0.1"}
ok(_api17._url(_loop17, {}) == "http://127.0.0.1:8770/run", "петлевой http пропускается по объявлению")
for _case17, _decl17 in (
        ("чужой хост по http", {"url": "http://example.com/run", "allow_insecure_host": "127.0.0.1"}),
        ("соседняя петля", {"url": "http://127.0.0.2:8770/run", "allow_insecure_host": "127.0.0.1"}),
        ("петля без объявления", {"url": "http://127.0.0.1:8770/run"})):
    try:
        _api17._url(_decl17, {})
        ok(False, f"{_case17} должен отбиваться")
    except ProviderError as e:
        ok(e.code == "DOWNLOAD_FORBIDDEN", f"{_case17} → DOWNLOAD_FORBIDDEN ({e.code})")

# Занят ≠ не поднят. Долгий расчёт не должен отправлять ИИ поднимать то, что и так работает.
_runner_decl17 = yaml.safe_load(CFG.read_text(encoding="utf-8"))["online"]["http"]["Local_runner"]
ok(_api17._unreachable(_runner_decl17, "Local_runner", _hx17.ConnectError("отказано")).code
   == "RUNNER_NOT_RUNNING", "молчащий порт → «подними раннер», кодом из объявления")
ok(_api17._unreachable(_runner_decl17, "Local_runner", _hx17.ReadTimeout("долго")).code
   == "PROVIDER_TIMEOUT", "не успел за отведённое → «не успел», а не «не поднят»")
ok(float(_runner_decl17["timeout_sec"]) > 120,
   f"предел ожидания объявлен под локальный расчёт, а не общий ({_runner_decl17['timeout_sec']} с)")

# Запуск в контейнере проверяется по СОБРАННОЙ команде: набор не должен требовать ни Docker, ни
# образа на 5–10 ГБ, а свойства, ради которых контейнер и заводился, живут именно в этой команде.
_docker17 = _Sup17(_reg, ROOT)._command("docker", _ws)
_dstr17 = " ".join(_docker17)
ok(any(a.startswith("127.0.0.1:") for a in _docker17),
   f"порт публикуется только на петлю хоста, а не на все интерфейсы ({[a for a in _docker17 if ':' in a and a.count(':') == 2] or '—'})")
ok("MCP_RUNNER_TOKEN" in _docker17 and not any("MCP_RUNNER_TOKEN=" in a for a in _docker17),
   "токен передаётся ИМЕНЕМ переменной — в выводе ps его значения нет")
ok("--device" in _docker17 and "/dev/kfd" in _dstr17,
   "карта пробрасывается устройствами ядра — системный ROCm внутрь не ставится")
_gid17 = _docker17[_docker17.index("--group-add") + 1] if "--group-add" in _docker17 else ""
ok(_gid17.isdigit(),
   f"группа доступа к карте взята у устройства ЧИСЛОМ, а не именем, которого в образе нет ({_gid17 or 'её нет'})")
ok(_dstr17.count("-v ") >= 2 and "vendor/models" in _dstr17 and str(_ws) in _dstr17,
   "веса и рабочая область приезжают ТОМАМИ, а не слоями образа")
ok("providers.yaml:ro" in _dstr17 and "tunnel.yaml" not in _dstr17,
   "декларации монтируются живые и только для чтения, а секреты внутрь не едут")

# Стенд: свободный порт + КОПИЯ конфига в своём корне проекта; состояние и журнал — путями от этого корня, как их
# разрешает супервизор. Боевой vendor/ не трогаем — иначе тест поднимал бы раннер поверх настоящего и стирал его запись.
_p17 = _sock17.socket(); _p17.bind(("127.0.0.1", 0)); _port17 = _p17.getsockname()[1]; _p17.close()
_tmp17 = Path(tempfile.mkdtemp(prefix="runner_"))
_cfg17 = declarations_copy({"providers.yaml": {"online.http.Local_runner.url": f"http://127.0.0.1:{_port17}/run",
                                               "local.runner.state_file": "state.json",
                                               "local.runner.log_file": "runner.log",
                                               "local.models_dir": str(_reg.models_dir)}}, root=_tmp17)

_row17 = {**_row_bg, "provider": "Local_runner"}
(_ws / "pic" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
    "B1": _row17}}}), encoding="utf-8")

_was17 = _srv.CONFIG_PATH
try:
    _srv.CONFIG_PATH = _cfg17
    _eng17 = _Eng(reactions=_REACTIONS, state_manager=_sm)
    _srv.register_basic_tools(_eng17, _IDG(), _sm)

    def _c17(tool, **params):
        return _aio.run(_eng17.call(tool, params))

    # Раннера нет — молчаливого отката «посчитаем в сервере» быть не должно: строка канала
    # просила изоляцию, и подмена её тишиной скрыла бы, что изоляции нет.
    _cold17 = _c17("media_generate", table="pic", resource_type="bg_removals",
                   input="pic/frame.png", scene_id="c1")
    ok(_cold17.status == "error" and _cold17.error.code == "RUNNER_NOT_RUNNING",
       f"раннер не поднят → честный отказ, а не тихий расчёт в сервере ({_cold17.error.code if _cold17.error else 'успех!'})")
    ok(_c17("media_runner", action="status").data["phase"] == "stopped",
       "прозвонка не поднятого раннера отвечает «не поднят», а не молчит")

    _start17 = _c17("media_runner", action="start")
    ok(_start17.status == "success" and _start17.data["phase"] == "running" and _start17.data["pid"],
       f"инструмент поднял раннер ({_start17.data.get('phase') if _start17.status == 'success' else _start17.error.code})")
    _state17 = _tmp17 / "state.json"
    ok(_start17.status != "success" or _state17.is_file(),
       f"состояние раннера легло от корня проекта копии — state_file объявлен путём от корня ({_state17})")

    if _start17.status == "success" and _state17.is_file():
        _tok17 = _json.loads(_state17.read_text(encoding="utf-8"))["token"]
        _base17 = f"http://127.0.0.1:{_port17}"

        # Токен не украшение: на петле сидит не только сервер.
        ok(_hx17.post(f"{_base17}/run", json={"kind": "bg_removals"}, timeout=10).status_code == 401,
           "без токена раннер не исполняет ничего")
        _alive17 = _hx17.get(f"{_base17}/health", timeout=10).json()
        ok(_alive17.get("ok") and "pool" not in _alive17 and "models" not in _alive17,
           "живость видна без токена, а содержимое пула и готовность моделей — нет")
        _full17 = _hx17.get(f"{_base17}/health", headers={"X-Runner-Token": _tok17}, timeout=10).json()
        ok("pool" in _full17 and isinstance(_full17.get("models"), list),
           "с токеном /health показывает, что держится поднятым, и готовность моделей описи")

        # Раннер доверяет вызывающему не больше, чем сервер клиенту — даже на своей машине.
        _esc17 = _hx17.post(f"{_base17}/run", headers={"X-Runner-Token": _tok17}, timeout=10,
                            json={"kind": "bg_removals", "source": "pic/frame.png",
                                  "target": "../../../tmp/утёк.png", "params": {}})
        ok(_esc17.status_code == 403 and _esc17.json().get("code") == "PATH_ESCAPE",
           f"путь наружу рабочей области раннер не принимает ({_esc17.json().get('code')})")
        _mesc17 = _hx17.post(f"{_base17}/run", headers={"X-Runner-Token": _tok17}, timeout=30,
                             json={"kind": "bg_removals", "source": "pic/frame.png", "target": "pic/m.png",
                                   "params": {"model": "../../../../etc/passwd"}})
        ok(_mesc17.json().get("code") == "PATH_ESCAPE"
           and _mesc17.json().get("facts", {}).get("path") == "../../../../etc/passwd",
           f"имя модели наружу каталога весов — раннер отвечает формой ErrorDetail: код двери и путь фактом "
           f"({_mesc17.json().get('code')}, {_mesc17.json().get('facts')})")
        _sec17 = _hx17.post(f"{_base17}/run", headers={"X-Runner-Token": _tok17}, timeout=10,
                            json={"kind": "bg_removals", "source": "pic/frame.png",
                                  "target": "ch/.secrets/утёк.png", "params": {}})
        ok(_sec17.status_code == 403 and _sec17.json().get("code") == "SECRET_ACCESS_DENIED",
           f"путь в закрытый каталог раннер называет своим кодом, а не «выход за область» ({_sec17.json().get('code')})")
        _notarget17 = _hx17.post(f"{_base17}/run", headers={"X-Runner-Token": _tok17}, timeout=10,
                                 json={"kind": "bg_removals", "source": "pic/frame.png", "params": {}})
        ok(_notarget17.status_code == 400 and _notarget17.json().get("code") == "VALIDATION_ERROR"
           and {"field": "target", "rule": "required"} in (_notarget17.json().get("errors") or []),
           f"нет пути результата — ошибка ввода по полю target, а не путь-текст в факте ({_notarget17.json()})")
        _sh17 = _hx17.post(f"{_base17}/run", headers={"X-Runner-Token": _tok17}, timeout=10,
                           json={"kind": "bg_removals", "source": "pic/frame.png",
                                 "target": "pic/assets/img/своё.sh", "params": {}})
        ok(_sh17.status_code == 403 and _sh17.json().get("code") == "FILE_TYPE_FORBIDDEN",
           f"тип файла раннер проверяет тем же allowlist, что сервер ({_sh17.json().get('code')})")
        _unknown17 = _hx17.post(f"{_base17}/run", headers={"X-Runner-Token": _tok17}, timeout=10,
                                json={"kind": "нет-такого", "target": "pic/x.png", "params": {}})
        ok(_unknown17.json().get("code") == "PROVIDER_NOT_CONFIGURED",
           "неизвестный вид ресурса → код реестра, а не пятисотка")

    if _start17.status == "success" and _bg_model:
        _gen17 = _c17("media_generate", table="pic", resource_type="bg_removals",
                      input="pic/frame.png", scene_id="r1")
        ok(_gen17.status == "success",
           f"тот же вызов посчитан РАННЕРОМ и дошёл до файла ({_gen17.error.code if _gen17.error else 'ok'})")
        if _gen17.status == "success":
            ok((_ws / _gen17.data["files"][0]).is_file(),
               f"файл лёг туда же, куда клал расчёт в сервере ({_gen17.data['files'][0]})")
            # Обмен ПУТЯМИ, а не байтами: раннер сообщает, чем считал он, а не «remote».
            ok(_gen17.data["compute"].get("device") and _gen17.data["compute"]["device"] != "remote",
               f"видно, чем считал раннер, а не общее «удалённо» ({_gen17.data['compute'].get('device')})")
            ok(_c17("media_runner", action="status").data["calls"] == 1,
               "раннер отчитывается, что вызов прошёл именно через него")

        # Отказ раннера приходит ЕГО кодом: «нет весов» не должно превращаться в «сервис недоступен».
        (_ws / "pic" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
            "B1": {**_row17, "model": "нет-такой-модели"}}}}), encoding="utf-8")
        _lost17 = _c17("media_generate", table="pic", resource_type="bg_removals",
                       input="pic/frame.png", scene_id="r2")
        ok(_lost17.status == "error" and _lost17.error.code == "LOCAL_MODEL_MISSING",
           f"причина отказа раннера доезжает своим кодом ({_lost17.error.code if _lost17.error else 'успех!'})")
        (_ws / "pic" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
            "B1": {**_row17, "model": "../../../../etc/passwd"}}}}), encoding="utf-8")
        _out17 = _c17("media_generate", table="pic", resource_type="bg_removals",
                      input="pic/frame.png", scene_id="r2b")
        ok(_out17.status == "error" and _out17.error.code == "PATH_ESCAPE"
           and _out17.error.facts.get("path") == "../../../../etc/passwd",
           f"отказ раннера доезжает до клиента MCP кодом и фактами, а не одним текстом "
           f"({_out17.error.code if _out17.error else 'успех!'}, {_out17.error.facts if _out17.error else ''})")
        (_ws / "pic" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
            "B1": _row17}}}), encoding="utf-8")

    # Падение раннера обязано быть ВИДНО: авто-подъёма нет намеренно — он спрятал бы причину.
    if _start17.status == "success":
        _pid17 = _start17.data["pid"]
        _sp17.run(["kill", "-9", str(_pid17)], check=False)
        for _ in range(50):
            if not _Sup17._alive(_pid17):
                break
            _time.sleep(0.1)
        _dead17 = _c17("media_runner", action="status")
        ok(_dead17.data["phase"] == "exited",
           f"убитый раннер виден как exited, а не как молчание ({_dead17.data['phase']})")
        ok(_dead17.data["log_tail"], "вместе с фазой приходит хвост журнала — причина переживает процесс")
        _after17 = _c17("media_generate", table="pic", resource_type="bg_removals",
                        input="pic/frame.png", scene_id="r3")
        ok(_after17.status == "error" and _after17.error.code == "RUNNER_NOT_RUNNING",
           f"вызов после падения отказывает подсказкой поднять, а не считает молча в сервере ({_after17.error.code if _after17.error else 'успех!'})")
        ok(_c17("media_runner", action="stop").data["phase"] == "stopped",
           "остановка убирает за собой и не спотыкается о мёртвый процесс")

    # И обратно: строка канала переключает расчёт в сервер тем же table_update, без рестарта.
    if _bg_model:
        (_ws / "pic" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
            "B1": _row_bg}}}), encoding="utf-8")
        _back17 = _c17("media_generate", table="pic", resource_type="bg_removals",
                       input="pic/frame.png", scene_id="r4")
        ok(_back17.status == "success" and _back17.data["provider"] == "Local_onnx_bg",
           f"та же работа считается в сервере после правки одной строки ({_back17.data.get('provider') if _back17.status == 'success' else _back17.error.code})")
finally:
    _srv.CONFIG_PATH = _was17
    _aio.run(_eng17.call("media_runner", {"action": "stop"}))

# ═══ §13 Транскрибация: локальный STT по умолчанию (stable-ts) ═══
# Провайдер объявлен, а не импортирован: проверяем, что декларация доводит до класса и что
# каждый отказ назван кодом. Веса вне git — живой прогон пропускается с причиной.
_stt = _reg.load("Local_whisper", "transcriptions")
ok(type(_stt).__name__ == "WhisperLocalSTT", f"декларация ведёт к адаптеру ({type(_stt).__name__})")

_stt_dir = Path(tempfile.mkdtemp(prefix="stt_"))
_fake_audio = _stt_dir / "sample.wav"
_fake_audio.write_bytes(b"RIFF")


def _stt_req(target_name, source, model="whisper-tiny", **params):
    return MediaRequest(input=str(source or ""), params={"model": model, **params},
                        target=_stt_dir / target_name, models_dir=_reg.models_dir,
                        source=source, resource_type="transcriptions")


for _case, _req, _code in (
        ("исходника нет", _stt_req("a.json", None), "CONTENT_REJECTED"),
        ("формат не объявлен", _stt_req("a.srt", _fake_audio), "CONTENT_REJECTED"),
        ("весов нет", _stt_req("a.json", _fake_audio, model="нет-такой-модели"), "LOCAL_MODEL_MISSING")):
    try:
        _stt.generate(_req)
        ok(False, f"транскрибация ({_case}) обязана отказать, а не молчать")
    except ProviderError as _e13:
        ok(_e13.code == _code, f"транскрибация, {_case}: {_e13.code}")

# Проверка среды не должна опережать проверку запроса: на машине без ffmpeg все отказы
# схлопывались в LOCAL_INFERENCE_FAILED, и клиент не узнавал ни про формат, ни про веса.
# Ловится только там, где ffmpeg НЕТ, — поэтому прячем его, а не полагаемся на машину.
_was_path = os.environ.get("PATH", "")
os.environ["PATH"] = str(_stt_dir / "пусто")
try:
    for _case, _req, _code in (
            ("формат", _stt_req("a.srt", _fake_audio), "CONTENT_REJECTED"),
            ("веса", _stt_req("a.json", _fake_audio, model="нет-такой-модели"), "LOCAL_MODEL_MISSING")):
        try:
            _stt.generate(_req)
            ok(False, f"без ffmpeg отказ по «{_case}» обязан остаться собой")
        except ProviderError as _e13n:
            ok(_e13n.code == _code, f"без ffmpeg «{_case}» по-прежнему {_e13n.code}, а не среда")
finally:
    os.environ["PATH"] = _was_path

# Форматы транскрипта и write_allowlist — два списка, обязанных совпадать. Держим инвариантом,
# а не дисциплиной: формат, который сервер напишет, но файрвол не пропустит, — тихий отказ.
from core.providers.stt.whisper_local import WRITERS as _WRITERS
_allowed_ext = set(yaml.safe_load((ROOT / "config" / "firewall.yaml").read_text(
    encoding="utf-8"))["write_allowlist"]["extensions"])
ok(all(f".{_f}" in _allowed_ext for _f in _WRITERS),
   f"каждый формат транскрипта пропускает write_allowlist ({', '.join(sorted(_WRITERS))})")
try:
    _stt.generate(_stt_req("a.srt", _fake_audio))
except ProviderError as _e13f:
    ok(all(_f in (_e13f.reason or "") for _f in _WRITERS),
       f"отказ по формату называет поддержанные: {', '.join(sorted(_WRITERS))}")

# Директива владельца 2026-08-20: транскрибация НЕ приезжает с зависимостями сервера.
# Это инвариант, а не дисциплина: строка в base сломает тест, а не всплывёт на чужой машине.
import tomllib as _toml
from core.advice import Advice as _Advice
_manifest = _toml.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
ok(not any(_k in _d for _d in _manifest["dependencies"] for _k in ("whisper", "stable", "torch")),
   "распознавание вне базовых зависимостей: сервер его рекомендует, но не тянет")
ok(any("transformers" in _d for _d in _manifest["optional-dependencies"]["local"]),
   "распознавание исполняет transformers из группы local — новых пакетов не потребовалось")

# Сервер РЕКОМЕНДУЕТ его — совет обязан доехать до клиента с готовой командой.
_advice_stt = [_a for _a in _Advice(ROOT / "config" / "recommendations.yaml")
               .get("media_provider_status.choice", table="канал") if "whisper" in _a.get("id", "")]
ok(_advice_stt and _advice_stt[0]["tool"] == "table_update",
   "рекомендация локальной транскрибации доезжает готовой командой")

_installed_stt = [_m["id"] for _m in _MCat(CFG, _reg.models_dir).installed()
                  if _m.get("kind") == "stt"]
if _installed_stt and _have_tts:
    ok(True, f"опись знает поставленные модели распознавания ({', '.join(_installed_stt)})")
    # Вход берём своей же озвучкой: набор не зависит от постороннего файла на машине.
    _probe_wav = _stt_dir / "probe.wav"
    _reg.load("Local_piper", "tts_characters").generate(MediaRequest(
        input="Проверка распознавания речи.", params={"model": "ru_RU-dmitri-medium"},
        target=_probe_wav, models_dir=_reg.models_dir, resource_type="tts_characters"))
    _live = _stt.generate(_stt_req("live.json", _probe_wav, model=_installed_stt[0], language="ru"))
    ok(_live.files and _live.files[0].exists(), "транскрипт лёг файлом на диск")
    ok(not any(_k in _live.meta for _k in ("text", "transcript", "segments")),
       "текста в meta нет: чужая речь не обходит конверт провенанса (S3)")
    _live_doc = _json.loads(_live.files[0].read_text(encoding="utf-8"))
    _live_words = _live_doc.get("words") or []
    ok(_live_words and all(_w.get("start") is not None for _w in _live_words),
       f"границы слов на месте ({len(_live_words)} шт) — по ним считаются паузы и сцены")
    ok((_live_doc.get("text") or "").strip(), "текст распознан, а не пустая строка")
else:
    print("  ⚠ веса распознавания или озвучки не поставлены — живой прогон пропущен")

# ═══ §19 Загрузка и установка моделей: двойники источника, HTTP и сна ═══
# Каталог Hugging Face, httpx.request и сон подменены (пауза записывается, а не ждётся), декларация и
# каталог весов — свои во временном каталоге; подмены снимаются в finally, боевые config/ и vendor/ не тронуты.
import contextlib as _ctx19
import hashlib as _hl19
import httpx as _hx19
import io as _io19
import huggingface_hub as _hf19
import logging as _log19
import shutil as _sh19
import socket as _sock19
import subprocess as _sp19
import time as _t19
import types as _ty19


def _model_fetch(work: Path, records: list[str]) -> None:
    import models as cli
    from core.contracts import ContractError
    from core.providers.catalog import ModelCatalog
    from core.providers.img.http_image import HttpImageAPI

    def refused(call) -> str:
        """Код отказа вызова, пусто — если отказа не было: каталог, загрузчик и дверь отказывают каждый своим кодом."""
        try:
            call()
        except ContractError as e:
            return e.code
        return ""

    def answered(call) -> int | str:
        """Код ответа вызова или имя исключения: падение разбора — провал проверки, а не всего набора."""
        try:
            return call().status_code
        except Exception as e:
            return type(e).__name__

    remote: dict[tuple[str, str], bytes] = {}
    calls: list[tuple] = []
    info: dict[str, _ty19.SimpleNamespace] = {}
    pauses: list[float] = []

    def sibling(name: str, size: int, sha: str = "", blob: str = ""):
        """Файл репозитория, как его отдаёт model_info(files_metadata=True): LFS — sha256, иначе id git-блоба."""
        return _ty19.SimpleNamespace(rfilename=name, size=size, blob_id=blob or None,
                                     lfs=_ty19.SimpleNamespace(sha256=sha) if sha else None)

    def repo(*siblings, gated=False):
        return _ty19.SimpleNamespace(gated=gated, safetensors=None, cardData=None, config=None, siblings=list(siblings))

    def git_blob(data: bytes) -> str:
        """Id git-блоба считает сам git — оракул, независимый от формулы в коде цели."""
        return _sp19.run(["git", "hash-object", "--stdin"], input=data, capture_output=True,
                         check=True).stdout.decode().strip()

    lfs_files: set[tuple[str, str]] = set()
    wire: list[tuple] = []                  # (хост, Range) каждого запроса, ушедшего в сеть, — журнал транспорта
    cuts: dict[tuple[str, str], int] = {}   # обрыв тела части после стольких байт — на один ответ
    away: dict[tuple[str, str], str] = {}   # resolve части ведёт не на CDN, а сюда
    during: dict[tuple[str, str], object] = {}  # вызов посреди отдачи части — пока её загрузка идёт; на один ответ
    lengthless: set[tuple[str, str]] = set()    # CDN отдаёт часть без Content-Length
    unlinked: set[tuple[str, str]] = set()  # переход resolve на CDN без X-Linked-Size

    def put(repo_id: str, name: str, data: bytes, lfs: bool = True):
        """Файл источника и его строка model_info: длина и отпечаток сняты с самих байтов, а не с кода цели."""
        remote[(repo_id, name)] = data
        if lfs:
            lfs_files.add((repo_id, name))
            return sibling(name, len(data), sha=_hl19.sha256(data).hexdigest())
        return sibling(name, len(data), blob=git_blob(data))

    def model_info(repo_id, revision=None, files_metadata=False, **_kw):
        calls.append(("model_info", repo_id, revision))
        return info[repo_id]

    def served(key: tuple[str, str], request) -> _hx19.Response:
        """Байты части с Range и If-Range, ETag — свой у сервера, а не отпечаток состава (как у моста Xet, шаг 0)."""
        if key in during:
            during.pop(key)()
        data, tag = remote[key], f'"xet-{_hl19.sha1(remote[key]).hexdigest()[:12]}"'
        asked, start = request.headers.get("range", ""), 0
        if asked and request.headers.get("if-range") == tag:
            start = int(asked.split("=")[1].rstrip("-"))
            if start >= len(data):
                return _hx19.Response(416, headers={"content-range": f"bytes */{len(data)}"})
        cut = cuts.pop(key, None)

        def stream():
            for i in range(start, len(data), 64):
                if cut is not None and i - start >= cut:
                    raise _hx19.ReadError("соединение оборвано")
                yield data[i:i + 64]
        headers = {"etag": tag} if key in lengthless else {"etag": tag, "content-length": str(len(data) - start)}
        if start:
            headers["content-range"] = f"bytes {start}-{len(data) - 1}/{len(data)}"
        return _hx19.Response(206 if start else 200, headers=headers, content=stream())

    def serve(request):
        """Стенд Hugging Face по HTTP: resolve отдаёт малый файл сам, LFS — переходом на CDN (как настоящий, шаг 0)."""
        host = request.headers.get("host", "")
        wire.append((host, request.headers.get("range")))
        if host == "huggingface.co":
            repo_id, _, rest = request.url.path.lstrip("/").partition("/resolve/")
            revision, _, name = rest.partition("/")
            calls.append((request.method.lower(), repo_id, name, revision, request.headers.get("range")))
            if (repo_id, name) not in remote:
                return _hx19.Response(404)
            if (repo_id, name) in away:
                return _hx19.Response(302, headers={"location": away[(repo_id, name)]})
            if (repo_id, name) in lfs_files:
                # Длина файла за CDN — в заголовке перехода, как у настоящего на HEAD (huggingface_hub v1.31.0).
                told = {} if (repo_id, name) in unlinked else {"x-linked-size": str(len(remote[(repo_id, name)]))}
                return _hx19.Response(302, headers={"location": f"https://us.aws.cdn.hf.co/{repo_id}/{name}", **told})
            return served((repo_id, name), request)
        owner, repo_name, path = request.url.path.lstrip("/").split("/", 2)
        return served((f"{owner}/{repo_name}", path), request)

    def downloads(since: int = 0) -> list[tuple]:
        return [c for c in calls[since:] if c[0] == "get"]

    _hf19.model_info = model_info
    ModelCatalog.transport = _hx19.MockTransport(serve)
    ModelCatalog.resolve = staticmethod(lambda host, port, *a, **k: [
        (_sock19.AF_INET, _sock19.SOCK_STREAM, 6, "", ({"huggingface.co": "18.1.1.1"}.get(host, "13.2.2.2"), port))])
    _t19.sleep = pauses.append

    models = work / "vendor" / "models"
    cfg = declarations_copy(root=work) / "providers.yaml"   # боевые декларации проекта `work`, своя — поверх
    retry = {"idempotent_methods": ["GET", "HEAD", "PUT", "DELETE", "OPTIONS", "TRACE"],
             "retry_after_max_sec": 30, "backoff_max_sec": 60, "backoff_jitter_ratio": 0}
    cfg.write_text(yaml.safe_dump({"online": {"retry": retry}, "local": {
        "models_dir": "vendor/models",
        "hand_placed": "by_presence",
        "sources": {
            "tts": {"kind": "piper_index", "repo": "voices/piper", "dest": "tts"},
            "image": {"kind": "hf_hub", "dest": "img"},
            "bg_removal": {"kind": "hf_hub", "dest": "bg", "require_formats": [".onnx"]},
        },
        "source_kinds": {"hf_hub": {"size_header": "X-Linked-Size"}},
        "install": {
            "allow_sources": ["huggingface.co"],
            "allow_suffixes": [".safetensors", ".onnx", ".json", ".txt"],
            "deny_suffixes": [".bin", ".ckpt", ".pt"],
            "max_total_mb": 1,
            "prefer_files": ["*model.onnx"],
            "progress": {"heartbeat_sec": 0.05, "stale_after_sec": 300},
            "fetch": {"schemes": ["https"], "hosts": ["huggingface.co", "us.aws.cdn.hf.co"], "block_private": True,
                      "max_bytes": 0, "timeout_sec": 30, "redirects": 5, "attempts": 1, "backoff_sec": 1,
                      "backoff_max_sec": 60, "retry_after_max_sec": 120, "jitter": 0,
                      "speed_floor_bytes_per_sec": 0, "speed_window_sec": 30,
                      "retry_on": [429, 500, 502, 503, 504],
                      "codes": {"failed": "MODEL_SOURCE_FAILED", "corrupt": "MODEL_CORRUPT"}},
            "revisions": {"org/bg": "rev-bg", "org/sd": "rev-sd", "voices/piper": "rev-voices",
                          "org/big": "rev-big", "org/closed": "rev-closed", "org/noonnx": "rev-noonnx",
                          "org/nodigest": "rev-nodigest", "org/liar": "rev-liar", "org/short": "rev-short",
                          "org/legacy": "rev-legacy", "org/space": "rev-space", "org/blind": "rev-blind",
                          "org/headed": "rev-headed", "org/told": "rev-told", "org/astray": "rev-astray",
                          "org/moved": "rev-moved", "org/cut": "rev-cut", "org/away": "rev-away",
                          "org/twice": "rev-twice", "org/held": "rev-held", "org/slow": "rev-slow",
                          "org/foo": "rev-foo", "other/foo": "rev-other-foo", "third/foo": "rev-third-foo"},
        },
    }}, allow_unicode=True), encoding="utf-8")
    cat = ModelCatalog(cfg, models)
    graph, prep, voice_graph = b"g" * 300, b"{}" * 5, b"v" * 100

    print("== 19.0. Отпечаток части: потоком, как у независимых hashlib и git hash-object ==")
    data = "содержимое\0".encode() * 1000
    (work / "probe.bin").write_bytes(data)
    ok([ModelCatalog._digest(work / "probe.bin", a) for a in ("sha256", "git-blob-sha1", "md5")]
       == [_hl19.sha256(data).hexdigest(), git_blob(data), _hl19.md5(data).hexdigest()],
       "отпечаток файла совпадает с независимым: hashlib по байтам и git hash-object")
    from core.providers.hardware import probe as hw_probe
    ok(not models.exists() and hw_probe(models)["disk_free_mb"] is not None,
       "каталога весов ещё нет — место читается на диске, куда он ляжет, а не «не прочитано»")

    print("== 19. Установка одним графом: граф по предпочтению, описания рядом, pickle — отказ с причиной ==")
    info["org/bg"] = repo(
        put("org/bg", "onnx/model.onnx", graph), put("org/bg", "onnx/model_fp16.onnx", b"h" * 150),
        put("org/bg", "preprocessor_config.json", prep, lfs=False), put("org/bg", "pytorch_model.bin", b"p" * 400))
    try:
        bg = cat.install_planned(cat.место_под("org/bg", "bg_removal"))
    except ContractError as e:                  # отказ — улика проверок ниже, а не обрыв §19
        bg = {"path": f"отказ {e.code} {e.facts}", "refused": [], "revision": "",
              "disk": {"verdict": e.code, "checked": "", "why": e.message}}
    landed = models / "bg/bg/model.onnx"
    ok(bg["path"] == "bg/bg/model.onnx" and landed.is_file() and landed.read_bytes() == graph,
       f"граф по предпочтению лёг плоско в каталог вида с именем модели ({bg['path']})")
    ok((models / "bg/bg/preprocessor_config.json").is_file(), "описание предобработки лежит рядом с графом")
    ok(not (models / "bg/bg/model_fp16.onnx").exists(), "тот же граф в другой разрядности не тянется")
    ok(any(r["file"] == "pytorch_model.bin" for r in bg["refused"]), "pickle-веса отказаны с причиной")
    ok(bg["revision"] == "rev-bg" and ("model_info", "org/bg", "rev-bg") in calls,
       "список файлов и файлы — из объявленной ревизии")
    ok(bg["disk"]["verdict"] == "ready" and bg["disk"]["checked"] == "по отпечатку",
       f"конец установки судит по отпечатку и называет ступень ({bg['disk']['why']})")

    print("== 19a. Установка снимком: вариант fp16 вместо полных весов ==")
    info["org/sd"] = repo(
        put("org/sd", "model_index.json", prep, lfs=False), put("org/sd", "unet/diffusion.fp16.safetensors", b"f" * 200),
        put("org/sd", "unet/diffusion.safetensors", b"F" * 400))
    sd = cat.install_planned(cat.место_под("org/sd", "image"))
    pulled = {c[2] for c in downloads() if c[1] == "org/sd"}
    ok(sd["variant"] == "fp16" and pulled == {"model_index.json", "unet/diffusion.fp16.safetensors"},
       f"из одних весов в двух разрядностях тянется одна — fp16 ({sorted(pulled)})")
    ok((models / "img/sd/unet/diffusion.fp16.safetensors").is_file() and sd["path"] == "img/sd",
       "снимок лёг в каталог вида с именем модели")

    print("== 19a1. Строка списка — тот же состав и та же ревизия, что у установки (MG-3) ==")
    held19 = dict(info)

    def declared(name: str, size: int):
        return sibling(name, size, sha="0" * 64)    # длина объявлена источником; ни список, ни состав не качают

    # Первым в ответе источника — файл, который установка не поставит: «первый попавшийся» в строке был бы виден.
    info["org/sd"] = repo(declared("unet/diffusion.safetensors", 800_000),
                          declared("unet/diffusion.fp16.safetensors", 400_000), declared("model_index.json", 1_000),
                          declared("text_encoder/model.fp16.safetensors", 200_000))
    info["org/bg"] = repo(declared("onnx/model_fp16.onnx", 150_000), declared("onnx/model.onnx", 300_000),
                          declared("preprocessor_config.json", 100_000))
    for repo_id, kind, want, mb in (("org/sd", "image", [], 0.6), ("org/bg", "bg_removal", [".onnx"], 0.4)):
        mark = len(calls)
        row, plan = cat._detail(repo_id, want, False), cat.manifest(repo_id, kind)
        ok(row.get("mb") == plan["mb"] == mb,
           f"{repo_id}: размер строки списка — сумма частей, которые поставит установка ({row} / {plan['mb']} / {mb})")
        ok(row.get("file") in {p["source"] for p in plan["parts"]}
           and Path(str(row.get("file"))).name == plan.get("file", Path(str(row.get("file"))).name),
           f"{repo_id}: файл строки — из частей, которые поставит установка, и главный тот же, что в составе "
           f"({row.get('file')} / {plan.get('file', '—')})")
        asked = [c for c in calls[mark:] if c[0] == "model_info"]
        ok(asked == [("model_info", repo_id, plan["revision"])] * 2,
           f"{repo_id}: список и состав читают одну закреплённую ревизию — журнал двойника ({asked})")
    info["org/free"] = repo(declared("onnx/model.onnx", 300_000))
    mark = len(calls)
    row = cat._detail("org/free", [".onnx"], False)
    ok("detail_error" not in row and row.get("mb") == 0.3
       and [c for c in calls[mark:] if c[0] == "model_info"] == [("model_info", "org/free", None)],
       f"ревизия не закреплена — список читает текущее состояние одним запросом, без вычисления ревизии ({row})")
    info.clear()
    info.update(held19)

    print("== 19b. Голос piper: индекс → файлы голоса → каталог tts ==")
    remote[("voices/piper", "voices.json")] = _json.dumps({"ru_RU-test-low": {
        "language": {"code": "ru_RU"}, "quality": "low", "num_speakers": 1,
        "files": {"ru/test.onnx": {"size_bytes": 100, "md5_digest": _hl19.md5(voice_graph).hexdigest()},
                  "ru/test.onnx.json": {"size_bytes": 10, "md5_digest": _hl19.md5(prep).hexdigest()}}}}).encode()
    remote.update({("voices/piper", "ru/test.onnx"): voice_graph, ("voices/piper", "ru/test.onnx.json"): prep})
    voice = cat.install_planned(cat.место_под("ru_RU-test-low", "tts"))
    ok(voice["path"] == "tts/test.onnx" and (models / "tts/test.onnx").is_file()
       and (models / "tts/test.onnx.json").is_file(), "граф голоса и его описание лежат рядом в tts/")

    print("== 19c. Опись: одна запись на имя, переустановка заменяет, а не дублирует ==")
    cat.install_planned(cat.место_под("org/bg", "bg_removal"))
    ids = [e["id"] for e in cat.inventory()]
    ok(ids.count("bg") == 1 and set(ids) == {"bg", "sd", "ru_RU-test-low"}, f"опись без дублей ({ids})")
    seen = {r["id"]: r["disk"] for r in cat.installed()}
    ok(all(d["verdict"] == "ready" and d["checked"] == "по длине" for d in seen.values()),
       f"целиком поставленные модели готовы, и в строке названо, как проверено ({seen})")
    ok({r["id"]: r for r in cat.installed()}["sd"]["variant"] == "fp16", "вариант весов виден в строке поставленного")
    told = cat.manifest("org/sd", "image").get("params_total", "нет поля")
    ok(told is None, f"источник не назвал числа параметров (нет safetensors) — в составе None, а не 0 ({told!r})")
    told = {r["id"]: r for r in cat.installed()}["sd"].get("params_total", "нет поля")
    ok(told is None, f"строка поставленного отдаёт неизвестное число параметров как None, а не 0 ({told!r})")

    print("== 19d. Состав: каждая часть с длиной, отпечатком и его алгоритмом, по пути ==")
    bg_parts = cat.manifest("org/bg", "bg_removal")["parts"]
    ok([(p["path"], p["algo"], p["digest"]) for p in bg_parts]
       == [("model.onnx", "sha256", _hl19.sha256(graph).hexdigest()),
           ("preprocessor_config.json", "git-blob-sha1", git_blob(prep))],
       f"граф — sha256 из LFS, описание — id git-блоба, части по пути ({bg_parts})")
    ok(cat.manifest("org/bg", "bg_removal") == cat.manifest("org/bg", "bg_removal"),
       "тот же ответ источника — тот же состав, включая порядок частей")
    sd_parts = cat.manifest("org/sd", "image")["parts"]
    ok([p["source"] for p in sd_parts] == ["model_index.json", "unet/diffusion.fp16.safetensors"]
       and [p["path"] for p in sd_parts] == [p["source"] for p in sd_parts],
       "снимок: части по пути репозитория, полные веса рядом с fp16 в состав не входят")
    mark = len(calls)
    voice_parts = cat.manifest("ru_RU-test-low", "tts")["parts"]
    ok(not [c for c in downloads(mark) if c[2] == "voices.json"],
       "индекс голосов той же ревизии читается с диска — повторный состав не ходит в сеть")
    ok([(p["path"], p["algo"], p["digest"], p["size"]) for p in voice_parts]
       == [("test.onnx", "md5", _hl19.md5(voice_graph).hexdigest(), 100),
           ("test.onnx.json", "md5", _hl19.md5(prep).hexdigest(), 10)],
       f"голос — md5 и длина из индекса ({voice_parts})")
    info["org/nodigest"] = repo(sibling("model.onnx", 100))
    blind = cat.manifest("org/nodigest", "bg_removal")["parts"][0]
    ok(blind["digest"] is None and blind["algo"] is None,
       "источник отпечатка не назвал — неизвестен, а не пустая строка")
    stored = {e["id"]: e for e in cat.inventory()}
    ok(stored["bg"]["parts"] == bg_parts and stored["sd"]["parts"] == sd_parts and stored["bg"]["dest"] == "bg/bg",
       "опись хранит тот же состав, что снят с источника")

    print("== 19e. Отказы установщика: каждый — кодом, до загрузки весов ==")
    info["org/closed"] = repo(gated=True)
    info["org/big"] = repo(sibling("unet/big.safetensors", 5_000_000))
    info["org/noonnx"] = repo(sibling("model.safetensors", 100))
    ok(refused(lambda: cat.место_под("нет-голоса", "tts")) == "MODEL_NOT_IN_CATALOG",
       "голоса нет в индексе источника — отказ своим кодом, а не «весов нет»")
    before = len(calls)
    ok(refused(lambda: cat.install_planned(cat.место_под("org/closed", "image"))) == "MODEL_GATED",
       "закрытая автором модель — отказ своим кодом")
    ok(refused(lambda: cat.install_planned(cat.место_под("org/big", "image"))) == "MODEL_TOO_LARGE",
       "сверх предела размера — отказ своим кодом")
    ok(refused(lambda: cat.install_planned(cat.место_под("org/noonnx", "bg_removal"))) == "MODEL_FORMAT_MISSING",
       "нет файла нужного формата — отказ")
    ok(not downloads(before), "ни один отказ не тянул весов")

    print("== 19f. Вердикт по диску: половина модели — не «установлено», порча той же длины видна только отпечатку ==")
    (models / "img/sd/unet/diffusion.fp16.safetensors").unlink()
    half = {r["id"]: r["disk"] for r in cat.installed()}["sd"]
    ok(half["verdict"] == "incomplete"
       and [(p["path"], p["job"]) for p in half["plan"]] == [("unet/diffusion.fp16.safetensors", "докачать")],
       f"1 из 2 частей на диске — не установлено, план «докачать» недостающую ({half['why']})")
    (models / "bg/bg/model.onnx").write_bytes(b"X" * 300)
    (models / "img/sd/model_index.json").write_bytes(b"[]" * 5)
    shallow = {r["id"]: r["disk"] for r in cat.installed()}["bg"]
    ok(shallow["verdict"] == "ready" and shallow["checked"] == "по длине" and "по длине" in shallow["why"],
       "порча той же длины по длине не видна — и ответ называет ступень проверки")
    deep = cat.judge(stored["bg"], deep=True)
    ok(deep["verdict"] == "corrupt" and deep["checked"] == "по отпечатку"
       and [(p["path"], p["job"]) for p in deep["plan"]] == [("model.onnx", "заменить")],
       f"по отпечатку та же часть — «заменить» ({deep['why']})")
    with (models / "tts/test.onnx.json").open("ab") as f:
        f.write(b" ")
    longer = {r["id"]: r["disk"] for r in cat.installed()}["ru_RU-test-low"]
    ok(longer["verdict"] == "corrupt"
       and [(p["path"], p["job"]) for p in longer["plan"]] == [("test.onnx.json", "заменить")],
       "часть длиннее объявленной — «заменить», а не «лежит»")
    unsized = {"id": "unsized", "dest": "bg/bg", "parts": [ModelCatalog._part("model.onnx", "model.onnx", None, None, None)]}
    ok(cat.judge(unsized)["verdict"] == "unverified", "источник не назвал длину части — «судить нечем», а не «лежит»")

    print("== 19g. pull: тянет только план разницы и заново, а не «лежит той же ревизии» ==")
    cli._catalog = lambda: cat
    mark = len(calls)
    ok(cli.main(["models.py", "pull"]) == 0, "pull чинит опись целиком и отвечает успехом")
    fetched = downloads(mark)
    ok({(c[1], c[2]) for c in fetched} == {("org/bg", "onnx/model.onnx"), ("voices/piper", "ru/test.onnx.json"),
                                           ("org/sd", "model_index.json"), ("org/sd", "unet/diffusion.fp16.safetensors")},
       f"тянутся только части плана: недостающая и расходящиеся, каждая своим запросом ({fetched})")
    ok(fetched and all(c[-1] is None for c in fetched),
       "части плана тянутся с источника целиком — лежащий файл не выдаётся за скачанный (своего остатка нет)")
    ok(all(r["disk"]["verdict"] == "ready" for r in cat.installed())
       and (models / "bg/bg/model.onnx").read_bytes() == graph and (models / "img/sd/model_index.json").read_bytes() == prep,
       "после pull каждая модель цела, испорченные части заменены")
    mark = len(calls)
    ok(cli.main(["models.py", "pull", "bg"]) == 0 and not downloads(mark), "целая модель повторно не тянется")
    empty = ModelCatalog(cfg, work / "empty_models")
    cli._catalog = lambda: empty
    ok(cli.main(["models.py", "pull"]) == 1, "пустая опись — отказ, а не тихий успех")

    print("== 19h. Конец установки судит по отпечатку: расхождение — отказ кодом причины, состав остаётся в описи ==")
    info["org/liar"] = repo(sibling("model.onnx", 300, sha=_hl19.sha256("другое".encode()).hexdigest()))
    remote[("org/liar", "model.onnx")] = b"l" * 300
    ok(refused(lambda: cat.install_planned(cat.место_под("org/liar", "bg_removal"))) == "MODEL_CORRUPT",
       "байты не сошлись с отпечатком источника — MODEL_CORRUPT, а не «поставлено»")
    info["org/short"] = repo(sibling("model.onnx", 300, sha=_hl19.sha256(b"s" * 300).hexdigest()))
    remote[("org/short", "model.onnx")] = b"s" * 200
    ok(refused(lambda: cat.install_planned(cat.место_под("org/short", "bg_removal"))) == "DOWNLOAD_INCOMPLETE",
       "источник отдал часть короче объявленной — загрузчик называет хост и байты (DOWNLOAD_INCOMPLETE), остаток жив")
    ok({"liar", "short"} <= {e["id"] for e in cat.inventory()}, "состав записан в опись: pull знает, что чинить")
    cli._catalog = lambda: cat
    ok(cli.main(["models.py", "pull"]) == 1
       and all(r["disk"]["verdict"] == "ready" for r in cat.installed() if r["id"] in ("bg", "sd", "ru_RU-test-low")),
       "неисправимая модель — pull отвечает отказом, остальные проходят до конца")

    print("== 19i. Запись описи без состава: «судить нечем», pull снимает состав с источника по repo и виду ==")
    legacy = {"id": "legacy", "kind": "bg_removal", "repo": "org/legacy", "path": "bg/legacy/model.onnx"}
    cat.inventory_file.write_text(yaml.safe_dump({"models": [*cat.inventory(), legacy]}, allow_unicode=True),
                                  encoding="utf-8")
    old = {r["id"]: r["disk"] for r in cat.installed()}["legacy"]
    ok(old["verdict"] == "unverified" and "pull legacy" in old["why"], f"старая запись — «судить нечем» и рецепт ({old})")
    info["org/legacy"] = repo(put("org/legacy", "model.onnx", b"L" * 120))
    got = cat.restore(legacy)
    ok(got["disk"]["verdict"] == "ready" and got["disk"]["checked"] == "по отпечатку"
       and {e["id"]: e for e in cat.inventory()}["legacy"]["parts"] == got["parts"],
       "состав снят с источника, диск сверен по отпечатку, опись дополнена составом")

    print("== 19r. Части — одним загрузчиком: обрыв докачивается, рост виден по остатку, чужой хост — ни запроса ==")
    from core.providers.installer import ModelInstaller as Watcher

    info["org/cut"] = repo(put("org/cut", "model.onnx", b"c" * 1000))
    cuts[("org/cut", "model.onnx")] = 384
    first = cat.место_под("org/cut", "bg_removal")
    ok(refused(lambda: cat.install_planned(first)) == "DOWNLOAD_INCOMPLETE",
       "обрыв посреди части — отказ кодом загрузчика, а не «поставлено»")
    part_file = models / "bg/cut/model.onnx.bg_removal-cut.part"
    ok(part_file.is_file() and part_file.stat().st_size == 384 and cat.on_disk(first) == 384,
       f"остаток владельца модели — ровно принятые байты, и счёт плана видит их ({cat.on_disk(first)})")
    watcher = Watcher(cat)
    watcher._write({"install_id": "w19r", "model_id": "org/cut", "kind": "bg_removal", "phase": "running",
                    "started": 0, "heartbeat": 0, "bytes": 0})
    once = iter([False, True])
    watcher._watch("w19r", first, _ty19.SimpleNamespace(wait=lambda _t: next(once)))
    ok(watcher._read("w19r")["bytes"] == 384, "наблюдение установки растёт по остатку части, а не по кэшу загрузчика")
    w0 = len(wire)
    second = cat.место_под("org/cut", "bg_removal")
    second_marks: list = []
    ok(refused(lambda: cat.install_planned(second, mark=lambda: second_marks.append(1))) == ""
       and (models / "bg/cut/model.onnx").read_bytes() == b"c" * 1000,
       "повторная установка докачала часть — файл совпал с источником")
    ok(len(second_marks) == 14,
       f"признаки работы по ходу: 2 перехода + 10 кусков по 64 байта + блок сверки загрузчика + блок суда ({len(second_marks)})")
    ok([r for h, r in wire[w0:] if h == "us.aws.cdn.hf.co"] == ["bytes=384-"] and not part_file.exists()
       and cat.on_disk(second) == 1000,
       f"на CDN ушёл Range от длины остатка, остаток встал под имя, счёт — вся часть ({wire[w0:]})")
    forced = cat.место_под("org/cut", "bg_removal", force=True)
    ok(forced["plan"] and cat.on_disk(forced) == 0,
       f"часть, лежавшая до загрузки, не считается легшей в этот раз ({cat.on_disk(forced)})")
    marks: list = []
    ok(cat.judge(second["entry"], deep=True, mark=lambda: marks.append(1))["verdict"] == "ready" and marks,
       f"суд по отпечатку отмечает работу по ходу чтения, а не одной отметкой до ({len(marks)} отметок)")
    ok(cat.silence_sec() == 60.0 and refused(lambda: Watcher(cat, stale_after_sec=60)) == "SCHEMA_INVALID"
       and refused(lambda: Watcher(cat, stale_after_sec=61)) == "",
       f"простой не короче тишины загрузчика: 60 с (2 × timeout 30) — отказ кодом, 61 с — строится "
       f"({cat.silence_sec()})")
    info["org/away"] = repo(put("org/away", "model.onnx", b"a" * 100))
    away[("org/away", "model.onnx")] = "https://evil.example/org/away/model.onnx"
    w0 = len(wire)
    ok(refused(lambda: cat.install_planned(cat.место_под("org/away", "bg_removal"))) == "DOWNLOAD_FORBIDDEN"
       and "evil.example" not in [h for h, _ in wire[w0:]],
       f"resolve ведёт на необъявленный хост — DOWNLOAD_FORBIDDEN, к нему ни запроса ({wire[w0:]})")

    print("== 19j. Вызов провайдера: повтор только объявленного, пауза — по слову провайдера ==")

    class Resp:
        def __init__(self, status, headers=None):
            self.status_code, self.headers, self.text = status, headers or {}, "ответ провайдера"

        def json(self):
            raise ValueError("не JSON")

    def answers(*queue):
        """Подменяет httpx.request очередью ответов (исключение — бросается); возвращает список ушедших методов."""
        queue = list(queue)
        sent: list[str] = []

        def fake(method, url, **_kw):
            sent.append(method)
            answer = queue.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        _hx19.request = fake
        return sent

    api = HttpImageAPI(AdapterRegistry(cfg, work))
    url = "https://p.invalid/run"
    req = MediaRequest(input="x", params={"retry_count": 1}, target=work / "o.png", models_dir=models,
                       api_key="k", provider="p")
    sent = answers(Resp(200))
    ok(api._request({"retry_on": [503]}, "POST", url, req).status_code == 200 and len(sent) == 1,
       "успех с первой попытки — один запрос")
    sent = answers(Resp(400), Resp(200))
    ok(refused(lambda: api._request({"retry_on": [503]}, "POST", url, req)) == "PROVIDER_FAILED"
       and len(sent) == 1, "код вне объявленных к повтору — отказ сразу")
    pauses.clear()
    sent = answers(Resp(503, {"Retry-After": "2"}), Resp(200))
    ok(api._request({"retry_on": [503]}, "POST", url, req).status_code == 200 and len(sent) == 2
       and pauses == [2.0], f"перегрузка повторяется с паузой, названной провайдером ({pauses})")
    detail_body = {"code": "PATH_ESCAPE", "message": "m", "facts": {"path": "../x"},
                   "errors": [{"field": "target", "rule": "required"}]}
    answer = _ty19.SimpleNamespace(json=lambda: detail_body)
    api._decl_cache = {"Local_runner": {"error_detail": True}}
    named = api._named_refusal(answer)
    ok(named is not None and named.code == "PATH_ESCAPE" and named.facts == {"path": "../x"}
       and named.errors == [{"field": "target", "rule": "required"}],
       f"ответ формой сервера (ErrorDetail) переходит в отказ с кодом, фактами и ошибками ввода ({named and named.facts})")
    api._decl_cache = {"Fal": {}}
    ok(api._named_refusal(answer) is None,
       "адресат, не объявленный отвечающим формой сервера, кодом сервера не говорит — даже если в теле код")
    api._decl_cache = {}

    print("== 19k. Retry-After: секунды или HTTP-дата, потолок; нечитаемое — своя пауза и запись в журнал ==")
    own = MediaRequest(input="x", params={"retry_count": 1, "retry_delay": 1}, target=work / "o.png",
                       models_dir=models, api_key="k", provider="p")
    pauses.clear()
    answers(Resp(503, {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}), Resp(200))
    got = answered(lambda: api._request({"retry_on": [503]}, "POST", url, own))
    ok(got == 200 and pauses == [1.0], f"HTTP-дата в прошлом — без падения разбора, пауза своя ({got}, {pauses})")
    pauses.clear()
    answers(Resp(503, {"Retry-After": "Fri, 01 Jan 2100 00:00:00 GMT"}), Resp(200))
    got = answered(lambda: api._request({"retry_on": [503]}, "POST", url, own))
    ok(got == 200 and pauses == [30.0],
       f"HTTP-дата далеко впереди — пауза не больше объявленного потолка ({got}, {pauses})")
    pauses.clear()
    answers(Resp(503, {"Retry-After": "скоро"}), Resp(200))
    got = answered(lambda: api._request({"retry_on": [503]}, "POST", url, own))
    ok(got == 200 and pauses == [1.0] and any("Retry-After" in r for r in records),
       f"нечитаемый срок — своя пауза и запись в журнал ({got}, {pauses}, {records})")
    pauses.clear()
    jittered = HttpImageAPI(api.registry, rng=_ty19.SimpleNamespace(random=lambda: 0.5))
    api.registry.need = lambda *p, kind=None: {**retry, "backoff_jitter_ratio": 0.1}[p[-1]]
    answers(Resp(503, {"Retry-After": "2"}), Resp(200))
    jittered._request({"retry_on": [503]}, "POST", url, req)
    del api.registry.need
    ok([round(p, 6) for p in pauses] == [2.1], f"разброс паузы — от переданного генератора ({pauses})")

    print("== 19l. Неидемпотентный запрос не повторяется после ошибки, при которой провайдер мог его получить ==")
    sent = answers(_hx19.ReadTimeout("ответ не пришёл"), Resp(200))
    ok(refused(lambda: api._request({"retry_on": [503]}, "POST", url, req)) == "PROVIDER_TIMEOUT"
       and sent == ["POST"], f"POST после таймаута чтения — один запрос и код ({sent})")
    sent = answers(_hx19.ConnectError("порт закрыт"), Resp(200))
    ok(api._request({"retry_on": [503]}, "POST", url, req).status_code == 200
       and sent == ["POST", "POST"], f"POST после ошибки соединения — запрос не ушёл, повтор ({sent})")
    sent = answers(_hx19.ReadTimeout("ответ не пришёл"), Resp(200))
    ok(api._request({"retry_on": [503]}, "GET", url, req).status_code == 200
       and sent == ["GET", "GET"], f"GET после таймаута чтения — идемпотентен, повтор ({sent})")
    bare = work / "bare" / "providers.yaml"
    bare.parent.mkdir()
    bare.write_text(yaml.safe_dump({"local": {"models_dir": "vendor/models"}}), encoding="utf-8")
    sent = answers(Resp(200))
    ok(refused(lambda: HttpImageAPI(AdapterRegistry(bare, work))._request(
        {"retry_on": [503]}, "POST", url, req)) == "DECLARATION_INVALID" and sent == [],
       "нет объявления повторов — отказ декларации до запроса, умолчания в коде нет")

    print("== 19m. Готовность к подъёму: состав по длине, отказ кодом причины, руками положенное — по политике ==")
    reg = AdapterRegistry(cfg, work)
    got = reg.ready("bg")
    ok(got["path"] == models / "bg/bg/model.onnx" and got["checked"] == "по длине",
       f"поставленная модель — путь её весов и названная ступень ({got['why']})")
    ok(refused(lambda: reg.ready("short")) == "MODEL_INCOMPLETE", "часть короче объявленной — MODEL_INCOMPLETE до подъёма")
    old_rows = [*cat.inventory(), {"id": "old", "kind": "bg_removal", "repo": "org/old", "path": "bg/old/model.onnx"}]
    cat.inventory_file.write_text(yaml.safe_dump({"models": old_rows}, allow_unicode=True), encoding="utf-8")
    ok(refused(lambda: reg.ready("old")) == "MODEL_UNVERIFIED", "запись описи без состава — MODEL_UNVERIFIED, а не «лежит»")
    ok(refused(lambda: reg.ready("нет-такой")) == "LOCAL_MODEL_MISSING", "ни записи, ни файла — LOCAL_MODEL_MISSING")
    (models / "руками.onnx").write_bytes(b"r" * 10)
    hand = reg.ready("руками.onnx")
    ok(hand["path"] == models / "руками.onnx" and hand["checked"] == "по наличию (состав не объявлен)",
       f"руками положенное по политике by_presence — пускается, ступень названа ({hand['checked']})")
    base = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    for policy, code, what in (("refuse", "MODEL_UNVERIFIED", "политика refuse — отказ"),
                               ("maybe", "SCHEMA_INVALID", "чужое значение — отказ декларации"),
                               (None, "DECLARATION_INVALID", "ключа нет — отказ декларации, умолчания в коде нет")):
        variant = work / f"hand_{policy}" / "providers.yaml"
        variant.parent.mkdir()
        local = {k: v for k, v in base["local"].items() if k != "hand_placed"} | ({"hand_placed": policy} if policy else {})
        variant.write_text(yaml.safe_dump({**base, "local": local}, allow_unicode=True), encoding="utf-8")
        ok(refused(lambda: AdapterRegistry(variant, work).ready("руками.onnx")) == code, f"руками положенное: {what}")
    engine = _Eng(reactions=_REACTIONS)

    def delivered(call) -> str:
        """Что клиент увидит в message отказа — путь доставки сервера (Engine.refusal с реестром), а не поле исключения."""
        try:
            call()
        except ProviderError as e:
            return engine.refusal(e).error.message
        return ""

    said = delivered(lambda: AdapterRegistry(work / "hand_maybe" / "providers.yaml", work).ready("руками.onnx"))
    ok(all(v in said for v in AdapterRegistry.HAND_PLACED), f"клиент видит допустимые значения из таблицы ({said})")
    said = delivered(lambda: reg.ready("short"))
    ok("докачать model.onnx" in said and "pull short" in said, f"клиент видит план и команду для этой модели ({said})")
    said = delivered(lambda: reg.ready("нет-такой"))
    ok("'нет-такой'" in said and "(ожидались в нет-такой)" in said and str(models) not in said,
       f"клиент видит модель и где ждали веса — путь от корня весов, без места сервера ({said})")
    from core.providers.img.onnx_runtime import OnnxImage
    (models / "битый.onnx").write_bytes(b"not an onnx graph")
    said = delivered(lambda: OnnxImage(reg).session(models / "битый.onnx"))
    ok("'битый.onnx'" in said and "не поднимаются" in said and "ONNXRuntimeError" not in said and "protobuf" not in said.lower(),
       f"граф лежит, но не поднимается — MODEL_UNLOADABLE, сырой текст библиотеки клиенту не уходит ({said})")
    said = delivered(lambda: AdapterRegistry(work / "hand_refuse" / "providers.yaml", work).ready("руками.onnx"))
    ok("руками.onnx" in said and "by_presence" in said, f"клиент видит, как пустить руками положенное ({said})")
    from core.contracts import ContractError
    try:
        reg.ready("../../etc/passwd")
        escaped = None
    except ContractError as e:
        escaped = engine.refusal(e).error
    ok(escaped is not None and escaped.code == "PATH_ESCAPE" and escaped.facts.get("path") == "../../etc/passwd"
       and str(models) not in escaped.message,
       f"имя модели наружу — отказ двери PATH_ESCAPE, путь от корня, без места сервера "
       f"({escaped and (escaped.code, escaped.facts, escaped.message)})")

    print("== 19n. /health раннера: готовность каждой модели описи по длине — без отпечатка и сети ==")
    from core.runner.service import RunnerService
    svc = RunnerService(cfg, work, work / "ws", token="t")
    body = _json.loads(_aio.run(svc.health(_ty19.SimpleNamespace(headers={"Authorization": "t"}))).text)
    # Ожидание — из построения: части liar загрузчик отверг по отпечатку до того, как они встали под имя; short короче;
    # cut докачан с места обрыва; у away переход на чужой хост — частей нет; old без состава.
    expected = {"bg": "ready", "sd": "ready", "ru_RU-test-low": "ready", "legacy": "ready", "cut": "ready",
                "liar": "incomplete", "short": "incomplete", "away": "incomplete", "old": "unverified"}
    ok({m["id"]: m["verdict"] for m in body["models"]} == expected,
       f"/health называет готовность каждой модели описи ({[(m['id'], m['verdict']) for m in body['models']]})")
    ok(all(m["checked"] == "по длине" for m in body["models"] if m["verdict"] != "unverified"),
       "/health судит только по длине и называет ступень — отпечатка на каждый вызов нет")
    bare_body = _json.loads(_aio.run(svc.health(_ty19.SimpleNamespace(headers={}))).text)
    ok("models" not in bare_body, "без токена готовность моделей не видна — только живость")

    print("== 19o. Место по плану: нужно — сумма частей плана, неизвестное — отказ кодом, качают тот же снимок ==")
    import core.providers.hardware as hw_mod
    from core.providers.installer import ModelInstaller

    def outcome(call) -> str:
        """Код отказа или имя исключения: падение — провал этой проверки, а не всего §19."""
        try:
            call()
        except ProviderError as e:
            return e.code
        except Exception as e:
            return type(e).__name__
        return ""

    info["org/space"] = repo(put("org/space", "model.onnx", b"s" * 400_000), put("org/space", "config.json", prep, lfs=False))
    space = cat.место_под("org/space", "bg_removal")
    ok(space["нужно_мб"] == 0.4 and [p["path"] for p in space["plan"]] == ["config.json", "model.onnx"],
       f"нужно — сумма частей плана ({space['нужно_мб']} МБ, {[p['path'] for p in space['plan']]})")
    whole = cat.место_под("org/bg", "bg_removal")
    ok(whole["нужно_мб"] == 0 and whole["plan"] == [], "поставленная целиком — качать нечего, нужно 0")
    mark = len(calls)
    cat.install_planned(cat.место_под("org/sd", "image"))
    ok(not downloads(mark), "переустановка целой модели снимком не тянет ничего — пустой план не значит «весь репозиторий»")
    # Длина части — по ступеням (MS-04): метаданные источника → HEAD без тела → неизвестна. Истина — байты стенда.
    def unsized(repo_id: str, data: bytes, *, linked: bool, cdn_length: bool) -> None:
        """Часть, чью длину model_info не назвал; HEAD найдёт её в X-Linked-Size перехода и/или в Content-Length CDN."""
        found = put(repo_id, "model.onnx", data)
        found.size = None
        if not linked:
            unlinked.add((repo_id, "model.onnx"))
        if not cdn_length:
            lengthless.add((repo_id, "model.onnx"))
        info[repo_id] = repo(found)

    def placed(repo_id: str) -> dict:
        """Снимок места или код отказа: отказ — провал проверки, а не всего §19."""
        try:
            return cat.место_под(repo_id, "bg_removal")
        except ContractError as e:          # каталог и загрузчик отказывают каждый своим кодом
            return {"code": e.code}

    unsized("org/headed", b"h" * 300_000, linked=True, cdn_length=False)
    unsized("org/told", b"t" * 200_000, linked=False, cdn_length=True)
    unsized("org/blind", b"b" * 100_000, linked=False, cdn_length=False)
    for repo_id, mb, step in (("org/headed", 0.3, "X-Linked-Size перехода"), ("org/told", 0.2, "Content-Length CDN")):
        mark = len(calls)
        got = placed(repo_id)
        sizes = [p["size"] for p in got.get("plan") or []]
        ok(got.get("нужно_мб") == mb and sizes == [len(remote[(repo_id, "model.onnx")])]
           and [c[0] for c in calls[mark:] if c[0] != "model_info"] == ["head"],
           f"{repo_id}: метаданные длину не назвали — её назвал HEAD ({step}), тело не тянули ({got} {calls[mark:]})")
        row = cat._detail(repo_id, [".onnx"], False)
        ok(row.get("mb") == mb and "size_unknown" not in row, f"{repo_id}: строка списка — та же длина по HEAD ({row})")
    ok(outcome(lambda: cat.место_под("org/blind", "bg_removal")) == "MODEL_SIZE_UNKNOWN",
       "длину не назвали ни метаданные, ни HEAD — MODEL_SIZE_UNKNOWN, а не 0 и не «хватает»")
    said = delivered(lambda: cat.место_под("org/blind", "bg_removal"))
    ok(all(s in said for s in ("org/blind", "model.onnx", "HEAD", "X-Linked-Size", "Content-Length")),
       f"клиент видит модель, часть без длины и что спрошено ({said})")
    row = cat._detail("org/blind", [".onnx"], False)
    ok(row.get("mb") is None and "HEAD" in str((row.get("size_unknown") or {}).get("model.onnx")),
       f"строка списка: размер пуст, а не 0, и названо, что спрошено ({row})")
    from core.providers.download import Unreached

    class _Недостижим:
        """Загрузчик ровно в части, которую спрашивает ступень HEAD: длина не узнана с заданной причиной."""

        def __init__(self, how):
            self.how = how

        def length(self, url, headers=None, *, linked=""):
            return None, self.how

    stages = []
    for how in ("HEAD us.aws.cdn.hf.co: 503", Unreached("HEAD us.aws.cdn.hf.co: 503")):
        cat.fetcher = lambda mark=None, _how=how: _Недостижим(_how)
        stages.append(cat._part_sizes("org/blind", info["org/blind"], ["model.onnx"]))
    del cat.fetcher
    ok(stages[0] == stages[1] and stages[1][0] == {"model.onnx": None} and "503" in stages[1][1]["model.onnx"],
       f"ступень HEAD каталога на причине Unreached («хост не достигнут») — тот же исход, что на простой строке ({stages})")

    # Список хаба — по записанному ответу (MS-07): свойства вне expand хаб не присылает вовсе.
    import json as _json19

    from core.providers.hardware import FitEstimator
    recorded = _json19.loads((ROOT / "tests" / "quick" / "hf_list_models.recorded.json").read_text(encoding="utf-8"))

    class RecordedApi:
        """Хаб по записанному ответу: запись — по источнику и по тому, есть ли likes в expand; разбор — ModelInfo хаба."""

        def list_models(self, **kw):
            label = "с_likes" if "likes" in (kw.get("expand") or []) else "без_likes"
            return [_hf19.ModelInfo(**{"siblings": None, **item})
                    for item in recorded[f"{'фон' if kw.get('filter') else 'картинка'}/{label}"]]

    real_hf_api, _hf19.HfApi = _hf19.HfApi, RecordedApi
    try:
        hub = [r for src, kind in (({"tags": ["background-removal", "onnx"]}, "bg_removal"),
                                   ({"pipeline_tag": "text-to-image"}, "image"))
               for r in cat._from_hf(src, {}, 2, kind)]
    finally:
        _hf19.HfApi = real_hf_api
    ok([(r["id"], r["likes"], r["downloads"]) for r in hub]
       == [(m["id"], m["likes"], m["downloads"]) for key in ("фон/с_likes", "картинка/с_likes") for m in recorded[key]],
       f"likes и downloads — числа хаба: likes просится в expand, иначе хаб его не пришлёт ({hub and hub[0]})")
    totals = {r["id"]: r["params_total"] for r in hub}
    ok(totals["abenzerps/Qwen-Image-2.1-Uncensored-GGUF"] is None
       and totals["stabilityai/stable-diffusion-xl-base-1.0"] == 2567463684,
       f"без safetensors (GGUF) хаб числа параметров не называет — None, а не 0; названное — как есть ({totals})")
    est = FitEstimator({"dtype_bytes": {"F16": 2, "F32": 4}, "default_dtype_bytes": 4, "overhead": 1.0,
                        "dtype_alias": {"fp16": "F16"}})
    got = (est.bytes_for({"F32": 10}, None, as_dtype="fp16"), est.bytes_for({}, None), est.bytes_for({}, 5))
    ok(got == (20, 0, 20),
       "узел «параметры неизвестны» — bytes_for: None → число из разрядностей, поднятое в fp16 (10 × 2); ни total, "
       f"ни разрядностей — знак 0 байт; названное число — по нему (5 × 4) ({got})")
    kept = cat._enrich([{"id": "org/blind", "mb": None}, {"id": "org/headed", "mb": None}], [".onnx"], False, 1000, 5)
    ok([r["id"] for r in kept] == ["org/blind"],
       f"отсев min_mb: неизвестный размер не «мельче» — строка остаётся, известные 0.3 МБ отсеяны ({kept})")
    unsized("org/astray", b"a" * 100_000, linked=False, cdn_length=True)
    away[("org/astray", "model.onnx")] = "https://evil.example/x"
    mark = len(wire)
    try:
        row = cat._detail("org/astray", [".onnx"], False)
    except ContractError as e:
        row = {"вылетел": e.code}       # строка списка не должна ронять весь список
    ok(placed("org/astray") == {"code": "DOWNLOAD_FORBIDDEN"} and row.get("detail_error") == "DOWNLOAD_FORBIDDEN"
       and not [w for w in wire[mark:] if w[0] == "evil.example"],
       f"переход HEAD на чужой хост — отказ суда, а не «размер неизвестен»; строка списка называет код; туда ни "
       f"запроса ({row} {wire[mark:]})")
    fit = cat.with_fit(kept)[0]["fit"] if kept else {}
    ok(fit.get("verdict") == "unknown" and "ни размера" in str(fit.get("why")),
       f"пригодность без параметров и размера — «неизвестно» с причиной, а не «влезает» ({fit})")
    ok(ModelCatalog._voice_mb({"files": {"a.onnx": {"size_bytes": 5_000_000}, "a.json": {}}}) is None
       and ModelCatalog._voice_mb({"files": {"a.onnx": {"size_bytes": 5_000_000}, "a.json": {"size_bytes": 100_000}}})
       == 5.1, "голос piper: файл без size_bytes — сумма неизвестна (None), а не меньше; названы все — сумма")
    real_probe = hw_mod.probe
    try:
        hw_mod.probe = lambda *a, **k: {**real_probe(*a, **k), "disk_free_mb": None}
        ok(outcome(lambda: cat.место_под("org/space", "bg_removal")) == "DISK_SPACE_UNKNOWN",
           "место не прочитано — DISK_SPACE_UNKNOWN, а не «нет места» и не «хватает»")
        said = delivered(lambda: cat.место_под("org/space", "bg_removal"))
        ok("не прочитано" in said, f"клиент видит, что место не прочитано ({said})")
        hw_mod.probe = lambda *a, **k: {**real_probe(*a, **k), "disk_free_mb": 0}
        mark = len(calls)
        ok(outcome(lambda: ModelInstaller(cat).start("org/space", "bg_removal")) == "MODEL_NO_DISK_SPACE"
           and not downloads(mark),
           "места меньше плана — каталог отказывает MODEL_NO_DISK_SPACE до потока и до первого запроса весов")
        said = delivered(lambda: cat.место_под("org/space", "bg_removal"))
        ok("0.4 МБ" in said and "свободно 0 МБ" in said and "org/space" in said,
           f"клиент видит модель и оба числа — нужное и свободное ({said})")
        cli._catalog = lambda: cat
        printed = _io19.StringIO()
        with _ctx19.redirect_stdout(printed):
            code = cli.main(["models.py", "install", "org/space", "--kind", "bg_removal"])
        ok(code == 2 and "MODEL_NO_DISK_SPACE" in printed.getvalue(),
           f"CLI при нехватке места — отказ тем же кодом каталога, а не загрузка ({code})")
    finally:
        hw_mod.probe = real_probe
    mark = len(calls)
    info["org/moved"] = repo(put("org/moved", "model.onnx", b"m" * 300))
    space = cat.место_под("org/moved", "bg_removal")
    info["org/moved"] = repo(put("org/moved", "model.onnx", b"m" * 300), put("org/moved", "config.json", prep, lfs=False))
    got = outcome(lambda: cat.install_planned(space))
    asked = [c for c in calls[mark:] if c[0] == "model_info"]
    ok(got == "" and asked == [("model_info", "org/moved", "rev-moved")]
       and {c[2] for c in downloads(mark)} == {"model.onnx"},
       f"место судили и качают по одному снимку: источник спрошен раз, план места = план загрузки ({asked}, {got})")
    mark = len(calls)
    ok(cli.main(["models.py", "install", "org/space", "--kind", "bg_removal"]) == 0
       and [c for c in calls[mark:] if c[0] == "model_info"] == [("model_info", "org/space", "rev-space")],
       "CLI ставит по тому же снимку, по которому проверил место")
    real_usage = _sh19.disk_usage

    def unreadable(path):
        raise OSError(13, "нет доступа")
    _sh19.disk_usage = unreadable
    try:
        blind_hw = real_probe(models)
    finally:
        _sh19.disk_usage = real_usage
    ok(blind_hw["disk_free_mb"] is None and any("свободное место" in u for u in blind_hw["unknown"]),
       f"probe: место не прочитано — None и строка «не знаем», а не 0 ({blind_hw['disk_free_mb']})")

    print("== 19p. Повторная установка поставленной — «уже стоит» сразу: без задачи, сети и чтения весов (MF-17) ==")
    import threading
    shas, asked = {"org/float": "c1"}, []

    class FakeApi:
        def repo_info(self, repo_id, repo_type="model"):
            asked.append(repo_id)
            return _ty19.SimpleNamespace(sha=shas[repo_id])

    def finished(tool, answer):
        """Итог ответа установки: исход без задачи — как есть; задача — прозвоном до конца (ожидание событием с потолком)."""
        if answer.status != "success" or answer.data.get("task_id") is None:
            return answer.data if answer.status == "success" else {"status": "failed", "result": None}
        pause = threading.Event()
        for _ in range(100):
            state = tool("media_install_status", install_id=answer.data["task_id"]).data["installs"][0]
            if state["status"] != "processing":
                return state
            pause.wait(0.05)
        return state

    was, real_api, real_digest = _srv.CONFIG_PATH, _hf19.HfApi, ModelCatalog.__dict__["_digest"]
    tool_eng = _Eng(reactions=_REACTIONS, state_manager=_sm)
    try:
        _srv.CONFIG_PATH = cfg.parent
        _hf19.HfApi = FakeApi
        _srv.register_basic_tools(tool_eng, _IDG(), _sm)

        def tool(name, **params):
            return _aio.run(tool_eng.call(name, params))

        installs = models / "installs"
        before_files, mark, read = set(installs.glob("*.json")) if installs.is_dir() else set(), len(calls), []
        ModelCatalog._digest = staticmethod(lambda path, algo: read.append(path) or real_digest.__func__(path, algo))
        same = tool("media_model_install", model_id="org/bg", kind="bg_removal", confirm=True)
        ModelCatalog._digest = real_digest
        ok(same.status == "success" and same.data["task_id"] is None and same.data["status"] == "completed"
           and same.data["result"]["outcome"] == "already_installed"
           and same.data["result"]["checked"] == "по длине, ревизия закреплена",
           f"поставленная модель — исход «уже стоит» сразу, задача не заводится ({same.data if same.status == 'success' else same.error})")
        ok(not calls[mark:] and not read and (set(installs.glob("*.json")) if installs.is_dir() else set()) == before_files
           and any(f.type == "CreationSkipped" for f in same.facts),
           f"ни источника, ни чтения весов, ни файла задачи — только исход ({calls[mark:]}, {len(read)} чтений)")

        mark = len(calls)
        again = tool("media_model_install", model_id="org/bg", kind="bg_removal", confirm=True, force=True)
        state = finished(tool, again)
        ok(again.data["status"] == "processing" and state["status"] == "completed"
           and state["result"]["outcome"] == "reinstalled" and downloads(mark),
           f"явный force — задача и перекачка, исход «перекачано» ({state.get('result')})")

        (models / "img/sd/unet/diffusion.fp16.safetensors").unlink()
        half = tool("media_model_install", model_id="org/sd", kind="image", confirm=True)
        state = finished(tool, half)
        ok(half.data["status"] == "processing" and state["result"]["outcome"] == "installed",
           "половина модели на диске — не «уже стоит», а обычная установка")

        info["org/float"] = repo(put("org/float", "model.onnx", b"F" * 200))
        cat.install_planned(cat.место_под("org/float", "bg_removal"))
        asked.clear()
        floating = tool("media_model_install", model_id="org/float", kind="bg_removal", confirm=True)
        ok(floating.data["task_id"] is None and asked == ["org/float"]
           and floating.data["result"]["checked"] == "по длине, ревизия сверена с источником",
           f"плавающая ревизия — один запрос коммита, и ответ называет поход в сеть ({asked}, {floating.data.get('result')})")
        shas["org/float"] = "c2"
        moved = tool("media_model_install", model_id="org/float", kind="bg_removal", confirm=True)
        state = finished(tool, moved)
        ok(moved.data["status"] == "processing" and state["status"] == "completed",
           "у источника новый коммит — не «уже стоит», а установка новой ревизии")

        cli._catalog = lambda: cat
        mark = len(calls)
        ok(cli.main(["models.py", "install", "org/bg", "--kind", "bg_removal"]) == 0 and not calls[mark:],
           "CLI: поставленная модель — «уже стоит», загрузчик не зван")
    finally:
        _srv.CONFIG_PATH, _hf19.HfApi = was, real_api
        ModelCatalog._digest = real_digest

    print("== 19s. Модель ставит одна установка на все процессы: замок по владельцу остатков; опись и малый файл "
          "источника — под своими замками ==")
    from core.paths import Files

    def busy(lock) -> bool:
        """Замок держит кто-то другой — проба ОС без ожидания (отказ замка слоя — подкласс TimeoutError)."""
        try:
            with lock.acquire(timeout=0):
                return False
        except TimeoutError:
            return True

    installs = models / "installs"

    def tasks() -> list:
        return sorted(installs.glob("*.json")) if installs.is_dir() else []

    info["org/twice"] = repo(put("org/twice", "model.onnx", b"t" * 500))
    twice = cat.место_под("org/twice", "bg_removal")
    seen: dict = {"tasks": tasks()}

    def second_start() -> None:
        try:
            ModelInstaller(cat).start("org/twice", "bg_removal")
        except ProviderError as e:
            seen["error"] = engine.refusal(e).error
        seen["after"] = tasks()

    during[("org/twice", "model.onnx")] = second_start
    try:
        first = cat.install_planned(twice)
    except Exception as e:                      # две установки в один остаток: порча байтов или чужой publish снял его
        first = {"disk": {"verdict": getattr(e, "code", type(e).__name__)}}
    error = seen.get("error")
    ok(error is not None and error.code == "MODEL_INSTALL_BUSY" and seen["after"] == seen["tasks"],
       f"вторая установка той же модели, пока первая пишет её часть, — отказ из start, файла задачи нет ({error})")
    ok(error is not None and "twice" in error.message and error.recovery.suggested_tool == "media_install_status",
       f"клиент видит, какая модель уже ставится, и чем прозвонить идущую ({error})")
    ok(first["disk"]["verdict"] == "ready" and (models / "bg/twice/model.onnx").read_bytes() == b"t" * 500,
       f"первая установка отказом второй не задета — часть совпала с источником ({first['disk']['verdict']})")

    info["org/held"] = repo(put("org/held", "model.onnx", b"h" * 300))
    held_space = cat.место_под("org/held", "bg_removal")
    owner = ModelCatalog.owner(held_space["entry"])
    remnant = Files(models).remnant(ModelCatalog.landing(held_space["entry"], held_space["plan"][0]), owner)
    remnant.parent.mkdir(parents=True, exist_ok=True)
    remnant.write_bytes(b"h" * 100)
    before = (remnant.read_bytes(), remnant.stat().st_ino)
    holder = _sp19.Popen(
        [sys.executable, "-c", "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
         "from core.paths import Files; lock = Files(Path(sys.argv[2])).lock(sys.argv[3]); lock.acquire(); "
         "print('держу', flush=True); sys.stdin.read()", str(ROOT), str(models), owner],
        stdin=_sp19.PIPE, stdout=_sp19.PIPE, text=True)
    try:
        ok(holder.stdout.readline().strip() == "держу", "другой процесс взял замок модели дверью слоя файлов")
        mark = len(calls)
        ok(outcome(lambda: cat.install_planned(held_space)) == "MODEL_INSTALL_BUSY"
           and (remnant.read_bytes(), remnant.stat().st_ino) == before and not downloads(mark),
           "замок держит другой процесс — отказ до первой записи: остаток не тронут, к источнику ни запроса")
        cli._catalog = lambda: cat
        printed = _io19.StringIO()
        with _ctx19.redirect_stdout(printed):
            code = cli.main(["models.py", "install", "org/held", "--kind", "bg_removal"])
        ok(code == 2 and "MODEL_INSTALL_BUSY" in printed.getvalue(),
           f"CLI ставит тем же путём и упирается в тот же замок — отказ кодом, а не вторая загрузка ({code})")
    finally:
        holder.stdin.close()
        holder.wait()
    ok(outcome(lambda: cat.install_planned(held_space)) == "" and not remnant.exists()
       and (models / "bg/held/model.onnx").read_bytes() == b"h" * 300,
       "держатель вышел — его замок сняло ядро, установка прошла")

    info["org/slow"] = repo(put("org/slow", "model.onnx", b"w" * 400))
    slow, at_fetch, finished = ModelInstaller(cat), {}, threading.Event()
    real_write = slow._write

    def write(state: dict) -> None:
        real_write(state)
        if state.get("phase") != "running":
            finished.set()

    slow._write = write
    during[("org/slow", "model.onnx")] = lambda: at_fetch.update(
        status=[s["status"] for s in slow.status() if s["params"]["model_id"] == "org/slow"],
        landed=(models / "bg/slow/model.onnx").exists())
    begun_task = slow.start("org/slow", "bg_removal")
    finished.wait()
    ok(begun_task["status"] == "processing" and at_fetch == {"status": ["processing"], "landed": False},
       f"настоящий каталог: пока источник отдаёт часть, установка уже видна прозвонкой, а части под именем нет — "
       f"ответ start не ждал загрузки ({at_fetch})")

    real_inventory, probed = cat.inventory, []
    cat.inventory = lambda: probed.append(busy(Files(cat.inventory_file.parent).lock(cat.inventory_file.name))) \
        or real_inventory()
    try:
        cat._remember(twice["entry"])
    finally:
        del cat.inventory
    ok(probed == [True], f"опись читается и пишется под своим замком: проба во время чтения — занято ({probed})")

    cache = Files(cat.cache_dir)
    index_rel = f"voices/piper/{cat.revision('voices/piper')}/voices.json"
    cache.place(index_rel).unlink(missing_ok=True)
    small: list = []
    during[("voices/piper", "voices.json")] = lambda: small.append(busy(cache.lock(index_rel)))
    voices = cat._piper_index(cat.source("tts"))
    ok(small == [True] and "ru_RU-test-low" in voices,
       f"малый файл источника качается под замком файла: остаток владельца source один на писателя ({small})")

    Files(installs).note("w.json", "чужой", {"etag": "x"})
    try:
        listed = [s["task_id"] for s in ModelInstaller(cat).status()]
    except Exception as e:
        listed = [type(e).__name__]
    ok(listed and all((installs / f"{i}.json").is_file() for i in listed),
       f"заметка остатка (*.part.json) в каталоге задач — не задача: список не падает и её не выдаёт ({listed})")

    print("== 19t. Ревизию источник не назвал — отказ кодом причины, а не «адрес не пропущен» ==")
    from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError

    asked_rev: list = []
    req = _hx19.Request("GET", "https://huggingface.co/api/models/org/x")
    raises = {"org/rev-gated": GatedRepoError("закрыто", response=_hx19.Response(401, request=req)),
              "org/rev-none": RepositoryNotFoundError("нет", response=_hx19.Response(404, request=req)),
              "org/rev-busy": HfHubHTTPError("перегрузка", response=_hx19.Response(503, request=req)),
              "org/rev-net": _hx19.ConnectError("сеть", request=req),
              "org/rev-odd": ValueError("чужое")}

    class RevApi:
        def repo_info(self, repo_id, repo_type="model"):
            asked_rev.append(repo_id)
            raise raises[repo_id]

    real_api, real_endpoint = _hf19.HfApi, _hf19.constants.ENDPOINT
    _hf19.HfApi = RevApi
    try:
        got = {repo: outcome(lambda: cat.revision(repo)) for repo in raises}
        said = {repo: delivered(lambda: cat.revision(repo)) for repo in ("org/rev-none", "org/rev-busy", "org/rev-net")}
        pinned = cat.revision("org/bg")
        _hf19.constants.ENDPOINT = "https://hf-mirror.example"          # как HF_ENDPOINT зеркала
        mirrored = delivered(lambda: cat.revision("org/rev-net"))
    finally:
        _hf19.HfApi, _hf19.constants.ENDPOINT = real_api, real_endpoint
    ok("hf-mirror.example" in mirrored and "huggingface.co" not in mirrored,
       f"источник — зеркало: отказ ревизии называет хост зеркала, а не huggingface.co ({mirrored})")
    ok(got == {"org/rev-gated": "MODEL_GATED", "org/rev-none": "MODEL_NOT_IN_CATALOG",
               "org/rev-busy": "MODEL_REVISION_UNKNOWN", "org/rev-net": "MODEL_REVISION_UNKNOWN", "org/rev-odd": "ValueError"},
       f"код на причину: закрыто, нет, перегрузка, сеть — свои; чужое исключение не переименовано ({got})")
    ok("huggingface.co" in said["org/rev-none"] and "503" in said["org/rev-busy"] and "ConnectError" in said["org/rev-net"],
       f"клиент видит источник и ответ: код HTTP или имя сбоя сети ({said})")
    ok(pinned == "rev-bg" and "org/bg" not in asked_rev, "закреплённая ревизия источник не спрашивает")

    print("== 19u. Имя того же вида занято другим репозиторием — отказ до записи, первая модель не тронута ==")
    info["org/foo"] = repo(put("org/foo", "model.onnx", b"1" * 200))
    info["other/foo"] = repo(put("other/foo", "model.onnx", b"2" * 200))
    info["third/foo"] = repo(put("third/foo", "model.safetensors", b"3" * 200))
    cat.install_planned(cat.место_под("org/foo", "bg_removal"))
    weights = models / "bg/foo/model.onnx"
    before_foo = (weights.read_bytes(), weights.stat().st_ino)
    mark = len(calls)
    ok(outcome(lambda: cat.место_под("other/foo", "bg_removal")) == "MODEL_NAME_TAKEN" and not downloads(mark)
       and (weights.read_bytes(), weights.stat().st_ino) == before_foo
       and [r.get("repo") for r in cat.inventory() if r.get("id") == "foo"] == ["org/foo"],
       "второй репозиторий с тем же именем того же вида — отказ до загрузки: веса и строка описи первого не тронуты")
    said = delivered(lambda: cat.место_под("other/foo", "bg_removal"))
    ok("'foo'" in said and "org/foo" in said, f"клиент видит, какое имя и кем занято ({said})")
    mark = len(calls)
    ok(outcome(lambda: cat.install_planned(cat.место_под("other/foo", "bg_removal", force=True))) == "MODEL_NAME_TAKEN"
       and not downloads(mark) and (weights.read_bytes(), weights.stat().st_ino) == before_foo,
       "force чужого репозитория — тоже отказ: force перекачивает ту же модель, а не затирает чужую")
    ok(outcome(lambda: cat.install_planned(cat.место_под("org/foo", "bg_removal", force=True))) == "",
       "тот же репозиторий — переустановка с force идёт: имя занято им самим")
    ok(outcome(lambda: cat.место_под("third/foo", "image")) == "",
       "то же имя другого вида — свой каталог и своя строка описи, отказа нет")

    print("== 19v. Каталог, установка и прозвонка моделей идут вне цикла событий — по декларации resources.yaml ==")
    import tools.media as media_tools

    ran_in: dict = {}
    loop_thread = threading.get_ident()             # `_aio.run` крутит цикл событий в этом же потоке
    real_probe_tool, real_start, real_status = media_tools.probe, ModelInstaller.start, ModelInstaller.status

    def where(name, fn):
        def wrapped(*a, **k):
            ran_in[name] = threading.get_ident()
            return fn(*a, **k)
        return wrapped

    was_cfg = _srv.CONFIG_PATH
    declared = _Eng(reactions=_REACTIONS, state_manager=_sm, resources=_srv._load_resources(cfg.parent / "resources.yaml"))
    try:
        _srv.CONFIG_PATH = cfg.parent
        _srv.register_basic_tools(declared, _IDG(), _sm)
        media_tools.probe = where("media_models", real_probe_tool)
        ModelInstaller.start = where("media_model_install", real_start)
        ModelInstaller.status = where("media_install_status", real_status)
        for name, params in (("media_models", {"scope": "installed"}),
                             ("media_model_install", {"model_id": "org/bg", "kind": "bg_removal", "confirm": True}),
                             ("media_install_status", {})):
            _aio.run(declared.call(name, params))
    finally:
        _srv.CONFIG_PATH = was_cfg
        media_tools.probe, ModelInstaller.start, ModelInstaller.status = real_probe_tool, real_start, real_status
    ok(set(ran_in) == {"media_models", "media_model_install", "media_install_status"}
       and loop_thread not in ran_in.values(),
       f"снимок железа, запуск установки и прозвонка — не в потоке цикла событий ({ran_in}, цикл {loop_thread})")


from core.providers.catalog import ModelCatalog as _MC19

_real19 = (_hf19.model_info, _t19.sleep, _hx19.request, _MC19.__dict__["transport"], _MC19.__dict__["resolve"])
_work19 = Path(tempfile.mkdtemp(prefix="model_fetch_"))
_rec19: list[str] = []
_catch19 = _log19.Handler()
_catch19.emit = lambda r: _rec19.append(r.getMessage())
_hlog19 = _log19.getLogger("core.providers.download")            # разбор Retry-After — общий, у загрузчика
_hlog19.addHandler(_catch19)
sys.path.insert(0, str(ROOT / "scripts"))
try:
    _model_fetch(_work19, _rec19)
except Exception as _e19:  # обрыв §19 — провал с причиной, а не трасса: проверки до обрыва уже засчитаны
    ok(False, f"§19 оборвался: {type(_e19).__name__}: {_e19}")
finally:
    _hf19.model_info, _t19.sleep, _hx19.request, _MC19.transport, _MC19.resolve = _real19
    _hlog19.removeHandler(_catch19)
    sys.path.remove(str(ROOT / "scripts"))
    _sh19.rmtree(_work19, ignore_errors=True)

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
