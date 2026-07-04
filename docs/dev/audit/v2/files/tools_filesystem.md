# Q&A: инструменты файловой системы (fs_*)

> **Роль:** скелет рабочего пространства. 9 инструментов + filesystem_core.
> **Сквозное:** [G2](../global.md#g2-единый-конверт-ответа-toolresult), [G9](../global.md#g9-две-вселенные-код-сервера-vs-workspace-данные), [G17](../global.md#g17-containment-workspace--единая-точка-а-не-проверка-в-каждом-хендлере).
> **Статус:** реализованы; _safe_resolve закрыл D1 (fs_*); D29 (state_manager) закрыт (S7).
> **Навигация:** `tools/filesystem/` + `config/ops/filesystem.ops.yaml` → `server.py` хендлеры.

## Инструменты (все 9 реализованы)

| Инструмент | Назначение | Статус |
|---|---|---|
| `fs_create_project_structure` | материализует структуру по шаблону ИЛИ один фрагмент | ✅ |
| `fs_create_file` | создать файл (.md/.json/.yaml/.py/.txt) | ✅ |
| `fs_create_python_script` | .py с каркасом (imports/main/error handling) | ✅ |
| `fs_read_file` | прочитать файл (с лимитом размера) | ✅ |
| `fs_write_file` | полная перезапись | ✅ |
| `fs_move` | переместить файл/каталог | ✅ |
| `fs_rename` | переименовать файл/каталог | ✅ |
| `fs_delete` | удаление (с подтверждением или force=true) | ✅ |
| `fs_smart_search` | поиск по directory/extension/keyword | ✅ |
| `fs_get_directory_tree` | дерево каталога с типами файлов | ✅ |

## Решение 1: Ключевые правила fs_create_project_structure
**Q:** как материализуется структура?
**A:**
- Дискриминатор режима: `mode: "template" | "single"`
- Имя + ID на каждом фрагменте. ID присваивает сервер.
- На входе — только `parent_ids`. Guard `parent_exists`.
- Пофрагментная валидация имени → нет имени → `skip`, код `STRUCTURE_INCOMPLETE`.
**Связь:** SESSIONS.md §Приложение Ж (шаблоны).

## Решение 2: Универсальные правила
**Q:** что общего у всех fs_*?
**A:**
- Инструмент тупой и чистый — не помнит состояние между вызовами.
- Контракт строгий — своя Pydantic-схема, обязательные поля обязательны.
- Предусловия — guards в `ops.yaml`, не `if` в коде.
- Единый конверт: `ToolResult{status, data, error, facts}`.
**Связь:** [G2](../global.md#g2-единый-конверт-ответа-toolresult), D5.

## Решение 3: _safe_resolve закрыл fs_* (D1)
**Q:** защищены ли fs_* от path traversal?
**A:** да. `_safe_resolve(path)` = `root.resolve().relative_to(root)` → `ValueError` при выходе за workspace. Проверено: traversal/абс/sibling → BLOCKED.
**НО:** state_manager — ВТОРАЯ ФС-поверхность без safe-join (D29).
**Связь:** D1 (закрыт), D29 (открыт), [G17](../global.md#g17-containment-workspace--единая-точка-а-не-проверка-в-каждом-хендлере).

## Открытые вопросы
- нет критичных
