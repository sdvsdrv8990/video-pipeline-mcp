"""
tests/quick/test_providers.py — динамический выбор провайдера/модели по данным канала.

Standalone-прогон:  python tests/quick/test_providers.py
Проверяет: провайдер и модель приходят ИЗ ДАННЫХ (не из кода), исчерпанный лимит уводит на
объявленный fallback и говорит об этом, кольцо в данных не зацикливает, отказы идут кодами
реестра, параметры вызова = вся строка минус служебные столбцы.
"""
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
    ok(e.code == "SCHEMA_INVALID", f"битая декларация → SCHEMA_INVALID ({e.code})")
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

print("== 9. Инструмент: кто исполняет и какой моделью (шаг 2) ==")
import asyncio as _aio
import json as _json
from core.engine import Engine as _Eng
from core.ids import IDGenerator as _IDG
from core.state import StateManager as _SM
import server as _srv

_ws = Path(tempfile.mkdtemp(prefix="prov_")) / "workspace"
_ws.mkdir(parents=True)
_sm = _SM(_ws)
_eng = _Eng(state_manager=_sm)
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
    ok(True, "ссылка приходит извне — путь загрузки проходит containment (G17)")
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

# F3/F10: облачные провайдеры ОБЪЯВЛЕНЫ, но исполнителя у них нет. Честность держит этот отказ,
# а не файл-заглушка: мёртвый адаптер снят, значит отказ обязан остаться слышным и называть выход.
for _cloud, _rt in (("ElevenLabs", "tts_characters"), ("OpenAI", "images"), ("Fal", "images")):
    try:
        _reg.load(_cloud, _rt)
        ok(False, f"{_cloud} без адаптера обязан отказать, а не сделать вид, что исполнил")
    except ProviderError as _ec:
        ok(_ec.code == "PROVIDER_ADAPTER_MISSING" and "media_provider_status" in (_ec.suggested_tool or ""),
           f"{_cloud}: отказ назван и есть чем разбираться ({_ec.code})")
        # F105: рецепт называет ТОЛЬКО имена с исполнителем — иначе он ведёт на тот же отказ.
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
except ProviderError as e:
    ok(e.code == "LOCAL_MODEL_MISSING", f"имя модели из данных не выводит за корень весов ({e.code})")
for _mod in ("core/providers/tts/piper_local.py", "core/providers/img/diffusers_local.py"):
    _msrc = (ROOT / _mod).read_text(encoding="utf-8")
    _code = "\n".join(_line for _line in _msrc.splitlines() if not _line.lstrip().startswith("#"))
    _code = _code.split('"""', 2)[-1]                     # шапку модуля не считаем кодом
    ok("SUBDIR" not in _code and 'models_dir /' not in _code,
       f"{Path(_mod).name}: подкаталог весов не зашит в код")

_tts_model = _reg.models_dir / "tts" / "ru_RU-dmitri-medium.onnx"
_have_tts = _tts_model.is_file()
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
    print("  ⚠ веса локальной озвучки не вытянуты (scripts/models.py pull) — живой прогон пропущен")

# Имя модели — данные, которые правит ИИ: путь наружу не читается.
(_ws / "v" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
    "L1": {**_row_local, "model": "../../../../etc/passwd"}}}}), encoding="utf-8")
_esc = _call("media_generate", table="v", resource_type="tts_characters",
             input="текст", scene_id="s1", video_slug="demo")
ok(_esc.status == "error" and _esc.error.code == "LOCAL_MODEL_MISSING",
   f"путь наружу в имени модели отбит containment ({_esc.error.code if _esc.error else 'success'})")

(_ws / "v" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
    "L1": {**_row_local, "model": "нет-такой-модели.onnx"}}}}), encoding="utf-8")
_nom = _call("media_generate", table="v", resource_type="tts_characters",
             input="текст", scene_id="s1", video_slug="demo")
ok(_nom.status == "error" and _nom.error.code == "LOCAL_MODEL_MISSING"
   and "media_model_install" in (_nom.error.recovery.reason if _nom.error.recovery else ""),
   "нет весов → честный отказ и назван инструмент, которым модель ставят")

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
   "вариант весов приходит из ОПИСИ: ИИ не обязан знать, что лежит на диске (F80)")
ok(_ldk({"variant": "fp32"}, {"variant": "fp16"})["variant"] == "fp32",
   "непустой столбец строки перекрывает опись — но только непустой")
