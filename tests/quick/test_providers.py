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

# Раскладка каталога весов — декларация, а не знание адаптера: правка `path` меняет, куда он смотрит.
_lcfg = Path(tempfile.mkdtemp(prefix="local_")) / "providers.yaml"
_ldata = yaml.safe_load(CFG.read_text(encoding="utf-8"))
_ldata["local"]["models_dir"] = "иной_каталог"
_ldata["local"]["catalog"][0]["path"] = "переложено/голос.onnx"
_lcfg.write_text(yaml.safe_dump(_ldata, allow_unicode=True), encoding="utf-8")
_lreg = AdapterRegistry(_lcfg, ROOT)
ok(_lreg.model_path("ru_RU-dmitri-medium") == (ROOT / "иной_каталог" / "переложено/голос.onnx").resolve(),
   "путь к весам взят из декларации целиком (каталог + path), в коде адаптера его нет")
ok(_lreg.model_path("руками_положенная.onnx") == (ROOT / "иной_каталог" / "руками_положенная.onnx").resolve(),
   "модель вне каталога трактуется как путь внутри корня весов — руками положенное тоже работает")
try:
    _lreg.model_path("../../../../etc/passwd")
    ok(False, "путь наружу должен падать")
except ProviderError as e:
    ok(e.code == "LOCAL_MODEL_MISSING", f"имя модели из данных не выводит за корень весов ({e.code})")
for _mod in ("core/providers/tts/piper_local.py", "core/providers/img/diffusers_local.py"):
    _msrc = (ROOT / _mod).read_text(encoding="utf-8")
    _code = "\n".join(l for l in _msrc.splitlines() if not l.lstrip().startswith("#"))
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
   and "models.py pull" in (_nom.error.recovery.reason if _nom.error.recovery else ""),
   "нет весов → честный отказ и названа команда, которая их принесёт")

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

_img_model = _reg.models_dir / "img" / "sd-turbo"
if _img_model.is_dir() and any(_img_model.rglob("*.safetensors")):
    (_ws / "i").mkdir()
    (_ws / "i" / "read.json").write_text(_json.dumps({"RESOURCE_LIMITS": {"schema": {}, "rows": {
        "I1": {"resource_type": "image_generations", "provider": "Local_diffusers",
               "fallback_provider": "", "daily_limit": -1, "current_usage": 0,
               "warning_threshold": -1, "model": "sd-turbo", "img_size": "512x512", "img_n": 1,
               "steps": 1, "variant": "fp16", "usage_unit": "image"}}}}), encoding="utf-8")
    _gi = _call("media_generate", table="i", resource_type="image_generations",
                input="a lighthouse on a cliff at sunrise", scene_id="scene01", video_slug="demo")
    ok(_gi.status == "success", f"локальная генерация картинки исполнена ({_gi.error.code if _gi.error else 'ok'})")
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
from core.paths import BUILTIN_SECRET_DIRS, SecretAccessError, configure_secret_dirs, is_secret_path
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
ok(_lst.status == "success" and _lst.data["models"],
   f"инструмент показывает, что стоит на машине ({len(_lst.data['models'])} моделей)")
_lst_on = _call("media_models", scope="online", kind="image", limit=5)
ok(all(m["mode"] == "image_generation" for m in _lst_on.data["models"]),
   "и что умеет шлюз онлайн — с провайдером и эндпоинтом")

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
