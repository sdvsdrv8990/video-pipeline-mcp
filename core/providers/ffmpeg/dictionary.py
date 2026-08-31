"""
core/providers/ffmpeg/dictionary.py — единственный вход в словарь `config/ffmpeg_filters.yaml`.

## Назначение
Имя из книги разрешается в фильтр ffmpeg, параметры проверяются объявленной там же схемой, а
жалоба бинаря — в код реестра. Собранный кусок фильтрографа отдаётся наружу готовым.

## Границы
Словарь закрыт: значения приезжают из книги и попадают в строку фильтров, поэтому незнакомое имя —
отказ, а не пропуск. Значение, не проходящее `SAFE_VALUE`, наружу не уходит ни при какой схеме.
"""

import re
from pathlib import Path

from jsonschema import Draft202012Validator

from core.declaration import Declaration
from .errors import FfmpegError

CONFIG = Path(__file__).resolve().parents[3] / "config" / "ffmpeg_filters.yaml"

# Разделители фильтрографа (`,` `;` `[` `]` `'` `=` `:`) внутри значения делают из одного фильтра
# два. Схема их и так не пропустит — проверка стоит второй, потому что цена промаха здесь чужая
# команда, а не кривая картинка.
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.+-]+$")


def fmt(value: float | int | str) -> str:
    """Значение в текст фильтрографа: целые без хвоста `.0`, дробные без экспоненты."""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def guard(value: str, where: str) -> str:
    """Значение, уезжающее в фильтрограф. Непроходное — отказ, а не экранирование."""
    if not SAFE_VALUE.match(value):
        raise FfmpegError(
            "VALIDATION_ERROR", f"Значение `{value}` нельзя вставить в фильтр ffmpeg ({where}).",
            reason="Разделители фильтрографа в значении запрещены: разбей замысел на параметры схемы.")
    return value


class FfmpegDictionary:
    """Переходы и фильтры: наше имя → исполнитель ffmpeg; жалоба бинаря → код реестра."""

    def __init__(self, config_file: str | Path | None = None):
        self._decl = Declaration(
            config_file or CONFIG, FfmpegError, "фильтров ffmpeg",
            "Заведи config/ffmpeg_filters.yaml — чем объявленное имя является для ffmpeg, сказано там.")

    @property
    def data(self) -> dict:
        return self._decl.data

    # ═══ Переходы ═══

    def transition(self, name: str) -> dict:
        """Переход по имени: токен `xfade` (или `None` для склейки) и длительность стыка."""
        section = self.data.get("transitions") or {}
        by_name = section.get("by_name") or {}
        if name not in by_name:
            raise FfmpegError(
                "FILTER_UNKNOWN", f"Переход `{name}` не объявлен.",
                reason=f"Допустимые: {', '.join(sorted(by_name))}. Словарь закрыт намеренно.",
                suggested_tool="fs_read_file")
        spec = dict(by_name[name])
        default = (section.get("defaults") or {}).get("duration_sec", 0.0)
        return {"video_filter": section.get("video_filter"),
                "audio_filter": section.get("audio_filter"),
                "transition": spec.get("transition"),
                "duration_sec": float(spec.get("duration_sec", default))}

    # ═══ Фильтры ═══

    def filter_spec(self, name: str) -> dict:
        by_name = (self.data.get("filters") or {}).get("by_name") or {}
        if name not in by_name:
            raise FfmpegError(
                "FILTER_UNKNOWN", f"Фильтр `{name}` не объявлен.",
                reason=f"Допустимые: {', '.join(sorted(by_name))}. Незнакомое имя в фильтрограф не уходит.",
                suggested_tool="fs_read_file")
        return by_name[name]

    def filter_expr(self, name: str, params: dict | None = None) -> str:
        """Имя + параметры → готовый кусок фильтрографа с подставленными дефолтами схемы."""
        spec = self.filter_spec(name)
        schema = spec.get("params") or {}
        given = dict(params or {})
        errors = sorted(Draft202012Validator(schema).iter_errors(given), key=lambda e: str(e.path))
        if errors:
            raise FfmpegError(
                "VALIDATION_ERROR", f"Параметры фильтра `{name}` не проходят его схему: {errors[0].message}",
                reason="Схема параметров объявлена в config/ffmpeg_filters.yaml — сверься с ней.")
        pairs = []
        for key, prop in (schema.get("properties") or {}).items():
            value = given.get(key, prop.get("default"))
            if value is None:
                continue
            pairs.append(f"{guard(key, name)}={guard(fmt(value), f'{name}.{key}')}")
        return f"{guard(spec['filter'], name)}=" + ":".join(pairs) if pairs else guard(spec["filter"], name)

    # ═══ Кодеки и общие параметры выхода ═══

    def encoder(self, codec: str) -> str:
        """Имя кодека из книги → имя энкодера ffmpeg. Человек пишет `h265`, ключу нужен `libx265`."""
        section = (self.data.get("codecs") or {}).get("video") or {}
        by_name = section.get("by_name") or {}
        if codec not in by_name:
            raise FfmpegError(
                "RENDER_PROFILE_INVALID", f"Кодек `{codec}` не объявлен.",
                reason=f"Допустимые: {', '.join(sorted(by_name))}. Поправь строку профиля рендера.",
                suggested_tool="table_set")
        return guard(str(by_name[codec]), "codec")

    def output_defaults(self) -> dict:
        """Звук микса и формат пикселей: у профиля таких столбцов нет, значения общие."""
        section = self.data.get("codecs") or {}
        return {"audio": guard(str(section.get("audio", "aac")), "audio codec"),
                "pixel_format": guard(str(section.get("pixel_format", "yuv420p")), "pixel format")}

    # ═══ Жалобы бинаря ═══

    def failure_code(self, stderr: str) -> str:
        """Код реестра по причине, которую ffmpeg называет последней строкой отказа."""
        section = self.data.get("failures") or {}
        by_reason = section.get("by_reason") or {}
        tail = stderr.strip().rsplit("\n", 1)[-1] if stderr.strip() else ""
        for reason, code in by_reason.items():
            if reason in tail or reason in stderr:
                return str(code)
        return str(section.get("default", "RENDER_FAILED"))
