# Q&A: core/providers/ffmpeg/ffmpeg_adapter.py

> **Роль:** адаптер рендера (FFmpeg как внешний MCP) — `trigger_render`/`poll_render_status`/`download_rendered`/`cancel_render` + оркестратор `render_full_pipeline` (draft→final). ЗАГЛУШКА.
> **Сквозное:** [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download) (осн. — trigger→poll→download); [G2](../global.md#g2-единый-конверт-ответа-toolresult) (`ToolResult`); [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст) (`_map_error`→класс сбоя).
> **Статус кода:** заглушка — `trigger/poll/download/cancel` → `raise NotImplementedError` (честно, contract-first). `render_full_pipeline` оркестрация написана, но нефункциональна (зовёт NotImplementedError). **Глубокий аудит отложен до реализации.**
> **Аудит-линза:** mcp-developer (интерфейс/контракты). Проверено чтением на `.venv`.

## Решение 1: адаптер прячет внешний MCP за нашим интерфейсом (G4/G2)
**Q:** как единообразно работать с рендером?
**A:** три фазы ([G4]) — `trigger_render`→task_id, `poll_render_status`→`TaskStatus`, `download_rendered`→`ToolResult{file_path,verified}`; всё в наши контракты ([G2]). `render_full_pipeline` цепляет draft→final с `derived_from_render_id`.
**Alt:** блокирующий рендер — отброшено ([G4], длинные операции).
**Связь:** [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download); `TaskStatus`-producer — [core_contracts_task_status.md] (честная заглушка).

## Решение 2: `_map_error` → registry-код (чистая сторона D4)
**Q:** как ошибки FFmpeg лечь в нашу систему?
**A:** `_map_error(external_error)` → `LOCAL_INFERENCE_FAILED` (битый исходник/кодек/ресурс) — локальный движок ([G5] три природы сбоя). Код **∈ `server_reactions.yaml`** → чистая сторона [D4] (дрейф — только в `server.py`).
**Связь:** [D4](../AUDIT.md#-d4), [core_reactions_reactions.md].

## Отложенный аудит (при реализации — хуки, не дефекты сейчас)
- **D23 (утечка):** `_map_error`/`download` понесут `raw_response` внешнего MCP → редактировать секреты перед вложением в `ErrorDetail`.
- **D25 (Fact.id):** `Fact(type="RenderCompleted", data={video_id,scene_id})` — id в `data`, не `Fact.id`; при реализации питать из `IDGenerator`.
- **⚠ busy-loop:** `render_full_pipeline` — `while True: poll…` БЕЗ sleep/таймаута → при реализации крутит CPU/висит; добавить паузу+таймаут.
- **D30-parity, D22-invariant:** результаты пойдут через транспорт (facts/error вне content) и `TaskStatus` (инвариант) — общий фикс.
