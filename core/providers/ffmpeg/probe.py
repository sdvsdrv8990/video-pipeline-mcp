"""core/providers/ffmpeg/probe.py — что на самом деле лежит в файле, по словам ffprobe.

Движку нужно про вход то, чего расширение не говорит: картинка это или видео, есть ли звук, сколько
длится. Тот же разбор принимает результат — собранный файл считается готовым только после
подтверждения, иначе вместо отказа была бы немая потеря.
"""

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.environment import child_spawn, child_text

from .errors import FfmpegError, FfmpegSaid

PROBE_TIMEOUT_SEC = 60


@dataclass(frozen=True)
class MediaFacts:
    """Факты о файле: чем он является для сборки команды и для приёмки."""

    path: Path
    kind: str                      # image | video | audio
    duration_sec: float | None
    width: int | None
    height: int | None
    has_video: bool
    has_audio: bool


def binary(name: str = "ffprobe") -> str:
    """Путь бинаря пакета ffmpeg в PATH; нет — FFMPEG_MISSING с его именем. Одно решение для ffmpeg и ffprobe."""
    found = shutil.which(name)
    if not found:
        raise FfmpegError("FFMPEG_MISSING", facts={"binary": name})
    return found


def own(path: Path) -> str:
    """Имя файла без места сервера — когда путь от корня вызывающий не дал (`name`)."""
    return path.name


def raw(path: Path, probe: str = "ffprobe", name: Callable[[Path], str] = own) -> dict:
    """Сырой разбор ffprobe. Файла нет или он не разбирается — отказ кодом реестра; файл в отказе назван `name`
    (путь от корня даёт вызывающий), слова ffprobe — причиной для журнала сервера."""
    named = name(path)
    if not path.exists():
        raise FfmpegError("FILE_NOT_FOUND", facts={"path": named})
    out = subprocess.run(
        [binary(probe), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, timeout=PROBE_TIMEOUT_SEC, **child_spawn("strict"))
    if out.returncode != 0:
        raise FfmpegError("RENDER_INPUT_UNPREPARED", facts={"path": named, "exit_code": out.returncode}) \
            from FfmpegSaid(child_text(out.stderr, "replace"))
    try:                                    # битые байты (теги файла не в utf-8) — громкий отказ, а не подмена знаком
        return json.loads(child_text(out.stdout, "strict") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FfmpegError("RENDER_INPUT_UNPREPARED", facts={"path": named}) from exc


def facts(path: Path, probe: str = "ffprobe", name: Callable[[Path], str] = own) -> MediaFacts:
    """Факты о файле. Картинку от видео отличает сам ffprobe, а не расширение имени."""
    data = raw(path, probe, name)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    raw_duration = (data.get("format") or {}).get("duration")
    duration = float(raw_duration) if isinstance(raw_duration, (str, int, float)) and \
        raw_duration != "N/A" else None
    if video is None and audio is None:
        raise FfmpegError("RENDER_INPUT_UNPREPARED", facts={"path": name(path)})
    # Неподвижная картинка приходит контейнером `*_pipe` и без длительности: у одного кадра её нет.
    kind = "audio" if video is None else ("image" if duration is None else "video")
    return MediaFacts(path=path, kind=kind, duration_sec=duration,
                      width=video.get("width") if video else None,
                      height=video.get("height") if video else None,
                      has_video=video is not None, has_audio=audio is not None)


@dataclass(frozen=True)
class Accepted:
    """Принятый результат: длительность и кадр, подтверждённые ffprobe, — без неизвестных."""

    duration_sec: float
    width: int
    height: int


def accept(path: Path, expect_duration: float | None, expect_size: tuple[int, int] | None,
           tolerance_sec: float = 0.5, probe: str = "ffprobe", name: Callable[[Path], str] = own) -> Accepted:
    """Приёмка результата: поток, ненулевая длительность, названный и обещанный профилем кадр."""
    got, named = facts(path, probe, name), name(path)
    frame = "x".join(map(str, expect_size)) if expect_size else "—"
    if not got.has_video:                   # команда отработала без ошибки, но собрала не видео
        raise FfmpegError("RENDER_UNVERIFIED", facts={"path": named, "rule": "video_stream", "expected": 1, "got": 0})
    if not got.duration_sec:                # пустой файл проходит как успех только до первого просмотра
        raise FfmpegError("RENDER_UNVERIFIED", facts={
            "path": named, "rule": "duration_sec", "expected": round(expect_duration, 2) if expect_duration else ">0",
            "got": "—" if got.duration_sec is None else got.duration_sec})
    if got.width is None or got.height is None:
        raise FfmpegError("RENDER_UNVERIFIED", facts={"path": named, "rule": "frame", "expected": frame, "got": "—"})
    if expect_duration and abs(got.duration_sec - expect_duration) > tolerance_sec:
        raise FfmpegError("RENDER_UNVERIFIED", facts={  # часть слоёв или звука не доехала до результата
            "path": named, "rule": "duration_sec", "expected": round(expect_duration, 2),
            "got": round(got.duration_sec, 2)})
    if expect_size and (got.width, got.height) != expect_size:
        raise FfmpegError("RENDER_UNVERIFIED", facts={
            "path": named, "rule": "frame", "expected": frame, "got": f"{got.width}x{got.height}"})
    return Accepted(duration_sec=got.duration_sec, width=got.width, height=got.height)
