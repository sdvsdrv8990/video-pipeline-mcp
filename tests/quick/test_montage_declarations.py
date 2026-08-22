"""
tests/quick/test_montage_declarations.py — декларации монтажа проверяются, а не подразумеваются.

Standalone-прогон:  python tests/quick/test_montage_declarations.py
Проверяет: профили рендера непротиворечивы (аспект соответствует разрешению), `render_profile`
разрешается в существующую строку, у элемента сцены есть где хранить геометрию/тайминг/обрезку/
движение, роли элемента не заведены вторым словарём, книги материализуются в .xlsx.
"""
import sys
import tempfile
from math import gcd
from pathlib import Path

import openpyxl
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TABLES = ROOT / "config" / "templates" / "tables"

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


def book(name):
    return yaml.safe_load((TABLES / f"{name}.schema.yaml").read_text(encoding="utf-8"))


def sheets(b):
    return {s["name"]: s for s in b["sheets"]}


CH = sheets(book("channel_data"))
VD = sheets(book("video_data"))

print("== профили рендера ==")
RC = CH["RENDER_CONFIG"]
_cols = {c["name"] for c in RC["columns"]}
ok("profile_id" in _cols, "строка = профиль (`profile_id`), а не отдельный параметр")
_profiles = {r["profile_id"]: r for r in RC["rows"]}
ok({"long_16x9", "short_9x16"} <= set(_profiles), f"оба формата объявлены: {sorted(_profiles)}")

# Прежняя форма противоречила себе: 1920x1080 + 16:9 при примечании «Стандарт для Shorts/Reels».
# Аспект — не свободный текст, он ВЫЧИСЛЯЕТСЯ из разрешения, поэтому и сверяется с ним.
for pid, row in _profiles.items():
    w, h = (int(x) for x in str(row["resolution"]).lower().split("x"))
    d = gcd(w, h)
    ok(f"{w // d}:{h // d}" == str(row["aspect_ratio"]),
       f"{pid}: {row['resolution']} даёт {w // d}:{h // d}, объявлено {row['aspect_ratio']}")

# Режим субтитров — свойство формата: на вертикали дорожку никто не включит.
ok(_profiles["short_9x16"]["subtitles_mode"] == "BURN", "вертикаль жжёт субтитры в кадр")
ok(_profiles["long_16x9"]["subtitles_mode"] == "TRACK", "горизонталь отдаёт дорожкой")

print("\n== fk не висит в пустоте ==")
_rp = next(c for c in VD["RENDERS"]["columns"] if c["name"] == "render_profile")
ok(_rp["flag"] == "fk", "`RENDERS.render_profile` объявлен ссылкой")
_labels = {c["name"] for c in VD["RENDERS"]["columns"]}
ok("format_label" in _labels, "`format_label` на месте (RENDERS знает про несколько форматов)")

print("\n== элемент сцены: есть где хранить то, что тянут мышью ==")
EL = {c["name"]: c for c in VD["SCENE_ELEMENTS"]["columns"]}
for need, why in [("x", "положение"), ("y", "положение"), ("width", "размер"), ("height", "размер"),
                  ("z_index", "порядок наложения"), ("time_start", "окно появления"),
                  ("time_end", "окно появления"), ("source_in", "обрезка исходника"),
                  ("source_out", "обрезка исходника"), ("speed", "темп"),
                  ("motion_path", "движение по точкам"), ("alpha_mode", "снятие фона")]:
    ok(need in EL, f"`{need}` объявлен — {why}")

# Словарь ролей живёт в SCENE_PROFILE; перечень здесь завёл бы вторую копию, и она бы разошлась.
ok(EL["element_role"]["flag"] == "fk" and "enum" not in EL["element_role"],
   "роль элемента — ссылка на `SCENE_PROFILE`, а не второй перечень")
ok(EL["alpha_mode"].get("enum") == ["AS_IS", "REMOVE_BG", "CHROMAKEY", "NONE"],
   f"словарь снятия фона объявлен значениями: {EL['alpha_mode'].get('enum')}")

