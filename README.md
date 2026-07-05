# Video Pipeline MCP Server

MCP-сервер для управления видеопайплайном. Работает с Claude AI Web через cloudflared-туннель.

> **Статус (2026-07):** реализован слой управления данными (файлы, таблицы/Excel, структура каналов,
> поиск) + файрвол + транспорт. Продуктовый видео-пайплайн (озвучка/транскрибация/картинки/монтаж) —
> в разработке. План доведения до корпоративного уровня: [`docs/roadmap/`](docs/roadmap/README.md).
> Легенда ниже: ✅ реализовано · 🟠 заглушка/частично · 🔲 план.

## Структура проекта (фактическая)

```
video_pipeline_mcp/
├── server.py              ✅ точка входа: MCP-эндпоинт + туннель; регистрация инструментов
├── pyproject.toml         ✅ сборка/зависимости (PEP 621) · requirements.lock — точные пины
├── install.sh · run.sh    ✅ установка (.venv + cloudflared) · запуск (--tunnel)
│
├── config/                ✅ декларации (поведение, НЕ код)
│   ├── channel_config.yaml    ✅ ЕДИНЫЙ конфиг канала: resource_limits (провайдеры/модели/
│   │                             лимиты/fallback), workflow_sequences, publishing_schedule,
│   │                             metadata_defaults, automation_rules, scene_profile, render_config
│   ├── server_reactions.yaml  ✅ реестр реакций (коды → класс + recovery)
│   ├── firewall.yaml          ✅ правила файрвола
│   ├── tunnel.yaml            ✅ конфиг туннеля (gitignored — секрет)
│   └── templates/
│       ├── workspace/         ✅ 6 шаблонов: niche/network/channel/video/competitor_channel/competitor_video
│       └── tables/            🔲 схемы книг (*.schema.yaml) — воркстрим A1′
│
├── core/                  — ядро (бизнес-логика)
│   ├── contracts/         ✅ Pydantic: ToolResult, ErrorDetail, Fact, TaskStatus
│   ├── engine/            ✅ generic-движок + template_engine
│   ├── firewall/          ✅ файрвол + rules/ (rate_limit, injection, ip_blocklist, anomaly)
│   ├── state/             ✅ read.json / write.json / session log
│   ├── reactions/         ✅ читалка server_reactions
│   ├── ids/               ✅ генерация ID + link_registry
│   ├── tables/ · excel/   ✅ слой таблиц и Excel-книг
│   ├── search/            ✅ поиск по ФС/таблицам (FsSearcher + QueryPlanner)
│   ├── transport/         ✅ туннель к Claude (transport + tunnel)
│   ├── paths.py           ✅ containment путей в workspace/
│   └── providers/         🟠 адаптеры провайдеров (заглушки — в разработке)
│       ├── stt/ (stable-ts) · tts/ (LiteLLM) · img/ (LiteLLM) · ffmpeg/ (внешний MCP)
│
├── tools/                 🔲 тонкие обёртки Bounded Context (план: вынос из server.py, A2)
├── pipeline/              🔲 оркестрация процессов (entry_points + steps) — план
├── scripts/               🔲 утилиты (план: introspect_tables.py — A1′)
├── tests/                 ✅ quick/ (unit) + симуляции (virus/bot_army/cache_*/…)
└── docs/
    ├── dev/               — история файлов, спеки (gitignored)
    └── roadmap/           ✅ план развития до корпоративного уровня + канон спек (spec/)
```

**Инструменты сейчас** (52 в `tools/list`): ✅ `fs_*` (файлы), `table_*`/`json_*` (таблицы),
`excel_*` (книги), `structure_*` (создание каналов/видео по шаблонам), `search_*` (поиск);
🟠 `tts_*`/`stt_*`/`img_*`/video (media — заглушки).

## Установка

```bash
./install.sh          # .venv + зависимости (pyproject) + cloudflared
```

## Запуск

```bash
./run.sh              # сервер + туннель (--no-tunnel — только локально)
```

## Зависимости

Источник истины — `pyproject.toml` (`pip install -e .`); точные пины — `requirements.lock`;
dev-инструменты — `pip install -e ".[dev]"`.

| Пакет | Зачем |
|---|---|
| pydantic | контракты (ToolResult, ErrorDetail) |
| openpyxl | работа с Excel |
| pyyaml · jsonschema | чтение конфигов · валидация params по схеме |
| httpx · aiohttp | HTTP-клиент/поллинг внешних MCP |
| litellm · stable-ts · torch · ffmpeg-python | провайдеры media (в разработке) |

cloudflared — бинарь (не pip), ставится `install.sh`.

## Разработка

План развития, находки и канон спек: [`docs/roadmap/`](docs/roadmap/README.md).
Предложения по улучшению функций: `docs/roadmap/spec/IMPROVEMENTS.md`.
