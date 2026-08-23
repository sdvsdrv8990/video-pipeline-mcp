"""
tests/quick/test_montage_tools.py — сцена из книги становится проверенным файлом.

Standalone-прогон:  python tests/quick/test_montage_tools.py
Проверяет: строки листов сцены собираются в замысел и рендерятся, строку результата заполняет
приёмка (`file_verified`, `duration_sec`), сцена без звука проходит, отказы приходят кодами
реестра, а имена листов, столбцов и ролей живут в декларации, а не в коде.
"""
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.ids import IDGenerator  # noqa: E402
from core.montage import MontageError, RenderLedger, SceneBook  # noqa: E402
from core.providers.ffmpeg import AudioTrack, FfmpegEngine, Layer  # noqa: E402
from core.state import StateManager  # noqa: E402

CONFIG = ROOT / "config"
MONTAGE = yaml.safe_load((CONFIG / "montage.yaml").read_text(encoding="utf-8"))

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


def refuses(fn, code, msg):
    """Отказ пришёл КОДОМ реестра, а не текстом."""
    try:
        result = fn()
    except MontageError as e:
        ok(e.code == code, f"{msg} (получено {e.code})")
        return
    except Exception as e:                                  # noqa: BLE001
        ok(False, f"{msg} — вместо кода реестра {type(e).__name__}: {e}")
        return
    got = getattr(getattr(result, "error", None), "code", None)
    ok(got == code, f"{msg} (получено {got or 'успех'})")


print("== декларация не разошлась с книгами и с движком ==")
_video = yaml.safe_load((CONFIG / "templates/tables/video_data.schema.yaml").read_text(encoding="utf-8"))
_channel = yaml.safe_load((CONFIG / "templates/tables/channel_data.schema.yaml").read_text(encoding="utf-8"))


def _columns(book, sheet):
    for s in book["sheets"]:
        if s["name"] == sheet:
            return {c["name"] for c in s["columns"]}
    return set()


for _part, _model in (("elements", Layer), ("audio", AudioTrack)):
    _cfg = MONTAGE["scene"][_part]
    _cols = _columns(_video, _cfg["sheet"])
    _missing = [f for f in _cfg["fields"] + [_cfg["scene_column"], _cfg["id_column"]] if f not in _cols]
    ok(not _missing, f"{_cfg['sheet']}: каждое объявленное поле есть столбцом книги (нет: {_missing or '—'})")
    _unknown = [f for f in _cfg["fields"] if f not in set(_model.model_fields)]
    ok(not _unknown, f"{_cfg['sheet']}: каждое поле принимает движок (не принимает: {_unknown or '—'})")

_roles = MONTAGE["scene"]["roles"]
ok(_roles["fills_frame_column"] in _columns(_channel, _roles["sheet"]),
   "столбец «слой на весь кадр» объявлен в листе профиля сцен канала")
_prof = MONTAGE["profile"]
ok(_prof["selector_column"] in _columns(_video, _prof["selector_sheet"]),
   "видео есть чем выбрать профиль рендера")
_target_cols = _columns(_video, MONTAGE["target"]["sheet"])
_lost = [c for c in MONTAGE["target"]["columns"].values() if c not in _target_cols]
ok(not _lost, f"каждый столбец результата объявлен в книге видео (нет: {_lost or '—'})")

_rig = MONTAGE["rig"]
for _part in ("slots", "variants"):
    _cfg = _rig[_part]
    _cols = _columns(_channel, _cfg["sheet"])
    _lost = [f for f in _cfg["fields"] + [_cfg["id_column"]] if f not in _cols]
    ok(not _lost, f"{_cfg['sheet']}: каждое объявленное поле есть столбцом книги канала (нет: {_lost or '—'})")
ok(_rig["slots"]["parent_column"] in _columns(_channel, _rig["slots"]["sheet"]),
   "у слота есть чем назвать родителя — от него и порядок наложения, и совместимость")
