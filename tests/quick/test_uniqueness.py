"""
tests/quick/test_uniqueness.py — A7.2: локальный расчёт уникальности + контракт готовности.

Standalone-прогон:  python tests/quick/test_uniqueness.py
Проверяет: похожесть n-gram, «тихий столбец» (выключенный тип не входит в расчёт), отличие
«нет данных» от «нулевой уникальности», readiness full/partial/empty, честный ноль на пустом
входе, декларативность (ни одного типа фрагмента и порога в коде), инструмент on-demand.
"""
import asyncio
import json
import sys
import tempfile
import warnings
from pathlib import Path

import yaml

warnings.simplefilter("error", UserWarning)  # чужой Fact.type (D25) → падение

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.engine import Engine
from core.ids import IDGenerator
from core.state import StateManager
from core.uniqueness import UniquenessEngine, UniquenessError
import server

CFG = ROOT / "config" / "uniqueness.yaml"

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


PROFILE = [
    {"fragment_type": "svg_bg", "enabled": True, "niche_weight": 0.3},
    {"fragment_type": "svg_character", "enabled": True, "niche_weight": 0.4},
    # Вес выключенного типа НЕ нулевой намеренно: с нулём проверка «тихого столбца» проходила бы
    # потому, что вес обнуляет вклад, а не потому, что тумблер гасит тип.
    {"fragment_type": "sound", "enabled": False, "niche_weight": 0.5},
]

print("== 1. Похожесть текста считается локально, окно — из декларации ==")
uniq = UniquenessEngine(CFG)
ok(uniq.similarity("один и тот же текст целиком", ["один и тот же текст целиком"]) == 1.0,
   "полный повтор → похожесть 1.0")
ok(uniq.similarity("совсем другое про рыбалку и лодку", ["герой идёт на работу под дождём"]) == 0.0,
   "непересекающийся текст → похожесть 0.0")
_part = uniq.similarity("герой просыпается и идёт на работу под дождём",
                        ["герой просыпается и идёт домой при солнце"])
ok(0.0 < _part < 1.0, f"частичное совпадение даёт промежуточное значение ({_part})")
ok(uniq.shingles("") == set(), "пустой текст не даёт шинглов")
ok(len(uniq.shingles("два слова")) == 1, "текст короче окна — один шингл, а не пусто")

print("== 2. «Тихий столбец»: выключенный тип не входит в расчёт вовсе ==")
_with_off = uniq.compute(text="новый текст про совершенно другое дело",
                         corpus=["старый про рыбалку"],
                         fragments={"svg_bg": ["A", "B"], "svg_character": ["C", "D"],
                                    "sound": ["S", "S", "S"]},   # повторы в ВЫКЛЮЧЕННОМ типе
                         profile_rows=PROFILE)
ok(_with_off["scores"]["scene_score"] == 1.0,
   f"повторы в выключенном типе не роняют оценку (получено {_with_off['scores']['scene_score']})")
_on = [dict(r) for r in PROFILE]
_on[2]["enabled"] = True
_with_on = uniq.compute(text="новый текст про совершенно другое дело", corpus=["старый про рыбалку"],
                        fragments={"svg_bg": ["A", "B"], "svg_character": ["C", "D"],
                                   "sound": ["S", "S", "S"]},
                        profile_rows=_on)
ok(_with_on["scores"]["scene_score"] < 1.0,
   "включённый тумблер тех же данных МЕНЯЕТ результат — профиль реально читается (анти-D2)")

print("== 3. «Нет данных» ≠ «нулевая уникальность» ==")
_gap = uniq.compute(text="новый текст про совершенно другое дело", corpus=["старый про рыбалку"],
                    fragments={"svg_bg": ["A", "B"]},          # svg_character включён, но пуст
                    profile_rows=PROFILE)
ok(_gap["fragment_gaps"] == ["svg_character"],
   f"включённый тип без применений назван поимённо (получено {_gap['fragment_gaps']})")