ok(_ldk({"device_map": "balanced", "max_memory": {0: "8GiB"}}, {}) ==
   {"device_map": "balanced", "max_memory": {0: "8GiB"}},
   "новый рычаг библиотеки = строка в декларации, а не ветка в коде")
ok(_ldk({"выдуманный_столбец": "x", "steps": 4}, {}) == {},
   "чего в объявленной карте нет — в загрузчик не уезжает, даже если это знакомый столбец")
ok(_reg.model_entry("такой-модели-нет") == {},
   "неизвестной модели опись не выдумывает свойств")

# Модель берётся из ОПИСИ, а не зашита в тест: источник локальных моделей один (S23).
from core.providers.catalog import ModelCatalog as _MCat
from core.providers.hardware import probe as _probe

_gpu_decl = (yaml.safe_load(CFG.read_text(encoding="utf-8"))["local"] or {}).get("gpu") or {}
_img_entry = next((e for e in _MCat(CFG, _reg.models_dir).installed()
                   if e["kind"] == "image" and e["present"]), None)
if _img_entry:
    (_ws / "i").mkdir()
    (_ws / "i" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
        "I1": {"resource_type": "image_generations", "provider": "Local_diffusers",
               "fallback_provider": "", "daily_limit": -1, "current_usage": 0,
               # `variant` в строке НЕТ намеренно: он приходит из описи (F80). Если регрессия
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
    print("  ⚠ веса локальной генерации картинок не вытянуты — живой прогон пропущен")

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
import shutil as _sh
_acfg_dir = Path(tempfile.mkdtemp(prefix="async_cfg_")) / "config"
_sh.copytree(ROOT / "config", _acfg_dir)
_adata = yaml.safe_load(CFG.read_text(encoding="utf-8"))
_adata["adapters"]["by_provider"]["Обл_async"] = "fake_async:_FakeAsync"
_adata["adapters"]["by_provider"]["Обл_link"] = "fake_async:_FakeLink"
(_acfg_dir / "providers.yaml").write_text(yaml.safe_dump(_adata, allow_unicode=True), encoding="utf-8")
# Тестовый провайдер живёт на петле — послабление объявлено в КОПИИ конфига, боевой запрет цел.
_mdata = yaml.safe_load((_acfg_dir / "media_tasks.yaml").read_text(encoding="utf-8"))
_mdata["download"]["fetch"].update({"allow_schemes": ["http", "https"], "block_private_hosts": False})
(_acfg_dir / "media_tasks.yaml").write_text(yaml.safe_dump(_mdata, allow_unicode=True), encoding="utf-8")

(_ws / "a").mkdir()
_arow = {"resource_type": "tts_characters", "provider": "Обл_async", "fallback_provider": "",
         "daily_limit": 100, "current_usage": 0, "warning_threshold": 90, "model": "облачная",
         "response_format": "wav", "sync_mode": False, "usage_unit": "character"}
(_ws / "a" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
    "A1": dict(_arow)}}}), encoding="utf-8")
_cfg_was = _srv.CONFIG_PATH
try:
    _srv.CONFIG_PATH = _acfg_dir
    _eng_a = _Eng(state_manager=_sm)
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
from core.providers import ResultDownloader

_dl = ResultDownloader(ROOT / "config" / "media_tasks.yaml")
for _case, _url in (("http вместо https", "http://example.com/a.wav"),
                    ("петля", "https://127.0.0.1/a.wav"),
                    ("localhost по имени", "https://localhost/a.wav"),
                    ("метаданные облака", "https://169.254.169.254/latest/meta-data")):
    try:
        _dl.check_url(_url)
        ok(False, f"{_case} должен отбиваться")
    except TaskCycleError as e:
        ok(e.code == "DOWNLOAD_FORBIDDEN", f"{_case} → DOWNLOAD_FORBIDDEN ({e.code})")

# Предел размера считается ПО ХОДУ записи: живой сервер отдаёт больше, чем разрешено.
_lim_cfg = Path(tempfile.mkdtemp(prefix="dlcfg_")) / "media_tasks.yaml"
_lim_cfg.write_text(yaml.safe_dump({"download": {"fetch": {
    "allow_schemes": ["http"], "block_private_hosts": False, "max_bytes": 1000,
    "timeout_sec": 10}}}), encoding="utf-8")


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
    ResultDownloader(_lim_cfg).fetch(f"http://127.0.0.1:{_srv_http.server_port}/big.wav", _out)
    ok(False, "превышение предела должно падать")
