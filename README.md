# Video Pipeline MCP Server

MCP-сервер для управления видеопайплайном. Работает с Claude AI Web через туннель.

> **Статус (2026-07):** реализован слой управления данными (файлы, таблицы/Excel, структура каналов,
> поиск) + файрвол + транспорт. Продуктовый видео-пайплайн (озвучка/транскрибация/картинки/монтаж) —
> в разработке. План доведения до корпоративного уровня: [`docs/roadmap/`](docs/roadmap/README.md).
> Легенда ниже: ✅ реализовано · 🟠 заглушка/частично · 🔲 план.

## Структура проекта (фактическая)

```
video_pipeline_mcp/
├── server.py              ✅ точка входа: MCP-эндпоинт + туннель; регистрация инструментов
├── pyproject.toml         ✅ сборка/зависимости (PEP 621) — единственный файл зависимостей
├── install.sh · run.sh    ✅ установка (.venv + бинарь туннеля) · запуск (--tunnel)
│
├── config/                ✅ декларации (поведение, НЕ код)
│   │                          (конфиг канала с S22 — не файл, а 7 листов channel_data.xlsx:
│   │                           RESOURCE_LIMITS, WORKFLOW_SEQUENCES, PUBLISHING_SCHEDULE,
│   │                           METADATA_DEFAULTS, AUTOMATION_RULES, SCENE_PROFILE, RENDER_CONFIG)
│   ├── server_reactions.yaml  ✅ реестр реакций (коды → класс + recovery)
│   ├── firewall.yaml          ✅ правила файрвола
│   ├── tunnel.yaml            ✅ конфиг туннеля (gitignored — машинный; токен — в среде, MCP_TUNNEL_TOKEN)
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
│   ├── runner/            ✅ инференс в отдельном процессе: сервис на петле + супервизор
│   ├── paths.py           ✅ containment путей в workspace/
│   └── providers/         🟠 адаптеры провайдеров (заглушки — в разработке)
│       ├── stt/ (stable-ts) · tts/ (LiteLLM) · img/ (LiteLLM) · ffmpeg/ (внешний MCP)
│
├── tools/                 🔲 тонкие обёртки Bounded Context (план: вынос из server.py, A2)
├── pipeline/              🔲 оркестрация процессов (entry_points + steps) — план
├── scripts/               🔲 утилиты (план: introspect_tables.py — A1′)
├── docker/                ✅ образ раннера (среда инференса заперта в контейнере)
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
./install.sh                        # роль машины, .venv + зависимости (pyproject) + бинарь туннеля
python3 scripts/installer_tui.py    # сменить роль без переустановки (Local — сервер и туннель на этой машине)
```

## Запуск

```bash
./run.sh              # по роли установки: Local — сервер + туннель, Console — сервер только для этой машины
./run.sh --stop       # остановить сервер и туннель
```

## Ключ доступа

Коннектор Claude AI Web шлёт ключ заголовком `x-api-key` (значение — сам ключ). Сервер хранит в `.env` только его
хэш, поэтому показать ключ повторно нельзя — только проверить или выпустить новый.

```bash
.venv/bin/python server.py --check-key   # проверить ключ: ввод без эха, ответ «верный / не тот» по отпечатку, сам ключ не печатается
.venv/bin/python server.py --rotate-key  # выпустить новый ключ взамен старого
```

После `--rotate-key` новый ключ печатается один раз — вставь его в коннектор и перезапусти сервер (`./run.sh --stop`,
затем `./run.sh`): запущенный сервер читает ключ на старте и до перезапуска пускает по старому. `--check-key` сверяет с
ключом в `.env` (или в `MCP_AUTH_TOKEN`, если он задан), а не с тем, что держит запущенный сервер.

## Зависимости

Единственный источник — `pyproject.toml` (`pip install -e .`);
dev-инструменты — `pip install -e ".[dev]"` (агрегат зон `test`/`scripts`/линт). CI и гейты ставят по замку версий
`requirements-lock.txt` (`pip install -c requirements-lock.txt -e ".[dev]"`): пересборка — команда в его шапке, мажоры
двигает dependabot.

| Пакет | Зачем |
|---|---|
| pydantic | контракты (ToolResult, ErrorDetail) |
| openpyxl | работа с Excel |
| pyyaml · jsonschema | чтение конфигов · валидация params по схеме |
| httpx · aiohttp | HTTP-клиент/поллинг внешних MCP |
| litellm · stable-ts · torch · ffmpeg-python | провайдеры media (в разработке) |

Бинарь туннеля — не pip-пакет: ставится `install.sh` в путь каталога (`config/tunnel_diagnostics.yaml`, `бинари`).

## Разработка

```bash
pip install -e ".[dev]"          # линт, типы, тесты, bandit, pip-audit
./scripts/install_dev_tools.sh   # gitleaks в .venv/bin — бинарь, в PyPI его нет
```

Второй шаг нужен только для гейта: серверу `gitleaks` не требуется, поэтому в `install.sh` его нет.
Версию скрипт берёт из `.github/workflows/ci.yml`, чтобы локально гонялся ровно тот бинарь, что и в CI.

Гейт — восемь джоб `ci.yml`, и «зелёно» считается по exit-коду каждой, а не по одному `pytest`:
`lint` · `studio` · `studio-app` · `test` · `conformance` · `security` · `comment-guard` · `gitleaks`.
Локально гоняются все восемь.
`pip-audit` — единственная проверка, которой нужна сеть: он ходит в PyPI за advisory.

План развития, находки и канон спек: [`docs/roadmap/`](docs/roadmap/README.md).
Предложения по улучшению функций: `docs/roadmap/spec/IMPROVEMENTS.md`.
