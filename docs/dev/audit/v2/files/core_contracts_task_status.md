# Q&A: core/contracts/task_status.py

> **Роль:** статус async-задачи для фазы поллинга — `TaskStatus{task_id,status,progress,result,error}`. Возвращается `poll_*`, отражает `pending→processing→completed/failed`.
> **Сквозное:** [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download) (осн. — фаза 2 поллинга, отдельно от `ToolResult`); [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст) (`error: ErrorDetail`); [G10](../global.md#g10-id-генерирует-сервер-не-claude) (`task_id`).
> **Статус кода:** реализован (Pydantic v2, 5 полей). **Лучший из контрактов по гигиене:** `status` — строгий `Literal`, словарь docstring совпадает с кодом (нет дрейфа D25/D4). Производители — честные заглушки. Единственный изъян — общий с [D22] инвариант terminal-состояния.
> **Навигация (знать не читая):** `core/contracts/task_status.py`. Поверхность: `TaskStatus(BaseModel)` — `task_id:str`, `status:Literal["pending","processing","completed","failed"]`, `progress:dict|None`, `result:dict|None`, `error:ErrorDetail|None`. Импортирует `.error_detail.ErrorDetail`. Производители: `providers/{ffmpeg,tts,img,stt}.poll_*` — все `raise NotImplementedError` (async-фаза ещё не построена).
> **Аудит-линзы:** mcp-developer (осн. — контракт/фаза), test-master (инвариант/дрейф). Находки доказаны запуском на `.venv`.

## Решение 1: 5 полей, `task_id` первым — идентификатор для поллинга
**Q:** какой минимум нужен, чтобы опрашивать длинную задачу?
**A:** `task_id` (по нему поллят, первый — [G6]) → `status` (что дальше) → `progress` (опц., для длинных) → `result` (при completed) → `error` (при failed). Разделение с `ToolResult` — сознательное ([G4] Решение 4): trigger отдаёт `ToolResult` с `task_id`, poll отдаёт `TaskStatus`.
**Alt:** влить статус в `ToolResult` (`task_id` там же) — отброшено: смешивает фазы trigger/poll/download ([G4]).
**Регрессия:** удаление `task_id` → нечем опрашивать; новый `status` → правка poll-логики.
**Связь:** [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download); trigger-конверт — [core_contracts_tool_result.md].

## Решение 2: `status` — строгий `Literal` из 4 значений (сделано ПРАВИЛЬНО)
**Q:** как задать словарь статусов, чтобы Claude не гадал?
**A:** `Literal["pending","processing","completed","failed"]` — Pydantic отсекает невалидное на границе. Словарь docstring («pending → processing → completed/failed») **совпадает** с кодом.
**Alt:** `status: str` — отброшено (правильно!): свободная строка дала бы дрейф.
**Регрессия / проверено запуском:** `TaskStatus(status="done")` → `ValidationError` (доказано). **Позитивный контраст:** тот же автор здесь применил `Literal` и синхронный docstring — ровно то, чего НЕ хватает `ErrorDetail.code` ([D4]) и `Fact.type` ([D25], где docstring-словарь вообще не пересекается с эмитируемым). Эталон для фикса D4/D25.
**Связь:** контраст с [D4](../AUDIT.md#-d4), [D25](../AUDIT.md#-d25).

## Решение 3: `result`/`error` опциональны (terminal-заполнение) — но инвариант не форсится (D22)
**Q:** когда заполняются `result` и `error`?
**A:** замысел — `result` при `completed`, `error` при `failed`, оба пусты в `pending`/`processing`. Опциональность отражает «ещё не готово».
**Alt:** обязательные `result`+`error` — отброшено: в `pending` их нет.
**Регрессия / находка (доказано запуском):** согласованность `status`↔(`result`/`error`) **не проверяется** — приняты `completed` без `result`, `failed` без `error`, `pending` С `result`. Это тот же класс, что [D22] в `ToolResult`, но во ВТОРОМ контракте-конверте → дефект сквозной: оба terminal-конверта лишены `model_validator`. Claude может получить `failed` без диагностики или `completed` без данных.
**Связь:** [D22](../AUDIT.md#-d22) (расширен на TaskStatus); `error` переиспользует `ErrorDetail` → к нему применимы [D4](../AUDIT.md#-d4)/[D23](../AUDIT.md#-d23) (дрейф кода / утечка `raw_response`).

## Решение 4: контракт-first — производители честные заглушки (контраст с D24)
**Q:** раз провайдеры — заглушки, `TaskStatus` вообще где-то создаётся?
**A:** нет — все `poll_*` (`ffmpeg.poll_render_status`, `tts.poll_status`, `img.poll_status`, `stt`) **явно `raise NotImplementedError("…будет реализован при подключении…")`**. Контракт определён ВПЕРЁД producers (contract-first): форма фазы 2 зафиксирована, реализация придёт с провайдерами.
**Alt:** не определять `TaskStatus` до реализации — отброшено: тогда провайдеры расходятся в форме статуса.
**Регрессия / важный контраст:** это **НЕ** «декларация без проводки» уровня [D24]. Там доки обещали поток `facts→_SESSION_LOG`, а код молча его не делал (тихий разрыв). Здесь код **честен**: `NotImplementedError` кричит «не готово». Разрыв документирован самим кодом → не дефект, а незавершённая фаза. Проверять при внедрении провайдеров (T4).
**Связь:** контраст с [D24](../AUDIT.md#-d24); реализация — [core_providers_ffmpeg.md], [core_providers_tts.md] и др. (T4, заглушки).

## Открытые вопросы файла
- **🟡 D22 (расширен на TaskStatus):** инвариант `status`↔`result`/`error` не форсится — доказано (`completed` без result, `failed` без error, `pending` с result приняты). Фикс D22 (`@model_validator`) должен покрыть ОБА конверта — `ToolResult` и `TaskStatus`. См. [../AUDIT.md#-d22](../AUDIT.md#-d22).
- **⚪ error → ErrorDetail:** failed-задача понесёт `ErrorDetail` → к ней применимы [D4] (код вне реестра) и [D23] (утечка `raw_response`) — фиксить в `error_detail`, не здесь.
- **Async-фаза не построена (честно):** `poll_*` — заглушки; проверить контракт «в бою» при внедрении провайдеров (T4), не сейчас.

## Что улучшить (регрессия-тесты, линза test-master)
- Тест инвариантов (общий фикс D22): `completed` требует `result is not None`; `failed` требует `error is not None`; `pending`/`processing` требуют `result is None` — иначе `ValidationError`. Один валидатор-паттерн на `ToolResult` и `TaskStatus`.
- Тест словаря (регресс-страж): `status` вне `Literal` → `ValidationError` (эталон, который надо ПОВТОРИТЬ для `Fact.type`/`ErrorDetail.code` при фиксе D25/D4).
- При внедрении провайдеров (T4): `poll_*` возвращает валидный `TaskStatus` с корректным terminal-заполнением (снять заглушку-проверку).