ok(_rig["variants"]["key_column"] in _columns(_channel, _rig["variants"]["sheet"])
   and _rig["slots"]["key_column"] in _columns(_channel, _rig["slots"]["sheet"]),
   "ось совместимости объявлена с обеих сторон: у слота — что сравнивать, у варианта — чем")
_el_cols = _columns(_video, MONTAGE["scene"]["elements"]["sheet"])
ok({"slot_id", "variant_id", "fade_sec"} <= _el_cols,
   "строка элемента умеет быть кадром дорожки слота, а не только одиночным слоем")

_toggle = _rig["toggle"]
_profile_rows = [s for s in _channel["sheets"] if s["name"] == _toggle["sheet"]][0]["rows"]
_flag = next((r for r in _profile_rows if r[_toggle["type_column"]] == _toggle["fragment_type"]), None)
ok(_flag is not None, f"тумблер вариативности объявлен строкой листа {_toggle['sheet']}")
ok(_flag and _flag[_toggle["enabled_column"]] is False,
   "и приезжает в канал ВЫКЛЮЧЕННЫМ: включение сдвигает числа уникальности, это решение человека")
_uniq_cols = [c for s in _video["sheets"] if s["name"] == "UNIQUENESS" for c in s["columns"]]
ok(not any(c.get("formula") for c in _uniq_cols),
   "в книге не осталось замороженной формулы уникальности — иначе включённый тумблер делает её ложной")

_tpls = MONTAGE["templates"]
for _part in ("elements", "audio"):
    _cols = _columns(_channel, _tpls[_part]["sheet"])
    _target = set(MONTAGE["scene"][_part]["fields"])
    _lost = sorted(_target - _cols)
    ok(not _lost, f"{_tpls[_part]['sheet']}: заготовка несёт те же поля, что и лист сцены (нет: {_lost or '—'})")
    ok(_tpls[_part]["template_column"] in _cols,
       f"{_tpls[_part]['sheet']}: есть чем сгруппировать строки в один шаблон")

_tpl = yaml.safe_load((CONFIG / "templates/workspace/video.tpl.yaml").read_text(encoding="utf-8"))
_folders = {f["name"] for f in _tpl["video"]["folders"]}
ok(MONTAGE["output"]["dir"] in _folders,
   f"каталог результата объявлен и в шаблоне видео ({MONTAGE['output']['dir']})")

_src = (ROOT / "core/montage/book.py").read_text(encoding="utf-8")
ok(not any(name in _src for name in ("SCENE_ELEMENTS", "SCENE_AUDIO", "RENDER_CONFIG", "layer_bg")),
   "в коде перевода нет ни одного имени листа или роли — они приходят декларацией")

print("\n── прогон на установленном ffmpeg ──")
if not shutil.which("ffmpeg"):
    # Пропуск с ПРИЧИНОЙ: рендер живёт на машине владельца, а не на раннере CI.
    print("  ⤼ ffmpeg не найден в PATH — прогонные проверки пропущены (не отказ)")