ok(_gap["readiness"] == "partial", "пробел по типу делает расчёт неполным, а не полным")
ok(_gap["scores"]["scene_score"] == 1.0,
   "тип без данных не занижает оценку соседей — его вес убран из знаменателя")

print("== 4. Готовность: full / partial / empty ==")
_full = uniq.compute(text="новый текст про совершенно другое дело", corpus=["старый про рыбалку"],
                     fragments={"svg_bg": ["A"], "svg_character": ["C"]}, profile_rows=PROFILE)
ok(_full["readiness"] == "full", "все входы на месте → full")
ok(set(_full["scores"]) == {"script_score", "scene_score"}, "при full посчитаны ВСЕ объявленные оценки")
_partial = uniq.compute(text="есть текст", fragments={"svg_bg": ["A"], "svg_character": ["C"]},
                        profile_rows=PROFILE)
ok(_partial["readiness"] == "partial" and _partial["missing_inputs"] == {"script_score": ["corpus"]},
   f"нет корпуса → partial с точным именем входа ({_partial['missing_inputs']})")
ok("script_score" not in _partial["scores"], "оценка без входа не выдумывается")
ok(_partial["composed"] == _partial["scores"]["scene_score"],
   "итог пере-нормирован по посчитанным слагаемым — неполнота не выдаётся за низкое качество")

_empty = uniq.compute()
ok(_empty["readiness"] == "empty" and _empty["scores"] == {}, "нет ничего → empty")
ok(_empty["composed"] == 0.0, "пустой вход даёт объявленный ноль, а НЕ «100% уникально»")
ok(_empty["alert"] is None, "на пустом входе сигнал не поднимается — тревожиться не о чем")

print("== 5. Пороги сигнала — из декларации, не из кода ==")
_dup = uniq.compute(text="герой просыпается и идёт на работу под дождём каждый день",
                    corpus=["герой просыпается и идёт на работу под дождём каждый день"],
                    fragments={"svg_bg": ["A"], "svg_character": ["C"]}, profile_rows=PROFILE)
ok(_dup["scores"]["script_score"] == 0.0, "текст повторён целиком → уникальность текста 0.0")
# Решение владельца S22: ХУДШАЯ ОЦЕНКА РЕШАЕТ. Композиция здесь = 0.5 (мягкий alert), но текст
# скопирован слово в слово (0.0) — сигнал обязан быть critical, иначе плагиат прячется за сценой.
ok(_dup["composed"] == 0.5, f"композиция усредняет провал до 0.5 (получено {_dup['composed']})")
ok(_dup["alert"] == "critical",
   f"худшая оценка решает: провал по тексту → critical, а не alert (получено {_dup['alert']})")
ok(_dup["alert_sources"] == ["script_score"],
   f"сервер называет, ЧТО пробило порог (получено {_dup['alert_sources']})")
_clean = uniq.compute(text="совсем новое про другое дело целиком", corpus=["старое про рыбалку"],
                      fragments={"svg_bg": ["A"], "svg_character": ["C"]}, profile_rows=PROFILE)
ok(_clean["alert"] is None and _clean["alert_sources"] == [],
   "всё уникально → сигнала нет и источников нет")
_src = (ROOT / "core/uniqueness/uniqueness_core.py").read_text(encoding="utf-8")
ok(not any(t in _src for t in ("svg_bg", "svg_character", "music", "transition")),
   "в коде расчёта нет ни одного типа фрагмента — они приходят данными")
ok("0.6" not in _src and "0.4" not in _src, "пороги в коде не зашиты — только в декларации")

print("== 6. Инструмент on-demand: читает данные проекта, называет источник профиля ==")
_tmp = Path(tempfile.mkdtemp())
_ws = _tmp / "workspace"
_ws.mkdir()
_sm = StateManager(_ws)
_eng = Engine(state_manager=_sm)
server.register_basic_tools(_eng, IDGenerator(), _sm)
(_ws / "v1").mkdir()