except TaskCycleError as e:
    ok(e.code == "DOWNLOAD_FORBIDDEN", f"предел размера обрывает загрузку ({e.code})")
ok(not _out.exists(), "недописанный файл удалён, а не выдан за результат")
_srv_http.shutdown()

print("== 14. Ключи провайдеров: лежат в канале, зашифрованы, ИИ недоступны (S23) ==")
from core.paths import (BUILTIN_SECRET_DIRS, PathEscapeError, SecretAccessError,
                        configure_secret_dirs, is_secret_path, safe_resolve)
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
    safe_resolve("ch/.secrets/provider_keys.enc", _ws)
    ok(False, "закрытый каталог обязан отказывать")
except SecretAccessError:
    ok(True, "закрытый каталог → SecretAccessError, а не общий выход за область")
except PathEscapeError:
    ok(False, "закрытый каталог назван «выходом за область» — причина подменена")

print("== 14b. Провайдеру нужен ключ: сервер говорит «прав нет», а не молчит ==")
_kcfg_dir = Path(tempfile.mkdtemp(prefix="key_cfg_")) / "config"
_sh.copytree(ROOT / "config", _kcfg_dir)
_kdata = yaml.safe_load(CFG.read_text(encoding="utf-8"))
_kdata["adapters"]["by_provider"]["Обл_ключ"] = {"adapter": "fake_async:_FakeKeyed", "requires_key": True}
(_kcfg_dir / "providers.yaml").write_text(yaml.safe_dump(_kdata, allow_unicode=True), encoding="utf-8")


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
    _eng_k = _Eng(state_manager=_sm)
    _srv.register_basic_tools(_eng_k, _IDG(), _sm)
    _kcall = lambda tool, **p: _aio.run(_eng_k.call(tool, p))       # noqa: E731 — локальный хелпер

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
    _log = (ROOT / "_SESSION_LOG.md")
    ok(not _log.exists() or _KEY not in _log.read_text(encoding="utf-8"),
       "и не попал в журнал фактов")

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
    _mcat.check_size(999999)
    ok(False, "превышение предела размера должно падать")
except ProviderError as e:
    ok(e.code == "LOCAL_MODEL_MISSING" and "max_total_mb" in e.reason,
       "размер сверх объявленного предела — отказ с указанием, где предел правится")
try:
    _mcat.check_repo("https://civitai.example/api/download/123")
    ok(False, "чужой источник должен отбиваться")
except ProviderError as e:
    ok("allow_sources" in e.reason, "чужой САЙТ как источник весов не берётся: модель — исполняемое содержимое")

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
# обязан честно показать пустой список, а не считаться сломанным (F91).
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
        self.seconds = seconds
        self.fail = fail
        self.install_rules = {}

    def install(self, model_id, kind, progress=lambda _m: None):
        _time.sleep(self.seconds)
        if self.fail:
            raise self.fail
        (self.models_dir / "весы.onnx").write_bytes(b"x" * 2048)
        return {"id": model_id, "kind": kind, "path": "весы.onnx", "mb": 0.002, "refused": []}


_idir = Path(tempfile.mkdtemp(prefix="inst_"))
_inst_ok = ModelInstaller(_SlowCatalog(_idir), heartbeat_sec=0.1, stale_after_sec=5)
_st = _inst_ok.start("модель-1", "tts")
ok(_st["phase"] == "running" and _st["install_id"],
   "установка запущена и вернула идентификатор, а не ждала гигабайты в одном вызове")
try:
    _inst_ok.start("модель-1", "tts")
    ok(False, "вторая установка тех же весов должна отбиваться")
except ProviderError as e:
    ok(e.code == "INVALID_ACTION", f"две загрузки одних весов в один каталог не пускаются ({e.code})")
for _ in range(50):
    _cur = _inst_ok.status(_st["install_id"])[0]
    if _cur["phase"] != "running":
        break
    _time.sleep(0.1)
ok(_cur["phase"] == "done" and _cur["path"] == "весы.onnx",
   f"успех виден прозвонкой, с путём результата ({_cur['phase']})")
ok(_cur["elapsed_sec"] > 0, "видно, сколько заняла установка")

_inst_bad = ModelInstaller(
    _SlowCatalog(Path(tempfile.mkdtemp(prefix="instf_")), seconds=0.2,
                 fail=ProviderError("LOCAL_MODEL_MISSING", "источник оборвал соединение",
                                    reason="повтори — скачанное не выбрасывается")),
    heartbeat_sec=0.1, stale_after_sec=5)