else:
    WS = Path(tempfile.mkdtemp(prefix="vpm-montage-tool-")) / "workspace"
    (WS / "ch1/v1/assets").mkdir(parents=True)
    (WS / "ch1/v1/renders").mkdir()
    for _args, _dst in (
        (["-f", "lavfi", "-i", "color=c=navy:s=320x240:d=1", "-frames:v", "1"], "bg.png"),
        (["-f", "lavfi", "-i", "color=c=0x00FF00:s=80x80:d=1", "-frames:v", "1"], "hero.png"),
        (["-f", "lavfi", "-i", "sine=frequency=330:duration=2"], "voice.wav"),
        (["-f", "lavfi", "-i", "color=c=red:s=60x60:d=1", "-frames:v", "1"], "pose_a.png"),
        (["-f", "lavfi", "-i", "color=c=yellow:s=60x60:d=1", "-frames:v", "1"], "pose_b.png"),
        (["-f", "lavfi", "-i", "color=c=white:s=20x20:d=1", "-frames:v", "1"], "face.png"),
    ):
        subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", *_args,
                        str(WS / "ch1/v1/assets" / _dst), "-y"], check=True)

    (WS / "ch1" / "read.json").write_text(json.dumps({
        "RENDER_CONFIG": {"schema": {}, "rows": {
            "P1": {"profile_id": "test_16x9", "codec": "h264", "resolution": "640x360",
                   "aspect_ratio": "16:9", "fps": 25, "crf": 30, "container": "mp4",
                   "subtitles_mode": "TRACK"}}},
        "SCENE_PROFILE": {"schema": {}, "rows": {
            "R1": {"fragment_type": "layer_bg", "enabled": True, "fills_frame": True},
            "R2": {"fragment_type": "layer_character", "enabled": True, "fills_frame": False}}},
        "CHARACTER_SLOTS": {"schema": {}, "rows": {
            "S1": {"slot_id": "body", "character_id": "hero", "slot_role": "BODY",
                   "parent_slot": "", "compat_axis": "view", "anchor_x": 40, "anchor_y": 60,
                   "bounds_w": 60, "bounds_h": 60, "z_offset": 0, "default_fade_sec": 0.3},
            "S2": {"slot_id": "face", "character_id": "hero", "slot_role": "FACE",
                   "parent_slot": "body", "compat_axis": "view", "anchor_x": 20, "anchor_y": 10,
                   "bounds_w": 0, "bounds_h": 0, "z_offset": 2, "default_fade_sec": 0.12}}},
        "ASSET_VARIANTS": {"schema": {}, "rows": {
            "V1": {"variant_id": "pose_idle", "slot_id": "body", "label": "спокойно",
                   "compat_key": "front", "asset_path": "ch1/v1/assets/pose_a.png",
                   "width": 60, "height": 60, "status": "ACTIVE"},
            "V2": {"variant_id": "pose_point", "slot_id": "body", "label": "указывает",
                   "compat_key": "front", "asset_path": "ch1/v1/assets/pose_b.png",
                   "width": 60, "height": 60, "status": "ACTIVE"},
            "V3": {"variant_id": "emo_smile", "slot_id": "face", "label": "улыбка",
                   "compat_key": "front", "asset_path": "ch1/v1/assets/face.png",
                   "width": 20, "height": 20, "status": "ACTIVE"},
            "V4": {"variant_id": "emo_side", "slot_id": "face", "label": "профиль",
                   "compat_key": "side", "asset_path": "ch1/v1/assets/face.png",
                   "width": 20, "height": 20, "status": "ACTIVE"},
            "V5": {"variant_id": "emo_huge", "slot_id": "face", "label": "великанская",
                   "compat_key": "front", "asset_path": "ch1/v1/assets/pose_a.png",
                   "width": 200, "height": 200, "status": "ACTIVE"}}},
    }, ensure_ascii=False), encoding="utf-8")

    def book(scene_rows, audio_rows, meta=None):
        (WS / "ch1/v1" / "read.json").write_text(json.dumps({
            "META": {"schema": {}, "rows": {"M1": meta if meta is not None
                                            else {"video_id": "VID1", "render_profile": "test_16x9"}}},
            "SCENE_ELEMENTS": {"schema": {}, "rows": scene_rows},
            "SCENE_AUDIO": {"schema": {}, "rows": audio_rows},
        }, ensure_ascii=False), encoding="utf-8")

    ELEMENTS = {
        "E1": {"element_id": "E1", "scene_id": "S01", "element_role": "layer_bg",
               "asset_path": "ch1/v1/assets/bg.png", "z_index": 0, "time_end": 3},
        "E2": {"element_id": "E2", "scene_id": "S01", "element_role": "layer_character",
               "asset_path": "ch1/v1/assets/hero.png", "alpha_mode": "CHROMAKEY",
               "x": 40, "y": 60, "width": 100, "height": 100, "z_index": 1,
               "time_start": 0.5, "time_end": 2.5, "motion_path": "0.5:40,60; 2.5:200,60"},
        "E9": {"element_id": "E9", "scene_id": "S02", "element_role": "layer_bg",
               "asset_path": "ch1/v1/assets/bg.png", "z_index": 0, "time_end": 1},
    }
    AUDIO = {"A1": {"audio_id": "A1", "scene_id": "S01", "audio_role": "VOICE",
                    "asset_path": "ch1/v1/assets/voice.wav", "volume": 0.7,
                    "fade_in_sec": 0.2, "time_start": 0.3}}
    book(ELEMENTS, AUDIO)

    SM = StateManager(WS)
    ENG = Engine(state_manager=SM)
    server.register_basic_tools(ENG, IDGenerator(), SM)

    def call(tool, **params):
        return asyncio.run(ENG.call(tool, params))

    _r = call("montage_render_scene", table="ch1/v1", scene_id="S01")
    ok(_r.status == "success", f"сцена из книги собралась ({_r.error.message if _r.error else ''})")
    if _r.status == "success":
        ok((WS / _r.data["file_path"]).exists(), f"файл лёг в объявленное место ({_r.data['file_path']})")
        ok(abs(_r.data["duration_sec"] - 3.0) < 0.15 and _r.data["frame"] == "640x360",
           f"длительность и кадр подтверждены приёмкой ({_r.data['duration_sec']:.2f} с, {_r.data['frame']})")
        ok(_r.data["profile_source"] == "project" and _r.data["profile_id"] == "test_16x9",
           "профиль взят из книги канала — уровнем выше видео, и это названо")
        ok(_r.data["elements"] == 2 and _r.data["audio"] == 1,
           "в сцену попали только её строки, чужая сцена не приехала")
        ok(any(f.type == "RenderCompleted" for f in _r.facts),
           "рендер приходит фактом контракта, а не только текстом")

        _rows = (SM.read_snapshot("ch1/v1") or {})[MONTAGE["target"]["sheet"]]["rows"]
        _row = _rows[_r.data["render_row_id"]]
        _cols = MONTAGE["target"]["columns"]
        ok(_row[_cols["verified"]] is True and _row[_cols["duration"]] == 3,
           f"строку результата заполнила приёмка, а не рука ({_row[_cols['duration']]} с, verified)")
        ok(_row[_cols["profile"]] == "test_16x9" and _row[_cols["status"]] == MONTAGE["target"]["status_ready"],
           "в строке результата стоит профиль и статус готовности")
        ok(_row[_cols["file_path"]].startswith("ch1/v1/"),
           "путь в книге относительный — рабочая область переносима")

    _silent = call("montage_render_scene", table="ch1/v1", scene_id="S02")
    ok(_silent.status == "success", "сцена без единой дорожки звука собирается: пустой звук не отказ")

    print("\n  ── тумблер вариативности и советы ──")
    _plain = call("montage_render_scene", table="ch1/v1", scene_id="S02")
    ok(not _plain.data.get("recommendations"),
       "тумблер выключен, слотов в сцене нет — сервер молчит: согласованный случай не советует")

    book({**ELEMENTS, "E2": {**ELEMENTS["E2"], "slot_id": "body"}}, AUDIO)
    _mismatch = call("montage_render_scene", table="ch1/v1", scene_id="S01")
    _rec = (_mismatch.data.get("recommendations") or [{}])[0]
    ok(_rec.get("id") == "variants_disabled_but_used",
       f"сцена пользуется слотами при выключенном тумблере → совет ({_rec.get('id') or 'нет совета'})")
    ok(_rec.get("tool") == "table_set" and _rec.get("params", {}).get("sheet") == "SCENE_PROFILE",
       "совет исполним: назван инструмент и лист, а не проза")

    _ch = json.loads((WS / "ch1" / "read.json").read_text(encoding="utf-8"))
    _ch["SCENE_PROFILE"]["rows"]["R9"] = {"fragment_type": MONTAGE["rig"]["toggle"]["fragment_type"],
                                          "enabled": True, "niche_weight": 0.25}
    (WS / "ch1" / "read.json").write_text(json.dumps(_ch, ensure_ascii=False), encoding="utf-8")
    _on_used = call("montage_render_scene", table="ch1/v1", scene_id="S01")
    ok(not _on_used.data.get("recommendations") and _on_used.data["variants_enabled"] is True,
       "тумблер включён и сцена по слотам — снова молчание, и состояние тумблера видно клиенту")
    _on_unused = call("montage_render_scene", table="ch1/v1", scene_id="S02")
    ok((_on_unused.data.get("recommendations") or [{}])[0].get("id") == "variants_enabled_but_unused",
       "включено, а сцена без слотов → второй совет: пробел готовности вместо числа")
    _ch["SCENE_PROFILE"]["rows"].pop("R9", None)          # канал возвращается к выключенному тумблеру
    (WS / "ch1" / "read.json").write_text(json.dumps(_ch, ensure_ascii=False), encoding="utf-8")
    book(ELEMENTS, AUDIO)

    print("\n  ── раскладка по заготовке ──")
    _gen = call("montage_generate_scene", table="ch1/v1", scene_id="S07", template_id="talking_head")
    ok(_gen.status == "success" and _gen.data["elements"] == 2 and _gen.data["audio"] == 1,
       f"заготовка разложилась строками ({_gen.data.get('elements')} слоёв, {_gen.data.get('audio')} звук)")
    ok(_gen.data["templates_available"] == ["talking_head"],
       f"какие заготовки есть — приходит выборкой, а не догадкой ({_gen.data['templates_available']})")
    _snap = SM.read_snapshot("ch1/v1") or {}
    _made = {rid: row for rid, row in _snap["SCENE_ELEMENTS"]["rows"].items()
             if row.get("scene_id") == "S07"}
    ok(len(_made) == 2 and all(r.get("template_row_id") is None for r in _made.values()),
       "ID строк выдал сервер: идентификатор заготовки в сцену не переехал")
    ok(all(set(r) <= set(MONTAGE["scene"]["elements"]["fields"]) | {"scene_id", "slot_id",
                                                                    "variant_id", "fade_sec",
                                                                    "element_role"}
           for r in _made.values()),
       "в сцену попали только объявленные поля — заготовка не приносит чужих столбцов")

    _gen2 = call("montage_generate_scene", table="ch1/v1", scene_id="S07",
                 template_id="talking_head", time_offset=4.0, z_offset=10)
    _snap2 = SM.read_snapshot("ch1/v1") or {}
    _all7 = [row for row in _snap2["SCENE_ELEMENTS"]["rows"].values() if row.get("scene_id") == "S07"]
    ok(len(_all7) == 4, f"второй шаблон ДОПИСАЛСЯ, а не заменил первый ({len(_all7)} строк)")
    ok(sorted({r.get("time_start") for r in _all7}) == [0.0, 4.0]
       and sorted({r.get("z_index") for r in _all7}) == [0, 1, 10, 11],
       "смещение по времени и порядку слоёв применилось — так шаблоны и собираются в одну сцену")

    refuses(lambda: call("montage_generate_scene", table="ch1/v1", scene_id="S08",
                         template_id="нет_такой"),
            "TEMPLATE_NOT_FOUND", "незнакомая заготовка отбивается и называет доступные")
    refuses(lambda: call("montage_render_scene", table="ch1/v1", scene_id="S07"),
            "SCENE_EMPTY", "разложенная заготовка без ассетов не рендерится молча: сказано, чего нет")

    print("\n  ── чистый скрипт сцены по требованию ──")
    _scr = call("montage_scene_script", table="ch1/v1", scene_id="S01")
    ok(_scr.status == "success", f"скелет выдан ({_scr.error.message if _scr.error else ''})")
    _src = (WS / _scr.data["file_path"]).read_text(encoding="utf-8")
    ok(_scr.data["file_path"].endswith(".py") and _scr.data["executed"] is False,
       f"файл лёг в объявленное место и НЕ исполнялся ({_scr.data['file_path']})")
    _ns: dict = {}
    exec(compile(_src, "scene_template", "exec"), _ns)          # noqa: S102 — свой же скелет
    ok(_ns["RESOLUTION"] == "640x360" and _ns["FPS"] == 25 and _ns["CODEC"] == "h264",
       f"требования канала подтянуты в скрипт ({_ns['RESOLUTION']}, {_ns['FPS']}, {_ns['CODEC']})")
    ok(_ns["ELEMENTS"] == [] and _ns["AUDIO"] == [],
       "наполнения нет: ни ассетов, ни таймингов, ни звука")
    ok(_ns["scene"]()["scene_id"] == "S01" and _ns["PROFILE_ID"] == "test_16x9",
       "скрипт исполним и знает, чья он сцена и каким профилем собирается")
    ok("{{" not in _src, "в файле не осталось незакрытых мест шаблона")

    _off_src = _src
    _ch2 = json.loads((WS / "ch1" / "read.json").read_text(encoding="utf-8"))
    _ch2["SCENE_PROFILE"]["rows"]["R9"] = {"fragment_type": MONTAGE["rig"]["toggle"]["fragment_type"],
                                           "enabled": True, "niche_weight": 0.25}
    (WS / "ch1" / "read.json").write_text(json.dumps(_ch2, ensure_ascii=False), encoding="utf-8")
    _scr_on = call("montage_scene_script", table="ch1/v1", scene_id="S01")
    _on_src = (WS / _scr_on.data["file_path"]).read_text(encoding="utf-8")
    _ns_on: dict = {}
    exec(compile(_on_src, "scene_template", "exec"), _ns_on)    # noqa: S102
    ok(_ns_on["VARIANTS_ENABLED"] is True and _ns["VARIANTS_ENABLED"] is False,
       "флаг канала доезжает В СКРИПТ, а не остаётся в книге")
    ok(_ns_on["SLOTS"] and any(s.get("parent_slot") for s in _ns_on["SLOTS"]),
       f"при включённой вариативности скрипт знает слоты и их родителей ({len(_ns_on['SLOTS'])})")
    ok("slot_id" in _on_src and "slot_id" not in _off_src,
       "форма строки в примере МЕНЯЕТСЯ по флагу: выключено — про слоты ни слова")
    _ch2["SCENE_PROFILE"]["rows"].pop("R9")
    (WS / "ch1" / "read.json").write_text(json.dumps(_ch2, ensure_ascii=False), encoding="utf-8")

    # Значение из книги уезжает В ФАЙЛ: кавычка в ячейке не должна закрывать докстринг.
    _evil = call("montage_scene_script", table="ch1/v1",
                 scene_id='S01"""\nimport os\nos.system("touch /tmp/vpm_pwned")\n"""')
    _marker = Path("/tmp/vpm_pwned")
    _marker.unlink(missing_ok=True)
    if _evil.status == "success":
        _evil_src = (WS / _evil.data["file_path"]).read_text(encoding="utf-8")
        _ns_evil: dict = {}
        exec(compile(_evil_src, "evil", "exec"), _ns_evil)      # noqa: S102 — проверяем инертность
        ok(not _marker.exists(),
           "кавычки и перевод строки из ячейки НЕ становятся кодом: побочного действия не произошло")
        ok(_ns_evil["SCENE_ID"].startswith("S01") and "os.system" in _ns_evil["SCENE_ID"],
           "враждебный текст доехал ЛИТЕРАЛОМ — он данные, а не инструкции")
        ok("/" not in Path(_evil.data["file_path"]).name and
           Path(_evil.data["file_path"]).parent.name == MONTAGE["script"]["dir"],
           f"имя файла собрано из значения безопасно ({Path(_evil.data['file_path']).name})")
    else:
        ok(True, f"враждебное имя сцены отбито до записи ({_evil.error.code})")

    print("\n  ── отказы ──")
    refuses(lambda: call("montage_render_scene", table="ch1/v1", scene_id="S99"),
            "SCENE_EMPTY", "сцены без элементов не рендерятся, а называются")
    refuses(lambda: call("montage_render_scene", table="ch1/v1", scene_id="S01", profile_id="нет_такого"),
            "RENDER_PROFILE_INVALID", "профиль вне листа отбивается до запуска бинаря")

    book({**ELEMENTS, "E3": {**ELEMENTS["E1"], "element_id": "E3",
                             "asset_path": "../../../etc/passwd"}}, AUDIO)
    refuses(lambda: call("montage_render_scene", table="ch1/v1", scene_id="S01"),
            "PATH_ESCAPE", "путь ассета из книги проходит containment рабочей области")

    book({**ELEMENTS, "E4": {**ELEMENTS["E1"], "element_id": "E4", "speed": "быстро"}}, AUDIO)
    refuses(lambda: call("montage_render_scene", table="ch1/v1", scene_id="S01"),
            "VALIDATION_ERROR", "нечисло в числовой ячейке отбивается с именем строки и листа")

    book(ELEMENTS, AUDIO, meta={"video_id": "VID1"})
    _no_choice = call("montage_render_scene", table="ch1/v1", scene_id="S01")
    ok(_no_choice.status == "success",
       "профиль в книге один — видео вправе его не называть")

    print("\n  ── дорожки слотов: варианты, рамка, совместимость ──")
    TRACK = {
        "T0": {"element_id": "T0", "scene_id": "S05", "element_role": "layer_bg",
               "asset_path": "ch1/v1/assets/bg.png", "z_index": 0, "time_end": 3.0},
        # дорожка тела: поза сменяется позой, файл берётся ИЗ КАТАЛОГА, а не из строки
        "T1": {"element_id": "T1", "scene_id": "S05", "element_role": "layer_character",
               "slot_id": "body", "variant_id": "pose_idle", "z_index": 1,
               "time_start": 0.0, "time_end": 1.5},
        "T2": {"element_id": "T2", "scene_id": "S05", "element_role": "layer_character",
               "slot_id": "body", "variant_id": "pose_point", "z_index": 1,
               "time_start": 1.5, "time_end": 3.0},
        # лицо крепится к телу: координаты складываются, свои x/y не трогаем
        "T3": {"element_id": "T3", "scene_id": "S05", "element_role": "layer_component",
               "slot_id": "face", "variant_id": "emo_smile", "z_index": 2, "time_end": 3.0},
    }
    book({**ELEMENTS, **TRACK}, AUDIO)
    _book = SceneBook(SM, CONFIG, lambda p: (WS / p).resolve())
    _profile, _report = _book.profile("ch1/v1")
    _spec5, _ = _book.scene("ch1/v1", "S05", WS / "t.mp4", _profile)
    _body = [el for el in _spec5.layers if el.slot == "body"]
    _face = next(el for el in _spec5.layers if el.slot == "face")
    ok(all(str(el.asset_path).endswith(".png") for el in _body),
       "вариант разрешён В ФАЙЛ: путь пришёл из каталога, строка его не дублирует")
    ok({el.x for el in _body} == {40} and {el.y for el in _body} == {60},
       f"кадры дорожки стоят в точке крепления слота ({ {el.x for el in _body} })")
    ok(_face.parent_slot == "body" and (_face.x, _face.y) == (20, 10),
       f"лицо знает родителя и несёт СВОЁ смещение, а не абсолютную точку ({_face.x},{_face.y})")
    ok(_body[1].fade_sec == 0.3 and _face.fade_sec == 0.12,
       "перекрытие пришло из слота, когда строка его не назвала")

    _argv5, _ = FfmpegEngine().build(_spec5)
    _graph5 = _argv5[_argv5.index("-filter_complex") + 1]
    ok("fade=t=out:alpha=1" in _graph5 and "fade=t=in:alpha=1" in _graph5,
       "на стыке дорожки уходящий гаснет, а приходящий проявляется")
    ok("x='(40)+(20)'" in _graph5 and "y='(60)+(10)'" in _graph5,
       "координаты ребёнка СЛОЖЕНЫ с родительскими, а не заданы заново")

    _track_render = call("montage_render_scene", table="ch1/v1", scene_id="S05")
    ok(_track_render.status == "success" and _track_render.data["elements"] == 4,
       f"сцена с дорожками собирается целиком ({_track_render.data.get('elements')} слоёв)")

    book({**TRACK, "T3": {**TRACK["T3"], "variant_id": "emo_huge"}}, AUDIO)
    refuses(lambda: call("montage_render_scene", table="ch1/v1", scene_id="S05"),
            "VARIANT_INCOMPATIBLE", "вариант больше рамки родителя не примеряется")
    book({**TRACK, "T3": {**TRACK["T3"], "variant_id": "emo_side"}}, AUDIO)
    refuses(lambda: call("montage_render_scene", table="ch1/v1", scene_id="S05"),
            "VARIANT_INCOMPATIBLE", "ключ оси не совпал с родителем — сочетание отбито до рендера")
    book({**TRACK, "T3": {**TRACK["T3"], "variant_id": "нет_такого"}}, AUDIO)
    refuses(lambda: call("montage_render_scene", table="ch1/v1", scene_id="S05"),
            "RENDER_INPUT_UNPREPARED", "висячая ссылка на вариант каталога отбивается, а не игнорируется")
    book({**TRACK, "T2": {**TRACK["T2"], "x": 15}}, AUDIO)
    refuses(lambda: call("montage_render_scene", table="ch1/v1", scene_id="S05"),
            "VALIDATION_ERROR", "кадры дорожки в разных точках отбиваются: слот двигается целиком")
    book(ELEMENTS, AUDIO)

    print("\n  ── профиль и строка результата поштучно ──")
    ok(_profile.codec == "h264" and _report["profile_owner"] == "ch1",
       "профиль найден вверх по дереву у канала, а не у самого видео")
    _spec, _counts = _book.scene("ch1/v1", "S01", WS / "out.mp4", _profile)
    _bg = next(el for el in _spec.layers if el.element_id == "E1")
    _hero = next(el for el in _spec.layers if el.element_id == "E2")
    ok(_bg.fit_canvas and not _hero.fit_canvas,
       "растягивается на кадр тот слой, чью роль книга канала пометила — код ролей не знает")
    ok(_hero.motion and _hero.alpha_mode == "CHROMAKEY" and _hero.width == 100,
       "геометрия, движение и режим непрозрачности доехали из строки в замысел")

    _empty_ws = Path(tempfile.mkdtemp(prefix="vpm-montage-empty-"))
    (_empty_ws / "v0").mkdir()
    (_empty_ws / "v0" / "read.json").write_text("{}", encoding="utf-8")
    _, _decl_report = SceneBook(StateManager(_empty_ws), CONFIG,
                                lambda p: (_empty_ws / p).resolve()).profile("v0", "long_16x9")
    ok(_decl_report["profile_source"] == "declaration",
       "книги канала ещё нет → профиль берётся из декларации И НАЗЫВАЕТСЯ")

    _ledger = RenderLedger(SM, CONFIG, IDGenerator())
    refuses(lambda: _ledger.record("ch1/v1", {"stage": "неведомая"}),
            "ENUM_VIOLATION", "стадия вне объявленных значений в книгу не попадает")
    refuses(lambda: _ledger.record("ch1/v1", {"выдумка": 1}),
            "SCHEMA_INVALID", "необъявленный столбец результата в книгу не попадает")

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
