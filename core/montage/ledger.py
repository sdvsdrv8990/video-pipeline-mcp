"""
core/montage/ledger.py — результат рендера становится строкой книги.

## Назначение
`RENDERS` заполняется приёмкой, а не рукой: длительность и `file_verified` приходят из ffprobe, и
взять их больше неоткуда. Какие столбцы заполнять — объявлено в `config/montage.yaml`.

## Границы
Пишем в снапшот точечно, мимо очереди `write.json`: та копится до осознанного `json_execute_queue`,
то есть строка рендера ждала бы чужого вызова, а применение утащило бы с собой ВСЕ накопленные
чужие операции. Тот же выбор и по той же причине сделан для учёта расхода провайдеров.
"""

from pathlib import Path

import yaml

from core.declaration import Declaration

from .errors import MontageError


def append_rows(state, table: str, sheet: str, rows: list[dict], ids, prefix: str) -> list[str]:
    """Дописать строки в лист снапшота. ID выдаёт сервер, как и в очереди.

    Одна реализация на два случая — результат рендера и раскладка шаблона: второй копией они
    разошлись бы по мелочам (одна создаёт лист, другая нет).
    """
    snapshot = state.read_snapshot(table)
    if snapshot is None:
        raise MontageError(
            "TABLE_NOT_FOUND", f"Данных проекта нет: {table}",
            reason="Писать некуда. Создай структуру проекта.",
            suggested_tool="fs_create_project_structure")
    sheet_obj = snapshot.setdefault(sheet, {"schema": {}, "rows": {}})
    written = []
    for row in rows:
        row_id = ids.generate_simple(prefix)
        sheet_obj.setdefault("rows", {})[row_id] = row
        written.append(row_id)
    state.write_snapshot(table, snapshot)
    return written


class RenderLedger:
    """Запись строки результата рендера в данные проекта."""

    def __init__(self, state_manager, config_path: str | Path, id_generator):
        self.state = state_manager
        self.config_path = Path(config_path)
        self.ids = id_generator
        self._decl = Declaration(
            self.config_path / "montage.yaml", MontageError, "монтажа",
            "Заведи config/montage.yaml — какие столбцы заполняет рендер, объявлено там.")

    @property
    def config(self) -> dict:
        return self._decl.data

    def record(self, table: str, values: dict) -> dict:
        """Новая строка результата. Возвращает ID строки и то, что в неё легло."""
        spec = self.config["target"]
        columns = spec["columns"]
        unknown = set(values) - set(columns)
        if unknown:
            raise MontageError(
                "SCHEMA_INVALID", f"Рендер пытается записать необъявленное: {', '.join(sorted(unknown))}",
                reason="Столбцы результата перечислены в config/montage.yaml → target.columns.")
        row = {columns[key]: value for key, value in values.items()}
        self._check_enums(table, spec["sheet"], row)
        row_id = append_rows(self.state, table, spec["sheet"], [row], self.ids, spec["id_prefix"])[0]
        return {"row_id": row_id, "sheet": spec["sheet"], "row": row}

    def _check_enums(self, table: str, sheet: str, row: dict) -> None:
        """Значения перечислимых столбцов сверяются с объявлением книги, а не с верой в код."""
        book = self.config["target"].get("book") or "video_data"
        path = self.config_path / "templates" / "tables" / f"{book}.schema.yaml"
        if not path.exists():
            return
        schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        declared = next((s for s in schema.get("sheets") or [] if s.get("name") == sheet), None)
        for column in (declared or {}).get("columns") or []:
            allowed = column.get("enum")
            value = row.get(column["name"])
            if allowed and value is not None and value not in allowed:
                raise MontageError(
                    "ENUM_VIOLATION", f"{sheet}.{column['name']}: `{value}` не из объявленных значений.",
                    reason=f"Допустимо: {', '.join(map(str, allowed))}. Значение приходит из "
                           f"config/montage.yaml → target.")
