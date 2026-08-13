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

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
