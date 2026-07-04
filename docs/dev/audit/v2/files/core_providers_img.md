# Q&A: core/providers/img/litellm_img.py

> **Роль:** адаптер генерации изображений через LiteLLM — `trigger_generation`/`poll_status`/`download_image`. ЗАГЛУШКА.
> **Сквозное:** [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download) (trigger→poll→download, чаще async); [G2](../global.md#g2-единый-конверт-ответа-toolresult); [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст).
> **Статус кода:** заглушка — все методы `raise NotImplementedError` (честно, contract-first). `__init__(api_url, api_key, timeout=120)`. **Глубокий аудит отложен до реализации.**
> **Аудит-линзы:** mcp-developer (интерфейс), security-reviewer (api_key при реализации). Проверено чтением на `.venv`.

## Решение 1: IMG через LiteLLM, async по умолчанию; сцена → несколько запросов (G4)
**Q:** как генерировать изображения сцены?
**A:** LiteLLM-шлюз; генерация обычно async → `task_id`→поллинг ([G4]); одна сцена = НЕСКОЛЬКО `img_*`-запросов (варианты). Ответы — контракты ([G2]).
**Alt:** прямые SDK / sync-only — отброшено (долгая генерация).
**Связь:** [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download); `TaskStatus` — [core_contracts_task_status.md].

## Решение 2: `_map_error` → registry-коды (чистая сторона D4)
**Q:** как ошибки лечь в систему?
**A:** `_map_error` → `CONTENT_REJECTED`/`PROVIDER_FAILED` (∈ `server_reactions.yaml`, [G5]) → чистая сторона [D4].
**Связь:** [D4](../AUDIT.md#-d4).

## Отложенный аудит (при реализации — хуки, не дефекты сейчас)
- **🔑 api_key** (как TTS): env, не хардкод/не в git; не логировать.
- **D23 (утечка):** сырой ответ LiteLLM в `message`/`raw_response` → редактировать секреты.
- **D30/D22:** транспорт-parity + `TaskStatus`-инвариант — общий фикс.
- **verify/пути:** `download_image` вернёт `image_paths` в `workspace/` — при реализации safe-join ([D29]-семейство).
