# Video Pipeline MCP Server

MCP-сервер для управления видеопайплайном. Работает с Claude AI Web через туннель.

## Структура проекта

```
video_pipeline_mcp/
├── server.py              — точка входа: MCP-эндпоинт + туннель к Claude
├── install.sh             — установка (.venv + зависимости)
├── run.sh                 — запуск сервера
├── requirements.txt       — зависимости Python
├── .gitignore             — что не коммитить
│
├── config/                — декларации (поведение, НЕ код)
│   ├── paths.yaml         — путь к workspace, лимиты
│   ├── model_routing.yaml — провайдеры, fallback-цепочки
│   ├── server_reactions.yaml — реестр реакций (5 классов, 13 кодов)
│   ├── ops/               — реестры операций по категории
│   │   ├── filesystem.ops.yaml
│   │   ├── tables.ops.yaml
│   │   ├── excel.ops.yaml
│   │   └── media.ops.yaml
│   └── templates/         — шаблоны структуры и таблиц
│
├── core/                  — ядро (бизнес-логика)
│   ├── contracts/         — Pydantic: ToolResult, ErrorDetail, Fact, TaskStatus
│   ├── engine/            — generic-движок деклараций
│   ├── state/             — read.json / write.json / session log
│   ├── reactions/         — читалка server_reactions
│   ├── ids/               — генерация ID, реестр связей
│   ├── transport/         — туннель к Claude
│   └── providers/         — адаптеры провайдеров
│       ├── stt/           — stable-ts (локально)
│       ├── tts/           — LiteLLM (облако)
│       ├── img/           — LiteLLM (облако)
│       └── ffmpeg/        — внешний MCP-сервер (GitHub)
│
├── tools/                 — тонкие обёртки (Bounded Context)
│   ├── filesystem/        — fs_* инструменты
│   ├── tables/            — table_* примитивы + json_* очередь
│   ├── excel_engine/      — excel_* (структура книг)
│   ├── media/             — tts_*, stt_*, img_*
│   └── video/             — монтаж сцен
│
├── pipeline/              — бизнес-логика процессов
│   ├── entry_points/      — 4 точки входа
│   └── steps/             — единая библиотека шагов
│
├── scripts/               — утилиты обслуживания
├── tests/                 — тесты
└── docs/                  — документация
    ├── dev/               — для разработки
    └── github/            — публичная
```

## Установка

```bash
./install.sh
```

Установит:
- Виртуальное окружение `.venv`
- Python-зависимости из `requirements.txt`
- Проверит наличие FFmpeg в системе

## Запуск

```bash
./run.sh
```

## Зависимости

| Пакет | Зачем |
|---|---|
| pydantic | Контракты (ToolResult, ErrorDetail) |
| openpyxl | Работа с Excel |
| litellm | TTS/IMG провайдеры |
| stable-ts | STT (локально) |
| torch | Для stable-ts |
| ffmpeg-python | Обёртка для FFmpeg |
| httpx | HTTP-клиент для внешних MCP |
| aiohttp | Async HTTP (поллинг) |
| pyyaml | Чтение конфигов |
# video-pipeline-mcp
