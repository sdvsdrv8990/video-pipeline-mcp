"""core/providers/ffmpeg/dictionary.py — единственный вход в словарь фильтров.

Имя из книги разрешается в фильтр ffmpeg, параметры проверяются объявленной там же схемой, а
жалоба бинаря переводится в код реестра. Словарь закрыт: значения приезжают из книги и попадают в
строку фильтров, поэтому незнакомое имя — отказ, а не пропуск. Значение, не прошедшее проверку
безопасности, наружу не уходит ни при какой схеме.
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
    if not SAFE_VALUE.match(value):             # разделители фильтрографа в значении запрещены, значение не в ответ
        raise FfmpegError("VALIDATION_ERROR", errors=[{"field": where, "rule": "filtergraph_safe"}])
    return value


class FfmpegDictionary:
    """Переходы и фильтры: наше имя → исполнитель ffmpeg; жалоба бинаря → код реестра."""

    def __init__(self, config_file: str | Path | None = None):
        self._decl = Declaration(config_file or CONFIG, FfmpegError, "фильтров ffmpeg")

    @property
    def data(self) -> dict:
        return self._decl.data

    # ═══ Переходы ═══

    def transition(self, name: str) -> dict:
        """Переход по имени: токен `xfade` (или `None` для склейки) и длительность стыка."""
        section = self.data.get("transitions") or {}
        by_name = section.get("by_name") or {}
        if name not in by_name:
            raise FfmpegError("FILTER_UNKNOWN", facts={"name": name, "known": ", ".join(sorted(by_name))})
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
            raise FfmpegError("FILTER_UNKNOWN", facts={"name": name, "known": ", ".join(sorted(by_name))})
        return by_name[name]

    def filter_expr(self, name: str, params: dict | None = None) -> str:
        """Имя + параметры → готовый кусок фильтрографа с подставленными дефолтами схемы."""
        spec = self.filter_spec(name)
        schema = spec.get("params") or {}
        given = dict(params or {})
        errors = sorted(Draft202012Validator(schema).iter_errors(given), key=lambda e: str(e.path))
        if errors:                              # поле и правило схемы, без пришедшего значения (EB-04)
            raise FfmpegError("VALIDATION_ERROR", facts={"filter": name}, errors=[
                {"field": ".".join([name, *map(str, e.path)]), "rule": str(e.validator)} for e in errors])
        pairs = []
        for key, prop in (schema.get("properties") or {}).items():
            value = given.get(key, prop.get("default"))
            if value is None:
                continue
            pairs.append(f"{guard(key, name)}={guard(fmt(value), f'{name}.{key}')}")
        return f"{guard(spec['filter'], name)}=" + ":".join(pairs) if pairs else guard(spec["filter"], name)

    # ═══ Кодеки и общие параметры выхода ═══

    def encoders(self) -> dict[str, str]:
        """Объявленная карта «кодек книги → энкодер ffmpeg» целиком."""
        by_name = ((self.data.get("codecs") or {}).get("video") or {}).get("by_name") or {}
        return {str(codec): str(encoder) for codec, encoder in by_name.items()}

    def encoder(self, codec: str) -> str:
        """Имя кодека из книги → имя энкодера ffmpeg. Человек пишет `h265`, ключу нужен `libx265`."""
        by_name = self.encoders()
        if codec not in by_name:
            raise FfmpegError("RENDER_PROFILE_INVALID", facts={"column": "codec", "allowed": ", ".join(sorted(by_name))})
        return guard(str(by_name[codec]), "codec")

    def muxer(self, container: str) -> str:
        """Контейнер профиля → muxer ffmpeg (`-f`). Человек пишет расширение `mkv`, ffmpeg зовёт формат `matroska`."""
        by_name = (self.data.get("containers") or {}).get("by_name") or {}
        if container not in by_name:
            raise FfmpegError("RENDER_PROFILE_INVALID",
                              facts={"column": "container", "allowed": ", ".join(sorted(by_name))})
        return guard(str(by_name[container]), "container")

    def output_defaults(self) -> dict:
        """Звук микса и формат пикселей: у профиля таких столбцов нет, значения общие."""
        section = self.data.get("codecs") or {}
        return {"audio": guard(str(section.get("audio", "aac")), "audio codec"),
                "pixel_format": guard(str(section.get("pixel_format", "yuv420p")), "pixel format")}

    # ═══ Жалобы бинаря ═══

    def failure(self, stderr: str) -> tuple[str, str]:
        """Код реестра и источник его фактов (`failures.by_reason.<причина>.facts`, пусто — нет) по причине, которую
        ffmpeg называет последней строкой отказа; незнакомая — `failures.default`."""
        tail = stderr.strip().rsplit("\n", 1)[-1] if stderr.strip() else ""
        for reason, rule in (self._decl.need("failures", "by_reason", kind=dict)).items():
            if reason in tail or reason in stderr:
                return str(rule["code"]), str(rule.get("facts") or "")
        return self.failure_default(), ""

    def failure_default(self) -> str:
        """Код отказа ffmpeg, причина которого не названа или не подтверждена фактом."""
        return self._decl.need("failures", "default", kind=str)