_bad_id = _inst_bad.start("модель-2", "tts")["install_id"]
for _ in range(50):
    _bad_state = _inst_bad.status(_bad_id)[0]
    if _bad_state["phase"] != "running":
        break
    _time.sleep(0.1)
ok(_bad_state["phase"] == "failed" and _bad_state["code"] == "LOCAL_MODEL_MISSING"
   and "оборвал" in _bad_state["error"],
   f"сбой посреди установки виден кодом и причиной, а не тишиной ({_bad_state['phase']})")

# Сервер перезапустили посреди загрузки: «идёт» здесь было бы враньём.
_stale_dir = Path(tempfile.mkdtemp(prefix="insts_"))
_inst_stale = ModelInstaller(_SlowCatalog(_stale_dir), heartbeat_sec=0.1, stale_after_sec=1)
_sid = _inst_stale.start("модель-3", "tts")["install_id"]
_sfile = _inst_stale.state_dir / f"{_sid}.json"
_time.sleep(0.8)
_frozen = _json.loads(_sfile.read_text(encoding="utf-8"))
_frozen.update({"phase": "running", "heartbeat": _time.time() - 999})
_sfile.write_text(_json.dumps(_frozen), encoding="utf-8")
_stale_state = _inst_stale.status(_sid)[0]
ok(_stale_state["phase"] == "stale" and "Повтори" in _stale_state["reason"],
   f"замершая установка называется прерванной и зовёт повторить ({_stale_state['phase']})")

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

_ocfg_dir = Path(tempfile.mkdtemp(prefix="online_")) / "config"
_sh.copytree(ROOT / "config", _ocfg_dir)
_odata = yaml.safe_load(CFG.read_text(encoding="utf-8"))
# https обязателен с ключом — для двойника послабление объявлено в КОПИИ конфига, боевой запрет цел.
_odata["online"]["live"]["Двойник"] = {"url": f"https://127.0.0.1:{_mserv.server_port}/v1/models",
                                       "auth": "bearer", "items": "data", "id_field": "id"}
(_ocfg_dir / "providers.yaml").write_text(yaml.safe_dump(_odata, allow_unicode=True), encoding="utf-8")

from core.providers.catalog import OnlineCatalog as _OC

_oc = _OC(_ocfg_dir / "providers.yaml")
try:
    _oc.live("Двойник", "ключ")
    ok(False, "https-двойник без сертификата должен падать по сети, а не по проверке схемы")
except ProviderError as e:
    ok(e.code == "PROVIDER_FAILED", f"недоступный адрес → PROVIDER_FAILED, не молчание ({e.code})")

# Тот же двойник по http: проверяем, что схема ловится ДО того, как ключ уйдёт в открытый канал.
_odata["online"]["live"]["Двойник"]["url"] = f"http://127.0.0.1:{_mserv.server_port}/v1/models"
(_ocfg_dir / "providers.yaml").write_text(yaml.safe_dump(_odata, allow_unicode=True), encoding="utf-8")
_oc2 = _OC(_ocfg_dir / "providers.yaml")
try:
    _oc2.live("Двойник", "ключ")
    ok(False, "http с ключом должен отбиваться")
except ProviderError as e:
    ok(e.code == "DOWNLOAD_FORBIDDEN" and not _Models.seen_auth,
       f"с ключом ходим только по https — запрос НЕ ушёл ({e.code})")

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
except Exception as e:                                        # noqa: BLE001
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
ok(_hw["ram_available_mb"] <= _hw["ram_total_mb"],
   "доступно не больше общего — читается MemAvailable, а не выдуманное число")
ok(_hw["disk_free_mb"] > 0, "свободное место под веса известно")
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

from huggingface_hub import constants as _hfc

ok(_mc.apply_transfer() == "https" and _hfc.HF_HUB_DISABLE_XET,
   "протокол загрузки весов берётся из декларации: объявлен https → Xet выключен")
_xet_cfg = _ws.parent / "providers_xet.yaml"
_xet_data = yaml.safe_load(CFG.read_text(encoding="utf-8"))
_xet_data["local"]["install"]["transfer"] = "xet"
_xet_cfg.write_text(yaml.safe_dump(_xet_data, allow_unicode=True), encoding="utf-8")
ok(ModelCatalog(_xet_cfg, ROOT / "vendor" / "models").apply_transfer() == "xet"
   and not _hfc.HF_HUB_DISABLE_XET,
   "поменяли декларацию — поменялся протокол: значение не зашито в код и не берётся из среды")
