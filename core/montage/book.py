"""
core/montage/book.py — строки книги становятся замыслом сцены.

## Назначение
Лист сцены → `SceneSpec`: что читать и откуда, объявлено в `config/montage.yaml`, здесь только
сборка. Профиль рендера ищется вверх по дереву — своего листа у видео нет, он живёт у канала; пока
книга не заполнена, берётся дефолт декларации, и источник НАЗЫВАЕТСЯ, а не выдаётся за данные.

## Границы
Ни одного имени листа, столбца или роли в коде. Путь ассета приезжает из книги текстом и проходит
containment рабочей области — доверенным он не становится оттого, что записан в таблицу.
"""

from pathlib import Path
from typing import Callable

import yaml

from core.providers.declaration import Declaration
from core.providers.ffmpeg import AudioTrack, Layer, RenderProfile, SceneSpec

from .errors import MontageError


class SceneBook:
    """Чтение замысла сцены из данных проекта по объявленной карте листов и столбцов."""

    def __init__(self, state_manager, config_path: str | Path, resolve: Callable[[str], Path]):
        self.state = state_manager
        self.config_path = Path(config_path)
        self._resolve = resolve
        self._decl = Declaration(
            self.config_path / "montage.yaml", MontageError, "монтажа",
            "Заведи config/montage.yaml — откуда читать сцену и куда писать рендер, объявлено там.")

    @property
    def config(self) -> dict:
        return self._decl.data

    # ═══ Чтение данных ═══

    def _rows(self, table: str, sheet: str) -> dict:
        snapshot = self.state.read_snapshot(table) or {}
        return ((snapshot.get(sheet) or {}).get("rows") or {})

    def _declared_rows(self, book: str, sheet: str) -> list[dict]:
        """Дефолт из декларации книги, пока книга проекта не заполнена."""
        path = self.config_path / "templates" / "tables" / f"{book}.schema.yaml"
        if not path.exists():
            return []
        schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for declared in schema.get("sheets") or []:
            if declared.get("name") == sheet:
                return list(declared.get("rows") or [])
        return []

    def _upward(self, table: str, sheet: str) -> tuple[list[dict], str, str]:
        """Лист у самой сущности или у ближайшего предка. Иерархия не зашита — идём по адресу."""
        node = str(table or "").strip("/")
        while True:
            rows = self._rows(node, sheet) if node else {}
            if rows:
                return list(rows.values()), "project", node
            if not node:
                return [], "none", ""
            node = node.rpartition("/")[0]

    # ═══ Профиль рендера ═══

    def profile(self, table: str, profile_id: str = "") -> tuple[RenderProfile, dict]:
        """Профиль: явно названный, выбранный видео или единственный. Источник — в отчёте."""
        spec = self.config["profile"]
        rows, source, owner = self._upward(table, spec["sheet"])
        if not rows:
            rows, source, owner = self._declared_rows(spec["fallback_book"], spec["sheet"]), "declaration", ""
        if not rows:
            raise MontageError(
                "RENDER_PROFILE_INVALID", "Профилей рендера нет ни в книге канала, ни в декларации.",
                reason=f"Заполни лист {spec['sheet']} книги канала: без профиля неизвестно, чем кодировать.",
                suggested_tool="table_append")
        wanted = profile_id or self._selected(table, spec)
        by_id = {str(row.get(spec["id_column"])): row for row in rows}
        if not wanted:
            if len(by_id) == 1:
                wanted = next(iter(by_id))
            else:
                raise MontageError(
                    "RENDER_PROFILE_INVALID", "Видео не выбрало профиль рендера.",
                    reason=f"Профилей объявлено {len(by_id)}: {', '.join(sorted(by_id))}. Проставь "
                           f"{spec['selector_sheet']}.{spec['selector_column']} или назови профиль в вызове.",
                    suggested_tool="table_set")
        if wanted not in by_id:
            raise MontageError(
                "RENDER_PROFILE_INVALID", f"Профиль `{wanted}` не найден.",
                reason=f"Есть: {', '.join(sorted(by_id))}. Ссылка ведёт в лист {spec['sheet']}.",
                suggested_tool="table_find_row")
        row = {k: v for k, v in by_id[wanted].items() if v not in (None, "")}
        values = {k: v for k, v in row.items() if k in set(RenderProfile.model_fields)}
        values["profile_id"] = wanted           # имя столбца может отличаться от имени поля
        try:
            profile = RenderProfile(**values)
        except ValueError as exc:
            raise MontageError(
                "RENDER_PROFILE_INVALID", f"Строка профиля `{wanted}` не годится для рендера: {exc}",
                reason="Значения профиля едут в команду кодирования — почини строку в книге канала.",
                suggested_tool="table_set") from exc
        return profile, {"profile_id": wanted, "profile_source": source, "profile_owner": owner}

    def _selected(self, table: str, spec: dict) -> str:
        """Чем видео выбрало профиль: столбец листа мета (первая строка — само видео)."""
        for row in self._rows(table, spec["selector_sheet"]).values():
            value = row.get(spec["selector_column"])
            if value:
                return str(value)
        return ""

    # ═══ Сцена ═══

    def _fills_frame(self, table: str) -> dict:
        """Роль → растягивается ли слой на кадр. Объявляет канал, а не код рендера."""
        spec = self.config["scene"]["roles"]
        rows, _source, _owner = self._upward(table, spec["sheet"])
        if not rows:
            rows = self._declared_rows(spec["fallback_book"], spec["sheet"])
        return {str(row.get(spec["type_column"])): bool(row.get(spec["fills_frame_column"]))
                for row in rows}

    def variants_state(self, table: str, scene_id: str) -> dict:
        """Тумблер вариативности и то, пользуется ли им сцена. Совет — только при расхождении."""
        rig = self.config.get("rig") or {}
        toggle = rig.get("toggle") or {}
        rows, _source, owner = self._upward(table, toggle["sheet"])
        if not rows:
            rows = self._declared_rows(toggle["fallback_book"], toggle["sheet"])
        enabled = next((bool(row.get(toggle["enabled_column"])) for row in rows
                        if row.get(toggle["type_column"]) == toggle["fragment_type"]), None)
        el_cfg = self.config["scene"]["elements"]
        slot_column = (rig.get("variants") or {})["slot_column"]
        used = any(row.get(slot_column) for _row_id, row in self._scene_rows(table, el_cfg, scene_id))
        advice = rig.get("advice") or {}
        key = ""
        # `None` = строки тумблера в канале нет вовсе (книга старой формы). Для совета это то же
        # «не включено»: сцена уже пользуется слотами, а оценка их не считает.
        if used and not enabled:
            key = advice.get("disabled_but_used", "")
        elif enabled and not used:
            key = advice.get("enabled_but_unused", "")
        return {"variants_enabled": enabled, "variants_used": used,
                "advice_key": key, "channel": owner}

    # ═══ Оснастка: слоты и варианты ═══

    def _rig_rows(self, table: str, part: str) -> list[dict]:
        spec = (self.config.get("rig") or {}).get(part) or {}
        rows, _source, _owner = self._upward(table, spec["sheet"])
        return rows or self._declared_rows(spec["fallback_book"], spec["sheet"])

    def slots(self, table: str) -> dict[str, dict]:
        """Слоты канала по идентификатору: чем крепится, куда и в какую рамку."""
        spec = (self.config.get("rig") or {}).get("slots") or {}
        return {str(row.get(spec["id_column"])): row for row in self._rig_rows(table, "slots")
                if row.get(spec["id_column"])}

    def variants(self, table: str) -> dict[str, dict]:
        """Каталог вариантов канала по идентификатору."""
        spec = (self.config.get("rig") or {}).get("variants") or {}
        return {str(row.get(spec["id_column"])): row for row in self._rig_rows(table, "variants")
                if row.get(spec["id_column"])}

    def _variant(self, catalogue: dict, variant_id: str, row_id: str) -> dict:
        if variant_id not in catalogue:
            raise MontageError(
                "RENDER_INPUT_UNPREPARED", f"Вариант `{variant_id}` (строка {row_id}) не объявлен в каталоге.",
                reason=f"Доступные берутся из листа вариантов канала: {', '.join(sorted(catalogue)) or 'пусто'}. "
                       "Нет нужного — сгенерируй ассет и заведи строкой.",
                suggested_tool="table_append")
        return catalogue[variant_id]

    def _fits(self, size: tuple, bounds: tuple, child: str, parent: str) -> None:
        """Рамка родителя: вариант больше неё не примеряется. Это «эмоция не уплывёт за голову»."""
        width, height = size
        limit_w, limit_h = bounds
        if (limit_w and width and width > limit_w) or (limit_h and height and height > limit_h):
            raise MontageError(
                "VARIANT_INCOMPATIBLE", f"Вариант слота `{child}` не влезает в рамку слота `{parent}`: "
                f"{width}x{height} против {limit_w}x{limit_h}.",
                reason="Рамка объявлена в листе слотов канала. Возьми вариант по размеру или "
                       "поправь рамку, если она занижена.",
                suggested_tool="table_find_row")

    def _compatible(self, child: dict, parents: list[dict], key_column: str,
                    child_id: str, parent_slot: str) -> None:
        """Совместимость — сравнение двух ячеек: ключ оси у ребёнка и у вариантов родителя."""
        key = str(child.get(key_column) or "")
        parent_keys = {str(row.get(key_column) or "") for row in parents}
        if not key or not parent_keys or "" in parent_keys or key in parent_keys:
            return
        raise MontageError(
            "VARIANT_INCOMPATIBLE", f"Вариант `{child_id}` не сочетается со слотом `{parent_slot}`: "
            f"ключ `{key}` против `{', '.join(sorted(parent_keys))}`.",
            reason="Совпадение ключа оси — условие сочетаемости (обычно это ракурс). Возьми вариант "
                   "с тем же ключом или сгенерируй недостающий.",
            suggested_tool="table_find_row")

    def _values(self, row: dict, fields: list[str]) -> dict:
        """Объявленные поля строки без пустых: пустая ячейка означает «не задано», а не ноль."""
        return {name: row[name] for name in fields if row.get(name) not in (None, "")}

    def _scene_rows(self, table: str, spec: dict, scene_id: str) -> list[tuple[str, dict]]:
        return [(row_id, row) for row_id, row in self._rows(table, spec["sheet"]).items()
                if str(row.get(spec["scene_column"]) or "") == scene_id]

    def scene(self, table: str, scene_id: str, output: Path,
              profile: RenderProfile, subtitles: Path | None = None) -> tuple[SceneSpec, dict]:
        """Замысел сцены целиком. Пустое аудио — валидная сцена, пустые слои — нет."""
        scene_cfg = self.config["scene"]
        el_cfg, au_cfg = scene_cfg["elements"], scene_cfg["audio"]
        fills = self._fills_frame(table)
        slots, catalogue = self.slots(table), self.variants(table)
        rig = (self.config.get("rig") or {}).get("variants") or {}
        rows = self._scene_rows(table, el_cfg, scene_id)
        by_slot: dict[str, list[dict]] = {}
        slot_column, variant_column = rig["slot_column"], rig["id_column"]
        for _rid, row in rows:
            if row.get(slot_column) and row.get(variant_column) in catalogue:
                by_slot.setdefault(str(row[slot_column]), []).append(catalogue[str(row[variant_column])])
        layers = []
        for row_id, row in rows:
            values = self._values(row, el_cfg["fields"])
            asset = values.pop("asset_path", "")
            slot_id = str(row.get(slot_column) or "")
            slot = slots.get(slot_id) or {}
            parent_slot = str(slot.get("parent_slot") or "")
            if variant_id := str(row.get(variant_column) or ""):
                variant = self._variant(catalogue, variant_id, row_id)
                asset = asset or variant.get(rig["fields"][1]) or ""
                if parent_slot:
                    self._fits((variant.get("width"), variant.get("height")),
                               (slots.get(parent_slot, {}).get("bounds_w"),
                                slots.get(parent_slot, {}).get("bounds_h")), slot_id, parent_slot)
                    self._compatible(variant, by_slot.get(parent_slot, []), rig["key_column"],
                                     variant_id, parent_slot)
            if not asset:
                raise MontageError(
                    "SCENE_EMPTY", f"У элемента {row_id} сцены {scene_id} не заполнен ассет.",
                    reason=f"Столбец {el_cfg['sheet']}.asset_path — это то, что кладётся в кадр; "
                           f"либо сошлись на вариант каталога, у которого файл уже есть.",
                    suggested_tool="table_set")
            role = str(row.get(el_cfg["role_column"]) or "")
            # Слот объявляет, ГДЕ он крепится к родителю; строка сдвигает от этой точки.
            if slot:
                values["x"] = int(slot.get("anchor_x") or 0) + int(values.get("x") or 0)
                values["y"] = int(slot.get("anchor_y") or 0) + int(values.get("y") or 0)
                values.setdefault("fade_sec", slot.get("default_fade_sec") or 0.0)
            layers.append(self._model(
                Layer, {**values, "element_id": str(row.get(el_cfg["id_column"]) or row_id),
                        "asset_path": self._resolve(str(asset)),
                        "slot": slot_id, "parent_slot": parent_slot,
                        "fit_canvas": fills.get(role, False)}, row_id, el_cfg["sheet"]))
        if not layers:
            raise MontageError(
                "SCENE_EMPTY", f"В сцене {scene_id} нет ни одного элемента.",
                reason=f"Рендерить нечего. Разложи фрагменты по листу {el_cfg['sheet']} "
                       f"(столбец {el_cfg['scene_column']} = {scene_id}).",
                suggested_tool="table_append")
        audio = []
        for row_id, row in self._scene_rows(table, au_cfg, scene_id):
            values = self._values(row, au_cfg["fields"])
            asset = values.pop("asset_path", "")
            if not asset:
                continue                       # дорожка без файла — не отказ: строку могли завести впрок
            audio.append(self._model(
                AudioTrack, {**values, "audio_id": str(row.get(au_cfg["id_column"]) or row_id),
                             "asset_path": self._resolve(str(asset))}, row_id, au_cfg["sheet"]))
        spec = SceneSpec(scene_id=scene_id, profile=profile, output=output,
                         layers=layers, audio=audio, subtitles_path=subtitles)
        return spec, {"elements": len(layers), "audio": len(audio)}

    @staticmethod
    def _model(model, values: dict, row_id: str, sheet: str):
        """Строка книги → типизированный кусок замысла. Отказ называет ЛИСТ и СТРОКУ."""
        try:
            return model(**values)
        except ValueError as exc:
            raise MontageError(
                "VALIDATION_ERROR", f"Строка {row_id} листа {sheet} не годится для рендера: {exc}",
                reason="Значение едет в команду сборки — почини ячейку в книге.",
                suggested_tool="table_set") from exc
