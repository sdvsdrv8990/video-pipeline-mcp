# 00 — Reality Check: диск vs документация

> **Актуализировано 2026-08-14 (Сессия 23).** Все числа ниже — замер на диске (`ls`/`find`/`grep`/`git`),
> не по памяти. Первичный замер Сессии 1 сохранён в §3 как история: почти все его 🔴 закрыты, и
> старые строки противоречили коду — они перенесены в таблицу «было → стало», а не оставлены как факт.
>
> Правило файла: если строка здесь расходится с диском — прав диск, файл чинится в ту же сессию.

## 1. Состояние на S23 (замер 2026-08-14)

| Измерение | Значение | Проверка |
|---|---|---|
| Точка входа `server.py` | **465 строк** (монолит распилен, A2 закрыт) | `wc -l server.py` |
| Инструментов в `tools/list` | **67** | `tests/quick/tools_inventory.golden.json` |
| Групп инструментов | **8 непустых** (`excel`, `filesystem`, `media`, `memory`, `search`, `structure`, `tables`, `uniqueness`) | `ls tools/*/` |
| Модулей `core/` | **66** `.py` в 12 подсистемах | `find core -name '*.py'` |
| Кодов реакций | **58** (57 + `DEFAULT`), 5 классов | `config/server_reactions.yaml` |
| Деклараций `config/` | 7 YAML + `config/templates/` | `ls config/` |
| Тестов в git | **31 файл** | `git ls-files tests` |
| Коммитов | 144 | `git rev-list --count HEAD` |
| CI | `.github/workflows/ci.yml` — ruff, mypy, pytest, bandit, gitleaks (хард), pip-audit (advisory) | файл на диске |
| Packaging | `pyproject.toml` + `requirements.lock` (пины), extra `dev`, extra `gpu-amd` | файл на диске |
| Локальные модели | 6 установлено, подъём **на видеокарте** (ROCm, без root) | память `hardware-probe-without-root` |

### Что реально работает (проверено прогоном, не декларацией)

- **`core/firewall/`** — модульный императивный пайплайн (IP-blocklist → rate-limit → injection → anomaly).
- **`core/contracts/`** — Pydantic-контракты (`ToolResult`, `ErrorDetail`, `Fact`, `TaskStatus`).
- **`core/tables/` + `core/excel/`** — логика таблиц/книг; конфиг канала живёт **листами книги**, не YAML.
- **`core/providers/`** — реестр адаптеров, опись моделей, проба железа, подъём локальных моделей на GPU.
- **Периметр и служебное:** `core/search/`, `core/uniqueness/`, `core/ids/`, `core/state/`, `core/paths.py`, `core/secrets.py`, `core/write_policy.py`, `core/integrity.py`, `core/auth.py`, `core/advice.py`.
- **`core/transport/` + `tunnel.py`** — cloudflared-туннель к Claude AI Web.

## 2. Что осталось незакрытым (актуальные разрывы)

| Разрыв | Реальность на диске | Тип |
|---|---|---|
| **Видео-пайплайн** — суть названия проекта | `tools/video/` и `pipeline/` — **пустые каталоги** (в git их нет вовсе); оркестрации рендера нет | 🔴 продукт: сервер сегодня = данные + медиа-генерация, но не сборка видео |
| **Облачные провайдеры** | 12 честных `NotImplementedError` в `ffmpeg`, `litellm_img`, `litellm_tts`, `stable_ts` — стабы КРИЧАТ (G16), но функции нет | 🟠 |
| **Декларативный ops-слой** | `config/ops/` пуст (0 файлов), `core/engine` generic и почти не зовётся; логика инструментов живёт в `tools/<группа>/` | ℹ️ **осознанно**: ops/`model_routing` упразднены (см. `05_data_template_media_system.md` §0). Пока реестра нет — «толстая обёртка» здесь норма; дефект качества — только **недокументированное** дублирование между группами |
| **`tools/excel_engine/`** | пустой каталог-реликт (логика в `core/excel/`) | 🟡 снести при следующем касании |
| **Observability** | structlog / metrics / health / tracing нет | 🟠 |
| **Аудит `D#`/`G#` как артефакт** | `docs/dev/audit/` удалён владельцем; словарь находок живёт в `02_findings.md` (`F#`/`D#`/`G#`), история — в git | ℹ️ не разрыв, а смена носителя |

## 3. История: что нашла Сессия 1 и что с этим стало

Замер 2026-07-05 фиксировал ~4780 LOC ядра, 52 инструмента и семь 🔴. Итог на S23:

| Утверждение S1 | Статус на S23 |
|---|---|
| `server.py` = 1521 строка, монолит; `tools/` подпапки пусты | ✅ закрыто (A2): 465 строк, 8 групп |
| `tests/` в `.gitignore` — тесты не в git | ✅ закрыто (I1): 31 файл в git; игнорится только `.pytest_cache`/`__pycache__`/`.coverage` |
| `docs/dev/` в `.gitignore` | ✅ снято: `docs/dev/` удалён, документация = `docs/roadmap/` в git |
| Нет `.github/workflows/` | ✅ закрыто (I3): CI с 5 хард-гейтами |
| Нет `pyproject.toml`, зависимости не запинены | ✅ закрыто: `pyproject.toml` + `requirements.lock` |
| Нет `mypy`/`ruff` | ✅ закрыто: оба в `[dev]` и в CI |
| `scripts/` пусто | ✅ закрыто: `config_to_schema.py`, `spec_to_schema.py`, `models.py`, `set_provider_key.py` |
| Провайдеры = сплошной `NotImplementedError` | 🟠 частично: локальные подняты и измерены, облачные — честные стабы |
| App-level auth отсутствует | ✅ есть `core/auth.py` (токен + отпечаток); за туннелем один клиент, IP-гранулярность по-прежнему бесполезна (G18) |
| `config/ops/` пуст → «ops-слой не существует» | ℹ️ переквалифицировано: слой **упразднён намеренно**, не сломан |

## 4. Следствие для roadmap

Три оси из S1 остаются, но веса сместились:

1. **Продукт (P)** — 🔴 главный незакрытый долг: видео-пайплайн как таковой (`pipeline/`, `tools/video/`).
2. **Архитектура (A)** — 🟢 в основном закрыта: распил монолита, группы, контракты, реакции, провайдеры.
3. **Инфра (I)** — 🟢 закрыта по обвязке (CI, packaging, пины, линт, тесты в git); осталась observability.

Детали и статус пунктов — `01_master_roadmap.md`, ход работ по сессиям — `_sessions.md`,
словарь находок — `02_findings.md`, навигация по всем документам — `README.md`.