_mc.apply_transfer()        # вернуть как объявлено в проекте

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

from PIL import Image as _PILImage                              # noqa: E402

from core.providers.catalog import ModelCatalog as _MC          # noqa: E402

_cat12 = _MC(CFG, _reg.models_dir)
ok(_cat12.prefer(["onnx/model_fp16.onnx", "onnx/model.onnx"]) == "onnx/model.onnx",
   "из одинаковых по смыслу файлов берётся объявленный первым, а не первый попавшийся")
ok(str(_cat12.cache_dir).startswith(str(_reg.models_dir)),
   f"загрузчик складывает скачанное внутрь каталога зависимостей проекта ({_cat12.cache_dir})")

_installed12 = {m["id"]: m for m in _cat12.installed() if m["present"]}
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
import httpx as _httpx                                       # noqa: E402

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
    if url.endswith("/status"):
        return _Resp(200, {"status": "COMPLETED"})
    if "predictions/pred1" in url:
        _seen["polls"] += 1
        return _Resp(200, {"status": "processing"} if _seen["polls"] < 2 else
                     {"status": "succeeded", "output": ["https://replicate.delivery/out.png"]})
    return _Resp(200, {"image": {"url": "https://fal.media/out.png"}})


_httpx.request, _httpx.get, _httpx.stream = (
    _stand_request, _stand_get, lambda m, u, **kw: _Stream(_PNG))
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
    _got13 = _ad13.fetch(_waited13["answer"], _ws / "up13.png")
    ok(_got13.read_bytes() == _PNG,
       "результат достан по объявленному пути output[0] и скачан общими правилами загрузки")

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
from core.providers import ModelSpec                          # noqa: E402

_ms = ModelSpec(_reg)

# Локальная модель описывает себя сама — сетью для этого ходить незачем.
# Веса вне git: на машине без них проверять нечего, и это ПРОПУСК с причиной, а не провал —
# иначе набор зелёный только там, где модель уже поставлена (в CI он падал именно так).
_sp_local = _ms.of("Local_diffusers", "sd-turbo")
if _sp_local["source"] == "model_files":
    ok(_sp_local["known"], f"локальная модель описана своими файлами, без сети ({_sp_local['source']})")
    _props = _sp_local["schema"].get("properties") or {}
    ok("512x512" in str(_props.get("img_size", {}).get("default")),
       f"нативное разрешение прочитано из модели ({_props.get('img_size', {}).get('default')})")
else:
    print("  ⚠ веса sd-turbo не поставлены (media_model_install) — спека из файлов не проверена")

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
# модели, поэтому без поставленных весов проверять нечего — пропуск с причиной (F91).
_spec_tool = _call("media_models", scope="spec", provider="Local_diffusers", model="sd-turbo")
if _spec_tool.status == "success" and _spec_tool.data.get("source") == "model_files":
    ok(bool(_spec_tool.data["parameters"]),
       f"инструмент отдаёт спеку с источником ({_spec_tool.data.get('source')})")
    ok(_spec_tool.facts and _spec_tool.facts[0].type == "ModelSpecRead",
       "чтение спеки приходит фактом контракта (тип заведён в KNOWN_FACT_TYPES, D25)")

    # И главное: вызов не состоится, если строка канала просит невозможное.
    (_ws / "spec14").mkdir()
    _PILImage.new("RGB", (32, 32), "#101010").save(_ws / "spec14" / "in.png")
    (_ws / "spec14" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
        "S1": {"resource_type": "image_generations", "provider": "Local_diffusers",
               "fallback_provider": "", "daily_limit": -1, "current_usage": 0,
               "warning_threshold": -1, "model": "sd-turbo", "img_size": 4096}}}}), encoding="utf-8")
    _deny14 = _call("media_generate", table="spec14", resource_type="image_generations",
                    input="кадр", scene_id="s1")
    ok(_deny14.status == "error" and _deny14.error.code == "VALIDATION_ERROR",
       f"строка канала просит невозможное — отказ ДО вызова ({_deny14.error.code if _deny14.error else 'успех!'})")
    ok("4096" in (_deny14.error.message if _deny14.error else ""),
       "в отказе названо, ЧТО именно не подошло")
else:
    print("  ⚠ веса sd-turbo не поставлены — спека сквозь инструмент и отказ ДО вызова не проверены")

print("== 15. Поднятая модель переживает вызов: пул с бюджетом (S24) ==")
import time as _time                                          # noqa: E402

