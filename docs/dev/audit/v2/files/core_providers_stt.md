# Q&A: core/providers/stt/stable_ts_adapter.py

> **Роль:** адаптер транскрибации (STT) локально через stable-ts — `trigger_transcription`/`parse_timestamps`. ЗАГЛУШКА.
> **Сквозное:** [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download) (обычно sync, на длинном аудио — async); [G2](../global.md#g2-единый-конверт-ответа-toolresult); [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст) (`_map_error`).
> **Статус кода:** заглушка — методы `raise NotImplementedError` (честно, contract-first). `__init__(model_size="large", device="cuda")` — локальный инференс. **Глубокий аудит отложен до реализации.**
> **Аудит-линза:** mcp-developer (интерфейс). Проверено чтением на `.venv`.

## Решение 1: STT локально через stable-ts, обычно sync (G4)
**Q:** как получать таймкоды/тишину из аудио?
**A:** локальный stable-ts (модель `large`/`medium`/`base`, `cuda`); обычно sync → `ToolResult{timestamps, silence_map}`, на длинном аудио может быть async. Ответы — контракты ([G2]).
**Alt:** облачный STT — отброшено (локально = приватность/без API-костов; деградация модели при нехватке ресурса — [G5]).
**Связь:** [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download).

## Решение 2: `_map_error(Exception)` → registry-код (чистая сторона D4)
**Q:** как локальные сбои лечь в систему?
**A:** `_map_error(inference_error: Exception)` → `LOCAL_INFERENCE_FAILED` (∈ `server_reactions.yaml`; деградация large→medium→base или человек, [G5]) → чистая сторона [D4].
**Связь:** [D4](../AUDIT.md#-d4), [core_reactions_reactions.md].

## Отложенный аудит (при реализации — хуки, не дефекты сейчас)
- **D23 (утечка):** `_map_error(Exception)` — `str(e)` локального стека → может нести пути/внутренности; редактировать перед `ErrorDetail`.
- **ресурсы/device:** `device="cuda"` хардкод-дефолт — при отсутствии GPU нужен фолбэк на cpu (граница ресурса → `LOCAL_INFERENCE_FAILED`).
- **D30/G4:** результаты через транспорт (parity) — общий фикс.
