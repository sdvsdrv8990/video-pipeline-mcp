"""
core/montage/template.py — шаблон сцены раскладывается СТРОКАМИ в книгу видео.

## Назначение
Заготовки живут строками книги канала и применяются дописыванием: собрать несколько шаблонов в одну
сцену значит применить их подряд со смещением по времени и порядку слоёв. Слияние строк
механическое — потому шаблон и не является файлом кода.

## Границы
Копируются только поля, объявленные для листов сцены: заготовка не может принести столбец, которого
у сцены нет. Исполнения здесь нет никакого — ни файла, ни кода.
"""

from .book import SceneBook
from .errors import MontageError
from .ledger import append_rows

ELEMENT_PREFIX = "SEL"
AUDIO_PREFIX = "SAU"


class SceneTemplate:
    """Чтение заготовок канала и их раскладка в книгу выбранного видео."""

    def __init__(self, book: SceneBook, id_generator):
        self.book = book
        self.ids = id_generator

    @property
    def config(self) -> dict:
        spec = self.book.config.get("templates") or {}
        if not spec:
            raise MontageError(
                "TEMPLATE_NOT_FOUND", "Шаблоны сцен не объявлены.",
                reason="Заведи раздел `templates` в config/montage.yaml — где лежат заготовки, сказано там.")
        return spec

    def _rows(self, table: str, part: str, template_id: str) -> list[dict]:
        spec = self.config[part]
        rows, _source, _owner = self.book._upward(table, spec["sheet"])
        if not rows:
            rows = self.book._declared_rows(spec["fallback_book"], spec["sheet"])
        return [row for row in rows if str(row.get(spec["template_column"]) or "") == template_id]

    def names(self, table: str) -> list[str]:
        """Какие шаблоны вообще есть — ответ выборкой по листу, а не выводом."""
        found = set()
        for part in ("elements", "audio"):
            spec = self.config[part]
            rows, _source, _owner = self.book._upward(table, spec["sheet"])
            rows = rows or self.book._declared_rows(spec["fallback_book"], spec["sheet"])
            found |= {str(row.get(spec["template_column"])) for row in rows
                      if row.get(spec["template_column"])}
        return sorted(found)

    def apply(self, table: str, scene_id: str, template_id: str,
              time_offset: float = 0.0, z_offset: int = 0) -> dict:
        """Разложить заготовку строками сцены. Смещения дают сборку нескольких шаблонов подряд."""
        scene_cfg = self.book.config["scene"]
        elements = self._rows(table, "elements", template_id)
        audio = self._rows(table, "audio", template_id)
        if not elements and not audio:
            raise MontageError(
                "TEMPLATE_NOT_FOUND", f"Шаблон `{template_id}` не найден.",
                reason=f"Есть: {', '.join(self.names(table)) or 'ни одного'}. Заготовки живут "
                       f"строками книги канала и правятся как любые другие.",
                suggested_tool="table_find_row")
        written = {}
        for part, prefix, cfg in (("elements", ELEMENT_PREFIX, scene_cfg["elements"]),
                                  ("audio", AUDIO_PREFIX, scene_cfg["audio"])):
            source = elements if part == "elements" else audio
            rows = [self._row(row, cfg, scene_id, time_offset, z_offset) for row in source]
            written[part] = append_rows(self.book.state, table, cfg["sheet"], rows,
                                        self.ids, prefix) if rows else []
        return {"template_id": template_id, "scene_id": scene_id,
                "elements": written["elements"], "audio": written["audio"],
                "time_offset": time_offset, "z_offset": z_offset}

    @staticmethod
    def _row(row: dict, cfg: dict, scene_id: str, time_offset: float, z_offset: int) -> dict:
        """Заготовка → строка сцены: только объявленные поля, времена и порядок со смещением."""
        allowed = set(cfg["fields"]) | {cfg["id_column"], "slot_id", "variant_id", "fade_sec",
                                        cfg["role_column"] if "role_column" in cfg else ""}
        out = {name: value for name, value in row.items()
               if name in allowed and value not in (None, "")}
        out[cfg["scene_column"]] = scene_id
        out.pop(cfg["id_column"], None)          # ID строки сцены выдаёт сервер, а не заготовка
        for field in ("time_start", "time_end"):
            # Ноль здесь ЗНАЧЕНИЕ («с начала сцены»), а не «не задано»: незаполненное приходит
            # пустой ячейкой и до сюда не доезжает. Иначе второй шаблон подряд не сдвинулся бы.
            if time_offset and isinstance(out.get(field), (int, float)):
                out[field] = round(float(out[field]) + time_offset, 3)
        if z_offset and isinstance(out.get("z_index"), (int, float)):
            out["z_index"] = int(out["z_index"]) + z_offset
        return out
