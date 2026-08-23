"""
core/providers/ffmpeg/probe.py — что на самом деле лежит в файле, по словам ffprobe.

## Назначение
Движку нужно знать про вход то, чего расширение не говорит: картинка это или видео, есть ли звук,
сколько длится. И тот же разбор принимает результат: собранный файл считается готовым только
после подтверждения — иначе немая потеря вместо отказа.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import FfmpegError

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
    found = shutil.which(name)
    if not found:
        raise FfmpegError(
            "FFMPEG_MISSING", f"Нечем проверить результат: {name} не найден в PATH.",
            reason="ffprobe ставится тем же пакетом, что и ffmpeg: без него приёмка рендера невозможна.")
    return found


def raw(path: Path, probe: str = "ffprobe") -> dict:
    """Сырой разбор ffprobe. Файла нет или он не разбирается — отказ кодом реестра."""
    if not path.exists():
        raise FfmpegError(
            "FILE_NOT_FOUND", f"Файл не найден: {path}",
            reason="Путь берётся из книги; проверь, что ассет создан и лежит там, где записано.")
    out = subprocess.run(
        [binary(probe), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=PROBE_TIMEOUT_SEC)
    if out.returncode != 0:
        raise FfmpegError(
            "RENDER_INPUT_UNPREPARED", f"ffprobe не разбирает файл: {path}",
            reason=(out.stderr.strip().rsplit("\n", 1)[-1] or "Формат не опознан") + ".")
    try:
        return json.loads(out.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise FfmpegError("RENDER_UNVERIFIED", f"ffprobe ответил неразбираемым: {path}",
                          reason=str(exc)) from exc


def facts(path: Path, probe: str = "ffprobe") -> MediaFacts:
    """Факты о файле. Картинку от видео отличает сам ffprobe, а не расширение имени."""
    data = raw(path, probe)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    raw_duration = (data.get("format") or {}).get("duration")
    duration = float(raw_duration) if isinstance(raw_duration, (str, int, float)) and \
        raw_duration != "N/A" else None
    if video is None and audio is None:
        raise FfmpegError(
            "RENDER_INPUT_UNPREPARED", f"В файле нет ни картинки, ни звука: {path}",
            reason="Монтировать нечего. Проверь, что ассет сгенерирован полностью.")
    # Неподвижная картинка приходит контейнером `*_pipe` и без длительности: у одного кадра её нет.
    kind = "audio" if video is None else ("image" if duration is None else "video")
    return MediaFacts(path=path, kind=kind, duration_sec=duration,
                      width=video.get("width") if video else None,
                      height=video.get("height") if video else None,
                      has_video=video is not None, has_audio=audio is not None)


def accept(path: Path, expect_duration: float | None, expect_size: tuple[int, int] | None,
           tolerance_sec: float = 0.5, probe: str = "ffprobe") -> MediaFacts:
    """Приёмка результата: поток, ненулевая длительность и обещанная профилем геометрия."""
    got = facts(path, probe)
    if not got.has_video:
        raise FfmpegError("RENDER_UNVERIFIED", f"В результате нет видеопотока: {path}",
                          reason="Команда отработала без ошибки, но собрала не видео.")
    if not got.duration_sec:
        raise FfmpegError("RENDER_UNVERIFIED", f"Длительность результата нулевая: {path}",
                          reason="Пустой файл проходит как успех только до первого просмотра.")
    if expect_duration and abs(got.duration_sec - expect_duration) > tolerance_sec:
        raise FfmpegError(
            "RENDER_UNVERIFIED", f"Длительность разошлась с замыслом: {got.duration_sec:.2f} с вместо "
            f"{expect_duration:.2f} с.",
            reason="Расхождение означает, что часть слоёв или звука не доехала до результата.")
    if expect_size and (got.width, got.height) != expect_size:
        raise FfmpegError(
            "RENDER_UNVERIFIED", f"Кадр {got.width}x{got.height} вместо обещанного профилем "
            f"{expect_size[0]}x{expect_size[1]}.",
            reason="Геометрию задаёт профиль рендера; расхождение ломает склейку сцен.")
    return got