from core.providers.pool import ModelPool as _Pool            # noqa: E402
import core.providers.pool as _pool_mod                       # noqa: E402

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

# Простой: залежавшееся убирается при СЛЕДУЮЩЕМ обращении, а не демоном в фоне (S15).
_pool_mod._ENTRIES.clear(); _lifts["n"] = 0
_p_idle = _Pool({"enabled": True, "budget_ratio": 0.9, "idle_ttl_sec": 0.05}, _hw15)
_p_idle.get("old", _lift("old"), need_bytes=1000, device="cpu")
_time.sleep(0.1)
_p_idle.get("new", _lift("new"), need_bytes=1000, device="cpu")
ok({r["key"] for r in _p_idle.stats()["models"]} == {"new"},
   "модель, которой давно не пользовались, выгружена при следующем обращении")

# Выключатель обязан выключать (F54-класс): без пула модель поднимается каждый раз.
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
    _keys15 = [r["key"] for r in _reg.pool.stats()["models"]]
    ok(any(k.startswith("onnx|") for k in _keys15) and _second <= _first,
       f"модель осталась поднятой между вызовами инструмента ({_first:.2f} с → {_second:.2f} с)")

print("== 16. Кто формирует задачу и куда кладёт результат (инварианты под раннер, S24) ==")
import re as _re16                                            # noqa: E402

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
import socket as _sock17                                      # noqa: E402
import subprocess as _sp17                                    # noqa: E402
import httpx as _hx17                                         # noqa: E402
from core.providers.img.http_image import HttpImageAPI as _HTTP17  # noqa: E402
from core.runner import RunnerSupervisor as _Sup17            # noqa: E402

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

# Стенд: свободный порт + КОПИЯ конфига, состояние и журнал в темпе. Боевой vendor/ не трогаем —
# иначе тест поднимал бы раннер поверх настоящего и стирал его запись о запуске.
_p17 = _sock17.socket(); _p17.bind(("127.0.0.1", 0)); _port17 = _p17.getsockname()[1]; _p17.close()
_tmp17 = Path(tempfile.mkdtemp(prefix="runner_"))
_cfg17 = _tmp17 / "config"
_sh.copytree(ROOT / "config", _cfg17)
_d17 = yaml.safe_load((_cfg17 / "providers.yaml").read_text(encoding="utf-8"))
_d17["online"]["http"]["Local_runner"]["url"] = f"http://127.0.0.1:{_port17}/run"
_d17["local"]["runner"].update({"state_file": str(_tmp17 / "state.json"),
                                "log_file": str(_tmp17 / "runner.log")})
_d17["local"]["models_dir"] = str(_reg.models_dir)
(_cfg17 / "providers.yaml").write_text(yaml.safe_dump(_d17, allow_unicode=True), encoding="utf-8")

_row17 = {**_row_bg, "provider": "Local_runner"}
(_ws / "pic" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
    "B1": _row17}}}), encoding="utf-8")

_was17 = _srv.CONFIG_PATH
try:
    _srv.CONFIG_PATH = _cfg17
    _eng17 = _Eng(state_manager=_sm)
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

    if _start17.status == "success":
        _tok17 = _json.loads((_tmp17 / "state.json").read_text(encoding="utf-8"))["token"]
        _base17 = f"http://127.0.0.1:{_port17}"

        # Токен не украшение: на петле сидит не только сервер.
        ok(_hx17.post(f"{_base17}/run", json={"kind": "bg_removals"}, timeout=10).status_code == 401,
           "без токена раннер не исполняет ничего")
        _alive17 = _hx17.get(f"{_base17}/health", timeout=10).json()
        ok(_alive17.get("ok") and "pool" not in _alive17,
           "живость видна без токена, а содержимое пула — нет")
        ok("pool" in _hx17.get(f"{_base17}/health", headers={"X-Runner-Token": _tok17},
                               timeout=10).json(), "с токеном /health показывает, что держится поднятым")

        # Раннер доверяет вызывающему не больше, чем сервер клиенту — даже на своей машине.
        _esc17 = _hx17.post(f"{_base17}/run", headers={"X-Runner-Token": _tok17}, timeout=10,
                            json={"kind": "bg_removals", "source": "pic/frame.png",
                                  "target": "../../../tmp/утёк.png", "params": {}})
        ok(_esc17.status_code == 403 and _esc17.json().get("code") == "PATH_ESCAPE",
           f"путь наружу рабочей области раннер не принимает ({_esc17.json().get('code')})")
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

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