print("\n== звук сцены ==")
AU = {c["name"]: c for c in VD["SCENE_AUDIO"]["columns"]}
ok(AU.get("audio_role", {}).get("enum") == ["VOICE", "MUSIC", "SOUND"], "роли звука перечислены")
for need in ("volume", "volume_curve", "speed", "fade_in_sec", "fade_out_sec", "source_in"):
    ok(need in AU, f"`{need}` объявлен")
# Движение элемента и кривая громкости — одна форма «точки во времени»; разошлись бы при второй.
ok("x" not in AU and "z_index" not in AU, "у звука нет геометрии — он не слой")

print("\n== обложки: CTR конструируется периодами ==")
TH = {c["name"]: c for c in VD["THUMBNAILS"]["columns"]}
# YouTube считает CTR на ВИДЕО: «исторический CTR обложки» взять неоткуда, его дают периоды.
for need in ("applied_from", "applied_to", "impressions", "ctr_percent"):
    ok(need in TH, f"`{need}` объявлен — без периода подмену обложки не измерить")
ok(TH["design_id"]["flag"] == "fk", "обложка ссылается на дизайн, а не описывает его заново")
DS = {c["name"]: c for c in CH["THUMBNAIL_DESIGNS"]["columns"]}
ok("videos_since_last_use" in DS, "«пора менять» считается на уровне канала")
# Веса `overall_uniqueness` уже дают 1.00 — слагаемое сдвинуло бы числа во всех каналах.
_uniq = next(c for c in VD["UNIQUENESS"]["columns"] if c["name"] == "overall_uniqueness")
ok("thumbnail" not in (_uniq.get("formula") or ""),
   f"обложка НЕ вошла в overall_uniqueness: {_uniq.get('formula')}")
ok(TH["thumbnail_uniqueness"]["flag"] == "F", "балл обложки вычисляемый, а не записываемый")

print("\n== регистр enum внутри подсистемы монтажа ==")
# Общепроектной конвенции НЕТ: в одном листе `AUTOMATION_RULES` соседствуют строчный `action` и
# заглавный `severity`. Но enum из спеки конвертер умеет отдавать ТОЛЬКО заглавными (он извлекает
# заглавные токены), поэтому словарь монтажа выровнен по ним — включая написанный руками.
_montage = [("channel_data", "RENDER_CONFIG", "subtitles_mode"),
            ("video_data", "SCENE_ELEMENTS", "alpha_mode"),
            ("video_data", "SCENE_AUDIO", "audio_role")]
for bname, sname, cname in _montage:
    col = next(c for c in sheets(book(bname))[sname]["columns"] if c["name"] == cname)
    vals = col.get("enum") or []
    ok(vals and all(str(v) == str(v).upper() for v in vals),
       f"{sname}.{cname} — словарь заглавными: {vals}")

print("\n== книги материализуются ==")
from core.engine.table_materializer import TableMaterializer  # noqa: E402
from core.excel import ExcelEngine  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    made = []
    for bname in ("channel_data", "video_data"):
        TableMaterializer(ExcelEngine(root), TABLES).materialize(bname, f"{bname}.xlsx")
        made.append((bname, root / f"{bname}.xlsx"))
    for bname, dst in made:
        wb = openpyxl.load_workbook(dst)
        ok(dst.exists() and dst.stat().st_size > 0, f"{bname}.xlsx создан")
        if bname == "video_data":
            ok({"SCENE_ELEMENTS", "SCENE_AUDIO"} <= set(wb.sheetnames),
               f"новые листы попали в книгу: {[n for n in wb.sheetnames if n.startswith('SCENE')]}")
            hdr = [c.value for c in wb["SCENE_ELEMENTS"][1]]
            ok("motion_path" in hdr, f"шапка элемента несёт `motion_path`: {hdr[-4:]}")
        else:
            ws = wb["RENDER_CONFIG"]
            ok([c.value for c in ws[1]][0] == "profile_id", "шапка RENDER_CONFIG = профили")
            ids = {ws.cell(r, 1).value for r in range(2, ws.max_row + 1)}
            ok({"long_16x9", "short_9x16"} <= ids, f"профили-дефолты материализовались: {sorted(x for x in ids if x)}")

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
