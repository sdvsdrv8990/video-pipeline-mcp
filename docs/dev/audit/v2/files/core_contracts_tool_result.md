# Q&A: core/contracts/tool_result.py

> **Роль:** единый конверт ответа ЛЮБОГО инструмента — `ToolResult{status,data,error,facts}`. База пирамиды контрактов: один тип, один парсер у Claude.
> **Сквозное:** [G2](../global.md#g2-единый-конверт-ответа-toolresult) (осн. — единый конверт); [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст) (ошибка = `ErrorDetail`, не исключение); [G13](../global.md#g13-контракты-вынесены-для-разрыва-цикла-импорта) (Pydantic-для-провода vs dataclass-для-внутреннего-DTO).
> **Статус кода:** реализован, стабилен (Pydantic v2, 4 поля). Механически исправен; инвариант согласованности полей не форсится (D22). На провод MCP выходит НЕ напрямую — маппится в transport.
> **Навигация (знать не читая):** `core/contracts/tool_result.py`. Поверхность: класс `ToolResult(BaseModel)` — `status: Literal["success","error"]`, `data: dict|None=None`, `error: ErrorDetail|None=None`, `facts: list[Fact]=[]`. Импортирует `.error_detail.ErrorDetail`, `.fact.Fact`. Производители — все инструменты/провайдеры/engine; конвертация на провод — `core/transport/transport.py:137-149`.
> **Аудит-линзы:** mcp-developer (осн. — контракт/проводка/parity), test-master (инвариант/регрессии). Находки доказаны запуском на `.venv`.

## Решение 1: один конверт `{status,data,error,facts}` на все инструменты (G2)
**Q:** как Claude отличает успех от ошибки, не зная деталей каждого инструмента?
**A:** любой инструмент возвращает один тип `ToolResult`. Один парсер у Claude вместо N форматов. `success` → `data`+`facts`; `error` → `error` (`ErrorDetail` с кодом+recovery). `facts` — память о действиях для оркестратора.
**Alt:** свободный `dict` на инструмент — отброшено: нет типизации, Claude гадает формат. Именно это «выстрелило» багом v1.1 (`server.py` возвращал `dict` вместо `ToolResult` → transport не парсил; вывод истории: «контракты — закон, не рекомендация»).
**Регрессия:** новый `status` кроме `success|error` → ломает парсинг у Claude; смена формы `data`/`facts` → ломает всех потребителей.
**Связь:** [G2](../global.md#g2-единый-конверт-ответа-toolresult); маппинг на MCP — Решение 4; ошибки — [core_contracts_error_detail.md], факты — [core_contracts_fact.md].

## Решение 2: `status` — строго `Literal["success","error"]`, async вынесен в `TaskStatus`
**Q:** почему всего два состояния, а не `pending`/`running`?
**A:** `ToolResult` — ответ ЗАВЕРШённого шага. Долгие операции живут в фазе поллинга ([G4]): `trigger_*` возвращает `ToolResult` (обычно `data` с `task_id`), а прогресс — отдельный `TaskStatus`, возвращаемый `poll_*`. `Literal` вместо `str` → невалидный статус ловится Pydantic на границе.
**Alt:** добавить `task_id`/`pending` в `ToolResult` — отброшено (историч. Решение 4): смешивает фазы trigger/poll/download.
**Регрессия:** новый член `Literal` → правка всех, кто ветвится по `status` (в т.ч. `transport.py:138`).
**Связь:** [core_contracts_task_status.md]; [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download).

## Решение 3: Pydantic v2 + порядок полей = граф зависимостей; `facts=[]` безопасен
**Q:** почему Pydantic (в отличие от dataclass-контрактов файрвола, [G13]) и не баг ли `facts: list[Fact] = []`?
**A:** этот контракт **пересекает провод MCP** → нужна валидация входа/сериализация → Pydantic v2 (в отличие от внутренних `@dataclass` файрвола). Порядок полей `status→data→error→facts` = причинно-следственный ([G6]): сначала «что дальше», потом содержимое, потом память. **`facts: list[Fact] = []` — НЕ shared-default-баг:** Pydantic v2 копирует дефолт per-instance.
**Alt:** dataclass — отброшено (нет валидации/сериализации на проводе).
**Регрессия / проверено запуском:** доказано — `x.facts.append(...)` НЕ течёт в `y.facts` (`len(y.facts)==0`, дефолты — разные объекты, identity `False`). Ровно противоположно dataclass-миру, где такой же `= []` дал бы утечку (ср. [D15] `DEFAULT_PATTERNS`). Тем не менее конвенция — `Field(default_factory=list)`: явнее и не зависит от «магии» Pydantic. Доп.: `model_config` не задан → `extra="ignore"`, лишние поля (`bogus=123`) принимаются молча и отбрасываются (проверено) — `extra="forbid"` ловил бы кривых производителей раньше.
**Связь:** [G13](../global.md#g13-контракты-вынесены-для-разрыва-цикла-импорта), [G6](../global.md#g6-порядок-кода-в-файле--граф-зависимостей-снизу-вверх); контраст с [D15](../AUDIT.md#-d15).

## Решение 4: на провод MCP `ToolResult` идёт НЕ напрямую — маппится в transport
**Q:** `ToolResult{status,data,error,facts}` — это же не MCP `CallToolResult{content,isError}`?
**A:** верно, parity — «по духу», не по форме ([G2]). Конвертация в `core/transport/transport.py:137-149`: `success` → `{"content":[{"type":"text","text":json.dumps(data)}], "facts":[...]}`; `error` → `{"content":[...], "isError":True, "error":{...}}`. `ToolResult` — внутренний контракт ядра, MCP-форма собирается на краю.
**Alt:** сериализовать `ToolResult.model_dump()` сырьём на провод — отброшено: `status/data/error/facts` не читаются MCP-клиентом (ждёт `content`/`isError`).
**Регрессия / открытый вопрос (доказано чтением transport):** `facts` и `error` кладутся **вне** MCP-`content` как нестандартные top-level ключи → спек-совместимый клиент их может не показать (как [D20] в firewall: данные есть, потребитель не читает). Проверить в аудите транспорта ([D12] Streamable-HTTP). Плюс на `success` не выставляется `isError:false` (для MCP ок — дефолт, но асимметрично).
**Связь:** [core_transport_transport.md] (T3), [D12](../AUDIT.md#-d12); паттерн «поле есть, провод его роняет» — ср. [D20](../AUDIT.md#-d20).

## Открытые вопросы файла
- **🟡 D22 (инвариант `status`↔`error` не форсится):** доказано запуском — `ToolResult(status="success", error=ed)` и `ToolResult(status="error")` (без detail) оба принимаются. При `error` без detail transport отдаёт Claude буквальное «Unknown error» (теряется причина, против [G5]); при `success` с error — error молча роняется. Нет `model_validator`. См. [../AUDIT.md#-d22](../AUDIT.md#-d22).
- **⚪ facts/error вне MCP-`content` (parity):** нестандартные ключи могут не дойти до Claude — верифицировать в аудите транспорта ([D12]).
- **⚪ `extra="ignore"` по умолчанию:** кривые производители контракта не отсекаются на границе; кандидат на `extra="forbid"`.

## Что улучшить (регрессия-тесты, линза test-master)
- Тест инвариантов (после фикса D22): `status="error"` требует `error is not None`; `status="success"` требует `error is None` — оба должны бросать `ValidationError`.
- Тест изоляции дефолта: `ToolResult(...).facts` двух инстансов — разные объекты (страж от регрессии на `default_factory` и от копипасты `= []` в dataclass-контрактах).
- Контракт-parity тест: `transport` success/error → результат содержит `content` (+`isError` на error); зафиксировать, где живут `facts` (в `content`/`structuredContent`, а не в отбрасываемом ключе) — привязать к [D12].
