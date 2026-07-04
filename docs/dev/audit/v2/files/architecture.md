# Q&A: архитектура MCP-сервера видеопайплайна

> **Роль:** общая архитектура: две вселенные, 7 принципов, firewall, фазы, ошибки, модели данных.
> **Сквозное:** [G1](../global.md#g1-роль-сервера-исолнительвалидатор-оркестратор--снаружи), [G2](../global.md#g2-единый-конверт-ответа-toolresult), [G3](../global.md#g3-firewall-перед-ядром), [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download), [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст), [G9](../global.md#g9-две-вселенные-код-сервера-vs-workspace-данные).
> **Статус кода:** реализован (ядро + инструменты); firewall работает; провайдеры — заглушки.
> **Навигация:** `server.py` → `core/engine` → `tools/*` → `workspace/`. Обратный поток: `ToolResult/ErrorDetail` → Claude.

## Решение 1: Сервер = исполнитель+валидатор, оркестратор — снаружи
**Q:** кто принимает решения — сервер или ИИ?
**A:** сервер только исполняет и валидирует. Думает внешний Claude через туннель. Сервер не self-healing: ошибку ловит, оборачивает в `ErrorDetail`, отдаёт Claude — он решает retry/смена/человек.
**Alt:** «умный» сервер с автопочинкой — отброшено: дублирует интеллект оркестратора, прячет причины сбоев.
**Регрессия:** если сервер начнёт сам чинить — Claude теряет видимость причин.
**Где:** `server.py`, все `providers/*` (`_map_error`), `reactions`.
**Связь:** [G1](../global.md#g1-роль-сервера-исолнительвалидатор-оркестратор--снаружи), SESSIONS.md §Сессия 1.

## Решение 2: Две вселенные — код сервера vs workspace/ данные
**Q:** где граница между кодом и управляемыми данными?
**A:** `video_pipeline_mcp/` — код/конфиги/доки (git). `workspace/` — данные (не в git). Excel = source of truth; сервер делает JSON-снапшоты.
**Иерархия данных:** ниша → сетка → (наши каналы | конкуренты) → видео → ассеты.
**Регрессия:** запись за пределы `workspace/` = path traversal (D1/D29).
**Где:** `core/state/state_manager.py`, `fs_*` в `server.py`.
**Связь:** [G9](../global.md#g9-две-вселенные-код-сервера-vs-workspace-данные), [D1](../AUDIT.md#-d1), [D29](../AUDIT.md#-d29).

### Структура workspace/
```
workspace/
└── niches/<niche>/
   ├── _NICHE_INDEX.md, niche_read.json, niche_write.json
   └── networks/<network>/
      ├── _NETWORK_INDEX.md, network_dashboard.xlsx, network_config.xlsx
      ├── channels/<channel>/
      │   ├── channel_data.xlsx, channel_config.xlsx, project_memory.md
      │   └── videos/<video>/
      │       ├── video_data.xlsx, read.json, write.json, project_memory.md
      │       └── assets/{svg,scenes,audio,transitions}
      └── competitors/<наш_канал>/<конкурент>/
          ├── competitor_channel_data.xlsx, channel_meta.json
          └── videos/<видео>/competitor_video_data.xlsx, read.json, write.json
```

## Решение 3: 7 ключевых принципов
**Q:** какие правила определяют архитектуру?
**A:**
1. **Никакого хардкода.** Любое значение — из конфига, не из кода.
2. **Excel = Source of Truth.** ИИ не читает/пишет Excel напрямую. Сервер делает снапшоты в JSON.
3. **Строгая валидация.** Pydantic-схемы на каждом входе. ToolResult{status, data, error, facts}.
4. **Один файл — один дом.** Документация в docs/, поведение в config/, логика в core/tools/pipeline/.
5. **Сервер не чинит себя.** Если ошибка — ловим, оборачиваем в ErrorDetail с кодом, отправляем Claude.
6. **Три фазы инструмента:** приём задачи → фоновый опрос (где нужен) → возврат результата.
7. **Файрвол ПЕРЕД ядром.** ВСЁ что приходит извне проходит через firewall.
**Связь:** G1–G9, SESSIONS.md §Сессия 1.

## Решение 4: Файрвол ПЕРЕД ядром
**Q:** где фильтровать входящие запросы?
**A:** отдельный слой `transport → FIREWALL → engine`. Атака не доходит до инструментов.
**Порядок правил:** IP-blocklist → rate-limit → injection → anomaly (дёшево→дорого).
**Дифференцированная реакция (v2):** бан IP — только при повторных нарушениях rate-limit; injection → блок текущего запроса без бана; anomaly → блок без бана.
**Fail-closed (v2):** ошибка парсинга → HTTP 400, исключение firewall → HTTP 403 (не пропуск).
**Где:** `core/firewall/*`, `server.py:359-380`.
**Связь:** [G3](../global.md#g3-firewall-перед-ядром), [D6](../AUDIT.md#-d6), [D7](../AUDIT.md#-d7), [D10](../AUDIT.md#-d10).

### Структура firewall
```
core/firewall/
├── firewall.py            — основной класс Firewall
├── rules/
│   ├── rate_limiter.py    — ограничение частоты
│   ├── injection_detector.py — детекция prompt injection
│   ├── ip_blocklist.py    — блокировка IP
│   └── anomaly_detector.py — детекция аномалий
├── contracts.py           — FirewallDecision/Request/Result
└── logger.py              — логирование атак
```

## Решение 5: Три фазы работы инструмента
**Q:** как единообразно работать и с мгновенными, и с долгими операциями?
**A:** общий жизненный цикл: trigger → poll → download.
**Фаза 2 нужна НЕ для всех:**
- `fs_*`, `table_*`, `excel_*` — синхронные, сразу → Фаза 3
- `tts_*`, `img_*` — async API → Фаза 2
- `stt_*` (локальный) — обычно sync, на длинном аудио → Фаза 2
**Связь:** [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download), SESSIONS.md §Сессия 1.

## Решение 6: Обработка ошибок — ловим и кодируем, не чиним
**Q:** сколько информации об ошибке давать Claude?
**A:** код (маппинг на `server_reactions.yaml`) + полный текст провайдера + recovery.
**Flow:**
```
Провайдер → исключение → match_error_to_reaction(e) → code
→ get_recovery(code) → ErrorDetail{code, message=ПОЛНЫЙ текст, recovery}
→ ToolResult(status="error", error=error) → Claude
```
**Три природы медиа-сбоя:**
- `PROVIDER_FAILED` — техсбой провайдера (сервер: retry+fallback)
- `CONTENT_REJECTED` — контент-отказ (Claude: переформулировать)
- `LOCAL_INFERENCE_FAILED` — локальный сбой (деградация → человек)
**Где:** `core/contracts/error_detail.py`, `reactions`, `providers/*._map_error`.
**Связь:** [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст), SESSIONS.md §Приложение Б.

## Решение 7: Пять примитивов для таблиц
**Q:** как ИИ работает с Excel через JSON?
**A:** 5 примитивов: `get_column`, `get_row`, `set`, `append`, `delete`. Запись — read-modify-write через очередь. Формулы защищены.
**Связь:** SESSIONS.md §Сессия 3, templates.md.

## Открытые вопросы файла
- **D14 (🟡):** IP-гранулярность бесполезна за туннелем — нужна гранулярность по сессии.
- **D29 (🟠):** Path traversal через state_manager — вторая ФС-поверхность без safe-join.
- **D31 (🟡):** Секрет туннеля в argv + tunnel.yaml в git.

## Что улучшить
- Вынести `_safe_resolve` в `core/paths.py` (единая точка containment, G17).
- Гранулярность firewall по сессии/токену, не по IP (D14, G18).
