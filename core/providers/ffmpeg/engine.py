"""
core/providers/ffmpeg/engine.py — движок монтажа: собрать, запустить, принять результат.

## Назначение
Единственное место, где сцена превращается в файл: длительность выводится из книги и фактов о
входах, команда собирается `command.py`, отказ бинаря переводится в код реестра, а готовый файл
проходит приёмку `ffprobe` — иначе успех означал бы «файл есть», а не «файл годен».

## Границы
Ни книги, ни листов движок не знает: на вход приходит `SceneSpec`. Оболочка не используется —
аргументы уходят списком, поэтому текст из книги остаётся значением, а не командой.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .command import SceneCommand
from .dictionary import FfmpegDictionary
from .errors import FfmpegError
from .probe import MediaFacts, accept, facts
from .spec import SceneSpec

RENDER_TIMEOUT_SEC = 1800


@dataclass(frozen=True)
class RenderOutcome:
    """Результат рендера: файл, подтверждённые факты о нём и команда, которой он собран."""

    path: Path
    duration_sec: float
    width: int
    height: int
    argv: list[str]


class FfmpegEngine:
    """Сборка и исполнение команд ffmpeg с приёмкой результата."""

    def __init__(self, dictionary: FfmpegDictionary | None = None, binary: str = "ffmpeg",
                 probe: str = "ffprobe", timeout_sec: int = RENDER_TIMEOUT_SEC):
        self.dict = dictionary or FfmpegDictionary()
        self.binary_name = binary
        self.probe_name = probe
        self.timeout_sec = timeout_sec

    # ═══ Бинарь ═══

    def binary(self) -> str:
        found = shutil.which(self.binary_name)
        if not found:
            raise FfmpegError(
                "FFMPEG_MISSING", f"Нечем монтировать: {self.binary_name} не найден в PATH.",
                reason="ffmpeg — системный бинарь, а не пакет pip: поставь его пакетным менеджером.")
        return found

    # ═══ Замысел → команда ═══

    def inspect(self, spec: SceneSpec) -> dict[Path, MediaFacts]:
        """Факты о каждом входе, по одному разбору на файл: один ассет часто берут два слоя."""
        paths = [el.asset_path for el in spec.layers] + [tr.asset_path for tr in spec.audio]
        return {path: facts(path, self.probe_name) for path in dict.fromkeys(paths)}

    def duration(self, spec: SceneSpec, known: dict[Path, MediaFacts]) -> float:
        """Длительность сцены: сказанная книгой либо следующая из самих материалов."""
        declared = spec.declared_duration()
        if declared:
            return declared
        ends = []
        for layer in spec.layers:
            span = known[layer.asset_path].duration_sec
            if span:
                cut = (layer.source_out or span) - (layer.source_in or 0)
                ends.append(layer.time_start + cut / layer.speed)
        for track in spec.audio:
            span = known[track.asset_path].duration_sec
            if span:
                cut = (track.source_out or span) - (track.source_in or 0)
                ends.append(track.time_start + cut / track.speed)
        if not ends:
            raise FfmpegError(
                "VALIDATION_ERROR", f"Длительность сцены {spec.scene_id} неоткуда взять.",
                reason="Неподвижные картинки её не задают: проставь `time_end` элементу или длительность сцены.",
                suggested_tool="table_set")
        return max(ends)

    def build(self, spec: SceneSpec) -> tuple[list[str], float]:
        known = self.inspect(spec)
        span = self.duration(spec, known)
        return SceneCommand(spec, known, self.dict, span).build(self.binary()), span

    # ═══ Запуск ═══

    def run(self, argv: list[str]) -> str:
        """Запуск без оболочки. Ненулевой код — отказ кодом реестра, а не сырым текстом наружу."""
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout_sec)
        except subprocess.TimeoutExpired as exc:
            raise FfmpegError(
                "RENDER_TIMEOUT", f"Рендер не уложился в {self.timeout_sec} с.",
                reason="Процесс снят, файл не готов. Раздели сцену или подними лимит времени.") from exc
        if done.returncode != 0:
            tail = (done.stderr or "").strip().rsplit("\n", 1)[-1] or "причина не названа"
            raise FfmpegError(self.dict.failure_code(done.stderr or ""),
                              f"ffmpeg отказался: {tail}",
                              reason="Строка отказа — слова самого ffmpeg о том, что ему помешало.")
        return done.stderr or ""

    def render_scene(self, spec: SceneSpec) -> RenderOutcome:
        """Сцена из книги → проверенный файл. Успех означает, что результат принят ffprobe."""
        argv, span = self.build(spec)
        spec.output.parent.mkdir(parents=True, exist_ok=True)
        self.run(argv)
        got = accept(spec.output, span, spec.profile.size, probe=self.probe_name)
        return RenderOutcome(path=spec.output, duration_sec=got.duration_sec or 0.0,
                             width=got.width or 0, height=got.height or 0, argv=argv)
