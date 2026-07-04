# Документация разработки — навигация для ИИ

**Что это:** единая точка входа в документацию проекта `video_pipeline_mcp`. Если ты открыл этот файл — ты в правильном месте.

**Правило:** v2 аудит = авторитет. При расхождении с чем-либо побеждает `audit/v2/`.

---

## Структура

```
docs/dev/
├── README.md                 ← ТЫ ЗДЕСЬ
├── CONTEXT_PROTOCOL.md       ← протокол: что читать перед задачей
├── questions.md              ← 113 инженерных вопросов (A1–F22)
├── workflow.md               ← процесс: 5 типов задач, write-back
│
└── audit/v2/
    ├── README.md             ← операционный мануал аудита
    ├── SESSIONS.md           ← хронология: 5 сессий → решения → D# → G#
    ├── AUDIT.md              ← дефекты D1–D31 (что улучшить)
    ├── global.md             ← сквозные решения G1–G18 (почему так)
    └── files/ (47)           ← Q&A по каждому файлу/модулю
```

---

## Краткая карта файлов

### Корень docs/dev/

| Файл | Зачем | Когда читать |
|---|---|---|
| `CONTEXT_PROTOCOL.md` | Протокол: какие файлы читать под каждый тип задачи | Перед ЛЮБОЙ задачей |
| `questions.md` | 113 инженерных вопросов с привязкой к D#/G# | Когда нужно понять статус вопроса |
| `workflow.md` | Процесс v2: типы задач, write-back, шаблон | Перед началом работы |

### audit/v2/ — ядро системы

| Файл | Зачем | Когда читать |
|---|---|---|
| `README.md` | Операционный мануал: холодный старт, правила | Первый файл v2 |
| `SESSIONS.md` | Хронология: что делали по сессиям 1–5 | Когда нужен контекст «что уже решено» |
| `AUDIT.md` | Дефекты D1–D31: severity, эмпирика, ремедиация | Когда ищем что улучшить |
| `global.md` | Сквозные решения G1–G18: почему так | Когда решение затрагивает несколько файлов |

### audit/v2/files/ — 47 файловых разборов

Каждый файл = Q&A: решения + Regressia + Связь + Открытые вопросы + Что улучшить.

**Группа: Архитектура и ядро (10)**

| Файл | Модуль | Ключевые D#/G# |
|---|---|---|
| `architecture.md` | Общая архитектура | G1–G5, G9 |
| `core.md` | Ядро сервера (core/) | G2, G5, G13, G15 |
| `server.md` | Точка входа (server.py) | D3, D12, D21, G7, G18 |

**Группа: Контракты (5)**

| Файл | Модуль | Ключевые D#/G# |
|---|---|---|
| `core_contracts_tool_result.md` | ToolResult | G2, D22 |
| `core_contracts_error_detail.md` | ErrorDetail | G5, D23, D27 |
| `core_contracts_fact.md` | Fact | D25 |
| `core_contracts_task_status.md` | TaskStatus | G4, D22 |
| `core_contracts_init.md` | Экспорт | G13 |

**Группа: Движок и состояние (3)**

| Файл | Модуль | Ключевые D#/G# |
|---|---|---|
| `core_engine_engine.md` | Engine | D5, D4, D26 |
| `core_state_state_manager.md` | State | D9, D24, D29, G17 |
| `core_reactions_reactions.md` | Reactions | D4, D27, G15 |

**Группа: Файрвол (6)**

| Файл | Модуль | Ключевые D#/G# |
|---|---|---|
| `core_firewall_firewall.md` | Firewall.check | G3, D10 |
| `core_firewall_contracts.md` | FirewallDecision/Request/Result | D20, D21, G13 |
| `core_firewall_rules_rate_limiter.md` | Rate limiter | D6, D14, D16, G12 |
| `core_firewall_rules_injection_detector.md` | Injection detector | D7, D15 |
| `core_firewall_rules_ip_blocklist.md` | IP blocklist | D14 |
| `core_firewall_rules_anomaly_detector.md` | Anomaly detector | D8, D17, D18, D19 |

**Группа: Транспорт и туннель (2)**

| Файл | Модуль | Ключевые D#/G# |
|---|---|---|
| `core_transport_transport.md` | JSON-RPC транспорт | D13, D30, G7 |
| `core_transport_tunnel.md` | Cloudflare tunnel | D31, G11 |

**Группа: IDs и провайдеры (5)**

