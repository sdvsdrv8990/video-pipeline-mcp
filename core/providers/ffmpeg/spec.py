"""
core/providers/ffmpeg/spec.py — замысел сцены в форме, которую движок умеет исполнить.

## Назначение
Строки книги приезжают сюда типами: движок не знает про листы и столбцы, а книга не знает про
ffmpeg. Что недопустимо — отбивается здесь, до сборки команды и запуска бинаря.

## Границы
Только форма и допустимость значений. Чем значение станет в фильтрографе, решает `command.py`;
роль элемента (фон, персонаж, компонент) движку не видна — вместо неё приходит `fit_canvas`.
"""

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .errors import FfmpegError

RESOLUTION = re.compile(r"^(\d{2,5})x(\d{2,5})$")
TOKEN = re.compile(r"^[A-Za-z0-9_.-]+$")
BITRATE = re.compile(r"^\d{1,7}[kKmM]?$")

AlphaMode = Literal["AS_IS", "REMOVE_BG", "CHROMAKEY", "NONE"]


def points(text: str, arity: int, what: str) -> list[tuple[float, ...]]:
    """Точки вида `t:a[,b]`, разделённые `;`. Пусто = точек нет, это не отказ."""
    out: list[tuple[float, ...]] = []
    for chunk in re.split(r"[;\n]", text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        head, _, tail = chunk.partition(":")
        values = [head] + tail.split(",")
        try:
            nums = tuple(float(v) for v in values)
        except ValueError as exc:
            raise FfmpegError(
                "VALIDATION_ERROR", f"Точка `{chunk}` в {what} не разбирается: ожидались числа.",
                reason="Форма точки — `t:значение` (и `t:x,y` для движения), точки через `;`.") from exc
        if len(nums) != arity + 1 or not tail:
            raise FfmpegError(
                "VALIDATION_ERROR", f"Точка `{chunk}` в {what}: ожидалось {arity} значения после времени.",
                reason="Форма точки — `t:значение` (и `t:x,y` для движения), точки через `;`.")
        out.append(nums)
    return sorted(out, key=lambda p: p[0])


class RenderProfile(BaseModel):
    """Строка листа профилей рендера: чем кодировать и в какой геометрии."""

    profile_id: str
    codec: str = "libx264"
    resolution: str = "1920x1080"
    aspect_ratio: str = ""
    fps: int = Field(default=30, ge=1, le=240)
    crf: int | None = Field(default=23, ge=0, le=63)
    bitrate: str = ""
    container: str = "mp4"
    subtitles_mode: Literal["BURN", "TRACK", "OFF"] = "BURN"

    @field_validator("codec", "container")
    @classmethod
    def _token(cls, v: str) -> str:
        if not TOKEN.match(v):
            raise ValueError(f"недопустимое имя `{v}`: ожидались буквы, цифры, `_.-`")
        return v

    @field_validator("resolution")
    @classmethod
    def _resolution(cls, v: str) -> str:
        if not RESOLUTION.match(v):
            raise ValueError(f"разрешение `{v}` не в форме ШИРИНАxВЫСОТА")
        return v

    @field_validator("bitrate")
    @classmethod
    def _bitrate(cls, v: str) -> str:
        if v and not BITRATE.match(v):
            raise ValueError(f"битрейт `{v}` не в форме числа с суффиксом k/M")
        return v

    @property
    def size(self) -> tuple[int, int]:
        w, h = RESOLUTION.match(self.resolution).groups()  # type: ignore[union-attr]
        return int(w), int(h)


class FilterUse(BaseModel):
    """Применение объявленного фильтра: имя из словаря + его параметры."""

    name: str
    params: dict = Field(default_factory=dict)


class Layer(BaseModel):
    """Строка элемента сцены: что кладём, где, когда и какого размера."""

    element_id: str
    asset_path: Path
    alpha_mode: AlphaMode = "AS_IS"
    x: int = 0
    y: int = 0
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    z_index: int = 0
    time_start: float = Field(default=0.0, ge=0.0)
    time_end: float | None = Field(default=None, gt=0.0)
    source_in: float | None = Field(default=None, ge=0.0)
    source_out: float | None = Field(default=None, gt=0.0)
    speed: float = Field(default=1.0, gt=0.0, le=100.0)
    motion_path: str = ""
    filters: list[FilterUse] = Field(default_factory=list)
    fit_canvas: bool = False

    @property
    def motion(self) -> list[tuple[float, ...]]:
        return points(self.motion_path, 2, f"движении элемента {self.element_id}")


class AudioTrack(BaseModel):
    """Строка аудио сцены: дорожка, её место во времени и как она звучит."""

    audio_id: str
    asset_path: Path
    audio_role: Literal["VOICE", "MUSIC", "SOUND"] = "VOICE"
    time_start: float = Field(default=0.0, ge=0.0)
    source_in: float | None = Field(default=None, ge=0.0)
    source_out: float | None = Field(default=None, gt=0.0)
    volume: float = Field(default=1.0, ge=0.0, le=10.0)
    volume_curve: str = ""
    speed: float = Field(default=1.0, gt=0.0, le=100.0)
    fade_in_sec: float = Field(default=0.0, ge=0.0)
    fade_out_sec: float = Field(default=0.0, ge=0.0)

    @property
    def curve(self) -> list[tuple[float, ...]]:
        return points(self.volume_curve, 1, f"кривой громкости {self.audio_id}")


class SceneSpec(BaseModel):
    """Сцена целиком: профиль, слои по возрастанию `z_index`, аудио и куда писать результат."""

    scene_id: str
    profile: RenderProfile
    output: Path
    layers: list[Layer] = Field(default_factory=list)
    audio: list[AudioTrack] = Field(default_factory=list)
    duration_sec: float | None = Field(default=None, gt=0.0)
    subtitles_path: Path | None = None

    @property
    def ordered(self) -> list[Layer]:
        return sorted(self.layers, key=lambda el: el.z_index)

    def declared_duration(self) -> float | None:
        """Длительность, следующая из книги: явная либо последний уход слоя со сцены."""
        if self.duration_sec:
            return self.duration_sec
        ends = [el.time_end for el in self.layers if el.time_end]
        return max(ends) if ends else None
