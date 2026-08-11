"""
tools/memory — память проекта (project_memory.md): чтение и дописывание записей.

Тонкие обёртки: containment путей — ctx.resolve (core/paths), ошибки — ctx.err (реестр реакций).
Контракт зафиксирован эталоном tests/quick/tools_inventory.golden.json.
"""

import re

from core.contracts import Fact, ToolResult
from core.engine import Engine
from tools._context import ANNOTATIONS_MODIFY, ANNOTATIONS_READONLY, ToolContext


def register(engine: Engine, ctx: ToolContext) -> None:
    """Регистрация группы memory в движке."""

    async def memory_read(path: str) -> "ToolResult":
        """Чтение памяти проекта с парсингом структуры.

        Возвращает: заголовок, записи (с полями), количество, существующие ID.
        Позволяет ИИ понять структуру ДО вставки.
        """
        try:
            target = ctx.resolve(path)
        except ValueError:
            return ctx.err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        if not target.exists():
            return ToolResult(status="success", data={"path": path, "exists": False, "entries": [], "ids": []})
        content = target.read_text(encoding="utf-8")
        # Парсим записи: ## [дата] Заголовок
        entries = []
        ids_found = []
        current_entry: dict | None = None
        for line in content.split("\n"):
            match = re.match(r"^## \[(.+?)\]\s*(.+)$", line)
            if match:
                if current_entry:
                    entries.append(current_entry)
                current_entry = {"date": match.group(1), "title": match.group(2), "fields": {}}
            elif current_entry and line.startswith("- **"):
                field_match = re.match(r"^- \*\*(.+?):\*\*\s*(.*)$", line)
                if field_match:
                    current_entry["fields"][field_match.group(1)] = field_match.group(2)
            # Ищем ID в формате PREFIX_hex
            for id_match in re.finditer(r'\b([A-Z]+_[0-9a-f]{32})\b', line):
                ids_found.append(id_match.group(1))
        if current_entry:
            entries.append(current_entry)
        return ToolResult(status="success", data={
            "path": path, "exists": True, "size": len(content),
            "entries": entries, "entry_count": len(entries),
            "ids": list(set(ids_found)),
        }, facts=[Fact(type="MemoryRead", data={"path": path, "entries": len(entries)})])

    async def memory_write(path: str, entry_date: str, title: str,
                           context: str = "", who_decided: str = "",
                           decision: str = "", reason: str = "",
                           result: str = "", after_date: str = "") -> "ToolResult":
        """Умная дозапись записи в память проекта.

        Вставляет новую запись в правильное место по дате (хронологически).
        Валидирует структуру: обязательные поля, ссылки на ID,的影响 на соседние записи.
        """
        try:
            target = ctx.resolve(path)
        except ValueError:
            return ctx.err("PATH_ESCAPE", f"Path escapes workspace: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Формируем запись
        entry = f"\n## [{entry_date}] Решение: {title}\n"
        if context:
            entry += f"- **Контекст:** {context}\n"
        if who_decided:
            entry += f"- **Кто решил:** {who_decided}\n"
        if decision:
            entry += f"- **Решение:** {decision}\n"
        if reason:
            entry += f"- **Почему:** {reason}\n"
        if result:
            entry += f"- **Результат:** {result}\n"
        else:
            entry += "- **Результат:** [ожидается]\n"
        # Читаем существующий контент
        old_content = target.read_text(encoding="utf-8") if target.exists() else ""
        # Ищем позицию для вставки (по дате)
        insert_pos = len(old_content)
        if after_date:
            # Ищем запись после которой вставлять
            pattern = rf"## \[{re.escape(after_date)}\]"
            match = re.search(pattern, old_content)
            if match:
                # Ищем конец этой записи (следующий ## или конец файла)
                next_section = re.search(r"\n## \[", old_content[match.end():])
                if next_section:
                    insert_pos = match.end() + next_section.start()
                else:
                    insert_pos = len(old_content)
        elif old_content:
            # Вставляем перед последней записью (новое сверху)
            last_entry = re.search(r"\n## \[", old_content)
            if last_entry:
                insert_pos = last_entry.start()
        # Вставляем
        new_content = old_content[:insert_pos] + entry + old_content[insert_pos:]
        target.write_text(new_content, encoding="utf-8")
        # Собираем ID из записи
        ids_in_entry = re.findall(r'\b([A-Z]+_[0-9a-f]{32})\b', entry)
        return ToolResult(status="success", data={
            "path": path, "inserted_at": insert_pos,
            "entry_date": entry_date, "title": title,
            "ids_referenced": ids_in_entry,
            "total_size": len(new_content),
        }, facts=[Fact(type="MemoryWritten", data={
            "path": path, "date": entry_date, "title": title,
            "ids": ids_in_entry, "position": insert_pos})])

    # ═══ ПАМЯТЬ ПРОЕКТА (project_memory.md) ═══
    memory_tools = [
        ("memory_read", "Память: прочитать", "Чтение памяти проекта с парсингом структуры: записи, поля, существующие ID",
         {"type": "object", "properties": {"path": {"type": "string", "description": "Путь к project_memory.md"}}, "required": ["path"]},
         memory_read, ANNOTATIONS_READONLY),
        ("memory_write", "Память: записать решение", "Умная дозапись записи в память (по дате, с валидацией полей и ссылок на ID)",
         {"type": "object", "properties": {
             "path": {"type": "string", "description": "Путь к project_memory.md"},
             "entry_date": {"type": "string", "description": "Дата записи (ГГГГ-ММ-ДД)"},
             "title": {"type": "string", "description": "Заголовок решения"},
             "context": {"type": "string", "description": "Контекст (что произошло, ссылки на ID)"},
             "who_decided": {"type": "string", "description": "Кто решил (человек / Claude)"},
             "decision": {"type": "string", "description": "Что именно сделали"},
             "reason": {"type": "string", "description": "Почему (этого нет в таблицах)"},
             "result": {"type": "string", "description": "Результат (дописывается позже, ссылки на ID/динамику)"},
             "after_date": {"type": "string", "description": "Вставить после записи с этой датой (хронология)"},
         }, "required": ["path", "entry_date", "title", "decision", "reason"]},
         memory_write, ANNOTATIONS_MODIFY),
    ]
    for name, title, desc, schema, handler, annot in memory_tools:
        engine.register(name=name, title=title, description=desc, input_schema=schema, handler=handler, group="memory", annotations=annot)  # type: ignore[arg-type]