| Файл | Модуль | Ключевые D#/G# |
|---|---|---|
| `core_ids_id_generator.md` | IDGenerator | D9, D28, G10 |
| `core_providers_ffmpeg.md` | FFmpeg adapter | G16 |
| `core_providers_tts.md` | TTS (LiteLLM) | G5, G16 |
| `core_providers_stt.md` | STT (stable-ts) | G5, G16 |
| `core_providers_img.md` | IMG (LiteLLM) | G5, G16 |

**Группа: Инструменты (7)**

| Файл | Модуль | Ключевые D#/G# |
|---|---|---|
| `tools_filesystem.md` | fs_* инструменты | D1, D4, D29, G17 |
| `tools_tables.md` | table_* примитивы | G2, G9 |
| `tools_excel.md` | excel_* структура | G9 |
| `tools_media_overview.md` | Media обзор | G4, G5, G16 |
| `tools_media_tts.md` | TTS инструменты | G4, G5 |
| `tools_media_stt.md` | STT инструменты | G4, G5 |
| `tools_media_img.md` | IMG инструменты | G4, G5 |

**Группа: Видео и пайплайн (3)**

| Файл | Модуль | Ключевые D#/G# |
|---|---|---|
| `tools_video_overview.md` | Видеомонтаж | G4, G11 |
| `tools_video_ffmpeg_adapter.md` | FFmpeg adapter | G1, G16 |
| `pipeline.md` | Пайплайн | G1, G4 |

**Группа: Данные и конфиги (8)**

| Файл | Модуль | Ключевые D#/G# |
|---|---|---|
| `templates.md` | Шаблоны workspace | G9, G16 |
| `server_reactions.md` | Реестр реакций | G5, G15 |
| `project_memory_spec.md` | Память проекта | G9 |
| `tables_video_data.md` | video_data.xlsx (23 листа) | G9 |
| `tables_channel_data.md` | channel_data.xlsx (15 листов) | G9 |
| `tables_channel_config.md` | channel_config.xlsx (7 листов) | G9 |
| `tables_network_config.md` | network_config.xlsx (4 листа) | G9 |
| `tables_competitor_channel_data.md` | competitor_channel_data.xlsx (10 листов) | G9 |
| `tables_competitor_video_data.md` | competitor_video_data.xlsx (19 листов) | G9 |
| `tables_niche_network_data.md` | niche_network_data.xlsx (12 листов) | G9 |

**Группа: Безопасность и тесты (3)**

| Файл | Модуль | Ключевые D#/G# |
|---|---|---|
| `firewall_sessions.md` | План развития файрвола (F1–F8) | G3, G12, G17, G18 |
| `threat_landscape.md` | Паттерны атак | G3, G12, G17, G18 |
| `testing_strategy.md` | Стратегия тестирования | G8 |

---

## Порядок чтения (быстрый старт)

```
1. docs/dev/README.md          ← ты здесь
2. docs/dev/CONTEXT_PROTOCOL.md ← определить тип задачи → получить список файлов
3. audit/v2/README.md          ← правила v2
4. audit/v2/SESSIONS.md        ← что уже сделано
5. audit/v2/AUDIT.md           ← что сломано
6. audit/v2/files/<нужный>.md  ← детали конкретного файла
7. questions.md                ← статус вопроса
```

---

## Связи между файлами

```
CONTEXT_PROTOCOL.md ──определяет──→ какой файл читать
        │
        ▼
questions.md ──привязан к──→ D#/G# в AUDIT.md / global.md
        │
        ▼
SESSIONS.md ──хронология──→ откуда взялся D#/G#
        │
        ▼
files/*.md ──детали──→ конкретный файл/модуль
        │
        ▼
workflow.md ──процесс──→ как работать (write-back после каждого прохода)
```

---

## Ключевые сокращения

| Сокращение | Что значит |
|---|---|
| `D#` | Дефект из AUDIT.md (D1–D31) |
| `G#` | Сквозное решение из global.md (G1–G18) |
| `F#` | Сессия развития файрвола из firewall_sessions.md (F1–F8) |
| `A/S/C/P/R/T/D/Q/I/F` | Секция вопросов из questions.md |
| `S1–S5` | Сессии из SESSIONS.md |
| `v2` | Текущая версия аудита (autorитет) |
| `RMW` | Read-Modify-Write |
| `SOT` | Source of Truth |
| `FP/FN` | False Positive / False Negative |

---

## Для ИИ: что делать при открытии этого файла

1. **Прочитай CONTEXT_PROTOCOL.md** — определи тип задачи
2. **Получи список файлов** для чтения из протокола
3. **Прочитай их по порядку** (согласно протоколу)
4. **Ответь на вопросы** из questions.md (честно, с доказательствами)
5. **Выполни задачу**
6. **Сделай write-back** — обнови SESSIONS.md / AUDIT.md / global.md / files/*.md