def _call(tool, **params):
    return asyncio.run(_eng.call(tool, params))


_r = _call("uniqueness_check", table="v1")
ok(_r.status == "success" and _r.data["readiness"] == "empty",
   "пустой проект → успех с readiness=empty, а не ошибка")
ok(_r.data["profile_source"] == "declaration",
   f"профиля в данных нет → взят дефолт декларации, и это НАЗВАНО ({_r.data['profile_source']})")
ok(any(f.type == "UniquenessIncomplete" for f in _r.facts),
   "неполнота приходит отдельным фактом, а не тонет в числе")

(_ws / "v1" / "read.json").write_text(json.dumps({
    "SCRIPT_PATTERNS_USED": {"schema": {}, "rows": {
        "P1": {"pattern_description": "герой просыпается и идёт на работу под дождём каждый день"},
        "P2": {"pattern_description": "герой просыпается и идёт на работу под дождём каждый день"}}},
    "ASSETS_USED": {"schema": {}, "rows": {
        "A1": {"asset_type": "svg_bg", "asset_id": "BG_1"},
        "A2": {"asset_type": "svg_character", "asset_id": "CH_1"}}},
    "SCENE_PROFILE": {"schema": {}, "rows": {
        "R1": {"fragment_type": "svg_bg", "enabled": True, "niche_weight": 0.3},
        "R2": {"fragment_type": "svg_character", "enabled": True, "niche_weight": 0.4}}},
}), encoding="utf-8")

_r2 = _call("uniqueness_check", table="v1", row_id="P1")
ok(_r2.data["profile_source"] == "project", "профиль из данных канала перекрывает дефолт декларации")
ok(_r2.data["readiness"] == "full", f"все входы на месте → full (получено {_r2.data['readiness']})")
ok(_r2.data["scores"]["script_score"] == 0.0, "дубль соседней строки виден как нулевая уникальность текста")
ok(_r2.data["alert"] == "critical" and any(f.type == "UniquenessAlert" for f in _r2.facts),
   "сигнал доехал и в данные, и в факты контракта")
ok(_r2.data["alert_sources"] == ["script_score"], "источник сигнала доехал до клиента")
ok(all(f.type != "UniquenessIncomplete" for f in _r2.facts),
   "при полных данных факта неполноты нет")

print("== 7. Битая декларация не глушится (иначе расчёт тихо потеряет параметры, D2) ==")
_bad = Path(tempfile.mkdtemp()) / "uniqueness.yaml"
_bad.write_text("ngram: [не словарь\n", encoding="utf-8")
try:
    UniquenessEngine(_bad).config
    ok(False, "битый YAML должен падать кодом реестра")
except UniquenessError as e:
    ok(e.code == "SCHEMA_INVALID", f"битая декларация → SCHEMA_INVALID (получено {e.code})")
try:
    UniquenessEngine(Path(tempfile.mkdtemp()) / "нет.yaml").config
    ok(False, "отсутствие декларации должно падать")
except UniquenessError as e:
    ok(e.code == "TEMPLATE_NOT_FOUND", f"нет декларации → TEMPLATE_NOT_FOUND (получено {e.code})")

print("== 8. Параметры расчёта реально грузятся (правка конфига меняет поведение) ==")
_cfg2 = Path(tempfile.mkdtemp()) / "uniqueness.yaml"
_data = yaml.safe_load(CFG.read_text(encoding="utf-8"))
_data["ngram"]["size"] = 12                     # окно шире текста → совпадений не найдёт
_cfg2.write_text(yaml.safe_dump(_data, allow_unicode=True), encoding="utf-8")
_wide = UniquenessEngine(_cfg2).similarity("герой просыпается и идёт на работу",
                                           ["герой просыпается и идёт домой"])
_narrow = uniq.similarity("герой просыпается и идёт на работу", ["герой просыпается и идёт домой"])
ok(_wide != _narrow, f"смена окна n-gram меняет результат ({_narrow} → {_wide}) — конфиг не декоративный")

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
