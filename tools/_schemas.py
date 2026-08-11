"""
tools/_schemas.py — общие фрагменты JSON-схем инструментов.

Одно описание параметра на весь сервер: `table`/`sheet` используют и группа данных
(`tools/tables`), и группа структуры (`tools/excel`) — при расхождении текстов клиент
получил бы два разных описания одного и того же параметра.
Фрагменты входят в контракт: их правка видна в `tests/quick/tools_inventory.golden.json`.
"""

TABLE = {"type": "string", "description": "Путь к таблице (сущности) относительно workspace"}
SHEET = {"type": "string", "description": "Имя листа (регистр важен)"}
PATH = {"type": "string", "description": "Путь к .xlsx относительно workspace"}
