"""
core/montage/script.py — чистый скрипт сцены по требованию: требования канала есть, наполнения нет.

## Назначение
ИИ вправе писать свой скрипт, но не обязан выяснять формат кадра и кодек: сервер выдаёт скелет,
в который уже подтянуты профиль рендера и состояние тумблера вариативности. Один источник
требований на оба пути — иначе логика скрипта и логика книги разойдутся.

## Границы
Скелет и тексты живут в декларации, здесь только подстановка. Значения из книги уезжают В ФАЙЛ,
поэтому литералы кладутся через `repr`, а проза чистится: кавычка из ячейки не должна становиться
кодом. Ни строчки этот модуль не исполняет — он пишет текст.
"""

import re

from core.providers.ffmpeg.spec import RenderProfile

from .book import SceneBook
from .errors import MontageError

LEFTOVER = re.compile(r"\{\{[A-Z_]+\}\}")


def prose(value: object) -> str:
    """Значение, уезжающее в ТЕКСТ файла: кавычки и переводы строк тут закрывают докстринг."""
    return re.sub(r'["\'\\\n\r]', " ", str(value)).strip()


class SceneScript:
    """Сборка скрипта сцены из объявленного скелета и требований книги канала."""

    def __init__(self, book: SceneBook):
        self.book = book

    @property
    def config(self) -> dict:
        spec = (self.book.config.get("script") or {})
        if not spec:
            raise MontageError(
                "TEMPLATE_NOT_FOUND", "Шаблон сцены не объявлен.",
                reason="Заведи раздел `script` в config/montage.yaml — что выдавать, объявлено там.")
        return spec

    def skeleton(self) -> str:
        path = self.book.config_path / "templates" / "scripts" / str(self.config["template"])
        if not path.exists():
            raise MontageError(
                "TEMPLATE_NOT_FOUND", f"Скелет скрипта сцены не найден: {path.name}",
                reason="Файл объявлен в config/montage.yaml → script.template.")
        return path.read_text(encoding="utf-8")

    def target(self, table: str, scene_id: str) -> str:
        """Путь скрипта внутри сущности видео — по объявленному месту и имени.

        Имя собирается из ЗНАЧЕНИЯ книги, поэтому в нём остаются только буквы, цифры и `_-`:
        разделитель пути или точка в имени сцены уводили бы файл из объявленного места.
        """
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", str(scene_id)).strip("_") or "scene"
        name = str(self.config["name"]).format(scene_id=safe)
        return f"{table.strip('/')}/{self.config['dir']}/{name}.py"

    def slots(self, table: str) -> list[dict]:
        """Слоты оснастки для скелета: чем ИИ вправе пользоваться и что к чему крепится."""
        rig = (self.book.config.get("rig") or {}).get("slots") or {}
        rows, _source, _owner = self.book._upward(table, rig["sheet"])
        if not rows:
            rows = self.book._declared_rows(rig["fallback_book"], rig["sheet"])
        out = []
        for row in rows:
            slot = {rig["id_column"]: row.get(rig["id_column"]),
                    rig["parent_column"]: row.get(rig["parent_column"]) or None,
                    rig["key_column"]: row.get(rig["key_column"])}
            out.append({k: (prose(v) if isinstance(v, str) else v) for k, v in slot.items()})
        return out

    def render(self, table: str, scene_id: str, profile: RenderProfile, report: dict) -> str:
        """Скелет + значения. Незакрытый плейсхолдер — отказ: полуготовый файл хуже отсутствия."""
        state = self.book.variants_state(table, scene_id)
        enabled = bool(state.get("variants_enabled"))
        shapes = self.config["element_shape"]
        scene_cfg = self.book.config["scene"]
        values = {
            "SCENE_TITLE": prose(scene_id), "PROFILE_TITLE": prose(profile.profile_id),
            "PROFILE_SOURCE": prose(report.get("profile_source", "")),
            "ELEMENTS_SHEET": prose(scene_cfg["elements"]["sheet"]),
            "AUDIO_SHEET": prose(scene_cfg["audio"]["sheet"]),
            "SCENE_ID": repr(str(scene_id)), "PROFILE_ID": repr(str(profile.profile_id)),
            "RESOLUTION": repr(profile.resolution), "ASPECT_RATIO": repr(profile.aspect_ratio),
            "FPS": repr(profile.fps), "CODEC": repr(profile.codec),
            "CONTAINER": repr(profile.container), "SUBTITLES_MODE": repr(profile.subtitles_mode),
            "VARIANTS_ENABLED": repr(enabled),
            "VARIANTS_NOTE": prose(self.config["notes"]["enabled" if enabled else "disabled"]),
            "SLOTS": repr(self.slots(table)) if enabled else "[]",
            "ELEMENT_SHAPE": str(shapes["with_slots" if enabled else "plain"]),
            "AUDIO_SHAPE": str(self.config["audio_shape"]),
        }
        text = self.skeleton()
        for key, value in values.items():
            text = text.replace("{{" + key + "}}", value)
        if left := LEFTOVER.findall(text):
            raise MontageError(
                "TEMPLATE_INVALID", f"Скелет просит значений, которых сервер не даёт: {', '.join(sorted(set(left)))}",
                reason="Плейсхолдер скелета остался незакрытым — файл вышел бы полуготовым.")
        return text
