"""
tests/quick/test_ffmpeg_engine.py — движок монтажа: сборка команды, запуск, приёмка результата.

Standalone-прогон:  python tests/quick/test_ffmpeg_engine.py
Проверяет: замысел сцены собирается в один фильтрограф, значение из книги не становится командой,
отказ ffmpeg приходит кодом реестра, а собранный файл считается готовым только после ffprobe.
Без бинаря структурная часть отрабатывает, прогонные проверки пропускаются с причиной.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.providers.ffmpeg import (  # noqa: E402
    AudioTrack, FfmpegDictionary, FfmpegEngine, FfmpegError, FilterUse, Layer, RenderProfile,
    SceneSpec, accept,
)
from core.providers.ffmpeg.command import escape_path, ramp, tempo_steps  # noqa: E402
from core.providers.ffmpeg.dictionary import guard  # noqa: E402
from core.providers.ffmpeg.spec import points  # noqa: E402

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
    """Отказ пришёл КОДОМ реестра, а не текстом и не исключением питона."""
    try:
        fn()
    except FfmpegError as e:
        ok(e.code == code, f"{msg} (получено {e.code})")
        return
    except Exception as e:                                  # noqa: BLE001
        ok(False, f"{msg} — вместо кода реестра {type(e).__name__}: {e}")
        return
    ok(False, f"{msg} — отказа не было вовсе")


DICT = FfmpegDictionary()
PROFILE = RenderProfile(profile_id="test_16x9", resolution="640x360", fps=25, crf=30)

print("== словарь закрыт ==")
refuses(lambda: DICT.filter_expr("wipe_from_the_left"), "FILTER_UNKNOWN",
        "незнакомый фильтр в фильтрограф не уходит")
refuses(lambda: DICT.transition("телепортация"), "FILTER_UNKNOWN",
        "незнакомый переход не уходит")
refuses(lambda: DICT.filter_expr("blur", {"sigma": 10 ** 6}), "VALIDATION_ERROR",
        "параметр вне объявленной схемы не уходит")
refuses(lambda: DICT.filter_expr("blur", {"радиус": 3}), "VALIDATION_ERROR",
        "параметр не из схемы не уходит (словарь параметров закрыт)")
refuses(lambda: DICT.filter_expr("chromakey", {"color": "0x00FF00,drawtext=text=x"}), "VALIDATION_ERROR",
        "значение с разделителем фильтрографа отбивается")
ok(DICT.filter_expr("chromakey") == "chromakey=color=0x00FF00:similarity=0.1:blend=0",
   "дефолты схемы подставляются в готовый кусок графа")
refuses(lambda: guard("fade,drawtext=text=x", "тест"), "VALIDATION_ERROR",
        "разделитель фильтрографа не проходит сторож значения даже мимо схемы")

print("\n== значение из книги остаётся значением ==")
ok(points("0:20,50;1:100,50", 2, "тест") == [(0.0, 20.0, 50.0), (1.0, 100.0, 50.0)],
   "точки движения разбираются числами")
refuses(lambda: points("0:x,y", 2, "движении"), "VALIDATION_ERROR",
        "текст вместо координат отбивается разбором точек")
_expr = ramp([(0.0, 20.0, 50.0), (1.0, 100.0, 50.0)], 1)
ok(re.fullmatch(r"[0-9if(lt,t*+\-.)/]+", _expr.replace(" ", "")),
   "выражение движения состоит только из чисел и операций")
ok(escape_path(Path("/tmp/a:b'c.srt")) == "/tmp/a\\:b\\'c.srt",
   "разделители внутри пути субтитров экранируются, а не остаются разделителями")
ok(tempo_steps(4.0) == [2.0, 2.0] and tempo_steps(1.0) == [],
   "темп вне диапазона atempo набирается цепочкой, ровный темп не даёт шага")

print("\n== геометрия и профиль ==")
try:
    RenderProfile(profile_id="bad", resolution="1920*1080")
    ok(False, "разрешение не в форме ШИРИНАxВЫСОТА должно отбиваться")
except Exception:
    ok(True, "разрешение не в форме ШИРИНАxВЫСОТА отбивается на входе")
try:
    RenderProfile(profile_id="bad", codec="libx264 -f lavfi")
    ok(False, "кодек с пробелом и флагом должен отбиваться")
except Exception:
    ok(True, "кодек с посторонним флагом отбивается на входе")

print("\n== коды отказов объявлены в реестре ==")
_declared = set(yaml.safe_load((ROOT / "config" / "server_reactions.yaml").read_text(encoding="utf-8")))
_used = set()
for _src in (ROOT / "core" / "providers" / "ffmpeg").glob("*.py"):
    _used |= set(re.findall(r'FfmpegError\(\s*"([A-Z_]+)"', _src.read_text(encoding="utf-8")))
ok(_used and not (_used - _declared),
   f"каждый код движка объявлен в server_reactions.yaml (нет: {sorted(_used - _declared) or '—'})")

print("\n── прогон на установленном ffmpeg ──")
FFMPEG = shutil.which("ffmpeg")
if not FFMPEG:
    # Пропуск с ПРИЧИНОЙ: рендер живёт на машине владельца, а не на раннере CI.
    print("  ⤼ ffmpeg не найден в PATH — прогонные проверки пропущены (не отказ)")
else:
    _tmp = Path(tempfile.mkdtemp(prefix="vpm-montage-"))
    BG, OBJ, SND = _tmp / "bg.png", _tmp / "obj.png", _tmp / "voice.wav"
    for _args in (
        ["-f", "lavfi", "-i", "color=c=blue:s=320x240:d=1", "-frames:v", "1", str(BG)],
        ["-f", "lavfi", "-i", "color=c=0x00FF00:s=100x100:d=1", "-frames:v", "1", str(OBJ)],
        ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(SND)],
    ):
        subprocess.run([FFMPEG, "-hide_banner", "-v", "error", *_args, "-y"], check=True)

    ENGINE = FfmpegEngine(DICT)

    def scene(name, **kw):
        return SceneSpec(scene_id=name, profile=PROFILE, output=_tmp / f"{name}.mp4", **kw)

    _two = scene("two_layers", duration_sec=3.0, layers=[
        Layer(element_id="bg", asset_path=BG, z_index=0, fit_canvas=True),
        Layer(element_id="obj", asset_path=OBJ, z_index=1, alpha_mode="CHROMAKEY",
              width=120, height=120, time_start=0.5, time_end=2.5,
              motion_path="0.5:20,50; 2.5:160,50",
              filters=[FilterUse(name="blur", params={"sigma": 2})]),
    ], audio=[AudioTrack(audio_id="voice", asset_path=SND, volume=0.8, fade_in_sec=0.3,
                         fade_out_sec=0.3, time_start=0.5)])

    _argv, _span = ENGINE.build(_two)
    _graph = _argv[_argv.index("-filter_complex") + 1]
    ok("shell" not in " ".join(_argv) and all(isinstance(a, str) for a in _argv),
       "команда собирается списком аргументов, оболочка не участвует")
    ok(_graph.count("overlay=") == 2 and "enable='between(t,0.5,2.5)'" in _graph,
       "оба слоя накладываются, окно присутствия задано временем элемента")
    ok("chromakey=color=0x00FF00" in _graph, "непрозрачность фона снимается объявленным фильтром")
    ok("gblur=sigma=2" in _graph and _graph.index("chromakey") < _graph.index("gblur"),
       "объявленный фильтр слоя встаёт в цепочку после снятия фона")
    ok("amix=inputs=1" in _graph and "adelay=500:all=1" in _graph,
       "дорожка звука уходит в микс со своим смещением")

    _out = ENGINE.render_scene(_two)
    ok(_out.path.exists(), "файл сцены из двух слоёв собран")
    ok(abs(_out.duration_sec - 3.0) < 0.1 and (_out.width, _out.height) == (640, 360),
       f"приёмка подтверждает длительность и кадр профиля ({_out.duration_sec:.2f} с, "
       f"{_out.width}x{_out.height})")

    _silent = scene("silent", duration_sec=1.0,
                    layers=[Layer(element_id="bg", asset_path=BG, fit_canvas=True)])
    _sout = ENGINE.render_scene(_silent)
    ok(_sout.duration_sec > 0, "сцена без звука рендерится: пустое аудио — не отказ")

    print("\n  ── отказы ──")
    _missing = scene("missing", duration_sec=1.0,
                     layers=[Layer(element_id="ghost", asset_path=_tmp / "нет.png")])
    refuses(lambda: ENGINE.render_scene(_missing), "FILE_NOT_FOUND",
            "пропавший ассет отбивается до запуска бинаря")

    _neural = scene("neural", duration_sec=1.0, layers=[
        Layer(element_id="obj", asset_path=OBJ, alpha_mode="REMOVE_BG")])
    refuses(lambda: ENGINE.render_scene(_neural), "RENDER_INPUT_UNPREPARED",
            "снятие фона нейросетью — работа провайдера, а не ffmpeg")

    _unknown = SceneSpec(scene_id="unknowncodec", output=_tmp / "unknown.mp4", duration_sec=1.0,
                         profile=RenderProfile(profile_id="broken", resolution="640x360",
                                               fps=25, codec="h266"),
                         layers=[Layer(element_id="bg", asset_path=BG, fit_canvas=True)])
    refuses(lambda: ENGINE.render_scene(_unknown), "RENDER_PROFILE_INVALID",
            "кодек, которого нет в словаре, отбивается до запуска бинаря")

    # Тот же код и с другой стороны: имя объявлено, а энкодера в этой сборке ffmpeg нет —
    # ловится уже разбором stderr. Проверяем на своём словаре, боевой для этого не ломаем.
    _fake_dict = _tmp / "broken_filters.yaml"
    _fake_dict.write_text((ROOT / "config" / "ffmpeg_filters.yaml").read_text(encoding="utf-8")
                          .replace("h264: libx264", "h264: nosuchencoder"), encoding="utf-8")
    _fake_engine = FfmpegEngine(FfmpegDictionary(_fake_dict))
    _badcodec = SceneSpec(scene_id="badcodec", output=_tmp / "badcodec.mp4", duration_sec=1.0,
                          profile=RenderProfile(profile_id="broken", resolution="640x360", fps=25),
                          layers=[Layer(element_id="bg", asset_path=BG, fit_canvas=True)])
    refuses(lambda: _fake_engine.render_scene(_badcodec), "RENDER_PROFILE_INVALID",
            "энкодер, которого нет в бинаре, приходит кодом профиля, а не сырым stderr")

    _noduration = scene("noduration", layers=[Layer(element_id="bg", asset_path=BG, fit_canvas=True)])
    refuses(lambda: ENGINE.render_scene(_noduration), "VALIDATION_ERROR",
            "сцена из одних картинок без времени отбивается: длительность неоткуда взять")

    refuses(lambda: accept(_out.path, 10.0, (640, 360)), "RENDER_UNVERIFIED",
            "длительность, разошедшаяся с замыслом, приёмку не проходит")
    refuses(lambda: accept(_out.path, 3.0, (1920, 1080)), "RENDER_UNVERIFIED",
            "кадр не той геометрии, что обещал профиль, приёмку не проходит")

    (_tmp / "notmedia.mp4").write_text("это не видео", encoding="utf-8")
    try:
        accept(_tmp / "notmedia.mp4", 1.0, (640, 360))
        ok(False, "не-видео не должно проходить приёмку")
    except FfmpegError as e:
        ok(e.code in ("RENDER_UNVERIFIED", "RENDER_INPUT_UNPREPARED"),
           f"файл, который не является видео, приёмку не проходит ({e.code})")

    _short = ENGINE.build(_two)[0]
    refuses(lambda: FfmpegEngine(DICT, timeout_sec=0).run(_short), "RENDER_TIMEOUT",
            "рендер за пределом времени снимается кодом, а не висит")

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
