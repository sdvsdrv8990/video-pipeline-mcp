# Q&A: core/providers/tts/litellm_tts.py

> **Роль:** адаптер TTS через LiteLLM (облако/локально) — `trigger_generation`/`poll_status`/`download_audio`. ЗАГЛУШКА.
> **Сквозное:** [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download) (trigger→poll→download); [G2](../global.md#g2-единый-конверт-ответа-toolresult); [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст) (`_map_error`).
> **Статус кода:** заглушка — все методы `raise NotImplementedError` (честно, contract-first). `__init__(api_url, api_key, timeout)`. **Глубокий аудит отложен до реализации.**
> **Аудит-линзы:** mcp-developer (интерфейс), security-reviewer (api_key при реализации). Проверено чтением на `.venv`.

## Решение 1: TTS через LiteLLM, sync/async по провайдеру (G4)
**Q:** как единообразно звать разных TTS-провайдеров?
**A:** LiteLLM как единый шлюз; sync → сразу файл+verify→`ToolResult{file_path}`, async → `task_id`→поллинг ([G4]). Ответы — наши контракты ([G2]).
**Alt:** прямые SDK каждого провайдера — отброшено (шлюз абстрагирует).
**Связь:** [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download); `TaskStatus` — [core_contracts_task_status.md].

## Решение 2: `_map_error` → registry-коды (чистая сторона D4)
**Q:** как ошибки LiteLLM лечь в систему?
**A:** `_map_error(api_error)` → `CONTENT_REJECTED` (модерация → переформулировать) ∨ `PROVIDER_FAILED` (тех.сбой → retry/смена). Коды **∈ `server_reactions.yaml`** ([G5] три природы) → чистая сторона [D4].
**Связь:** [D4](../AUDIT.md#-d4), [core_reactions_reactions.md].

## Отложенный аудит (при реализации — хуки, не дефекты сейчас)
- **🔑 api_key (D31/секрет-семейство):** `__init__(api_key=…)` — при реализации брать из env, НЕ хардкод/не в git; не логировать.
- **D23 (утечка):** `_map_error` делает `message=str(api_error)` — сырой ответ LiteLLM может нести секреты/ключи → редактировать перед `ErrorDetail`.
- **D30/D22:** результаты через транспорт (parity) + `TaskStatus` (инвариант) — общий фикс.
