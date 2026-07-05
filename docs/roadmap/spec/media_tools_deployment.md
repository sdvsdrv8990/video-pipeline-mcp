# План развертывания: транскрибация, озвучка, генерация изображений

> **Источники:** ИНСТРУКЦИЯ_media_инструменты.md (полномочия), channel_config.yaml
> (RESOURCE_LIMITS), workflow.md (процесс), core/providers/ (заглушки), server.py (реестр).
>
> **Статус:** ДО РЕАЛИЗАЦИИ — это план. Заглушки в `core/providers/` содержат сигнатуры
> методов и контракты. Реализация = заполнение TODO по приоритету.

---

## 0. Архитектурная карта: что уже есть, чего нет

```
УРОВЕНЬ 1 — Инструменты (Claude дёргает)
  tools/media/                    ← НЕТ (нужно создать)
    ├─ stt_*                       ← 3 инструмента
    ├─ tts_*                       ← 4 инструмента
    └─ img_*                       ← 3 инструмента

УРОВЕНЬ 2 — Адаптеры провайдеров (сигнатуры есть, TODO внутри)
  core/providers/stt/stable_ts_adapter.py  ← заглушка
  core/providers/tts/litellm_tts.py        ← заглушка
  core/providers/img/litellm_img.py        ← заглушка

УРОВЕНЬ 3 — Конфигурация (ЕДИНЫЙ ИСТОЧНИК)
  config/channel_config.yaml
    ├─ resource_limits            ← провайдер + модель + голос + лимиты + fallback + sync_mode
    │   (расширен: model, model_size, voice, response_format, speed,
    │    img_size, img_n, timeout, retry_count, retry_delay, sync_mode)
    ├─ workflow_sequences         ← разрешённые переходы
    ├─ scene_profile              ← активные типы фрагментов
    └─ render_config              ← параметры рендера
    (остальные секции — publishing_schedule, metadata_defaults, automation_rules)

УРОВЕНЬ 4 — Реакции
  server_reactions.yaml           ← PROVIDER_FAILED / CONTENT_REJECTED / LOCAL_INFERENCE_FAILED
```

**Принцип: один конфиг = один источник правды.** `RESOURCE_LIMITS` хранит ВСЁ:
провайдер, модель, параметры, лимиты, fallback, retry. Адаптер читает одну строку
и получает всё для вызова. Отдельных `model_routing.yaml` и `media.ops.yaml` НЕТ.

---

## 1. Полный список инструментов

### 1.1. STT — транскрибация (локально, stable-ts) · Воркфлоу Шаг 7

| # | Инструмент | Назначение | Вход | Выход | Режим |
|---|---|---|---|---|---|
| S1 | `trigger_transcription` | Запустить stable-ts на аудио | `{audio_path, model_size?, word_timestamps?, suppress_silence?, vad?}` | `ToolResult{segments, silence_map, duration_sec}` | sync/async |
| S2 | `poll_transcription_status` | Прогресс локального джоба | `{task_id}` | `TaskStatus{status, progress}` | sync (вырождается если sync-провайдер) |
| S3 | `parse_timestamps_and_silence` | Извлечь пословные таймкоды + границы тишины | `{raw_segments}` | `ToolResult{timestamp_start, timestamp_end, word_count, pace_wpm, silence_map}` | sync |

**Цепочка:** `S1 → S2 (опционально) → S3`
**Сбой:** `LOCAL_INFERENCE_FAILED` (не PROVIDER_FAILED) → деградация large→medium→base
**Связь с данными:** результат → лист `SCENES` (`timestamp_start/end`) + `TRANSCRIPT` по `scene_id`

### 1.2. TTS — озвучка (LiteLLM) · Воркфлоу Шаг 6

| # | Инструмент | Назначение | Вход | Выход | Режим |
|---|---|---|---|---|---|
| T1 | `prepare_tts_input` | Подготовить текст (разбивка, SSML/паузы) из сценария | `{scenes: [{scene_id, text, pause_before?, pause_after?, speed?}]}` | `ToolResult{prepared_inputs: [{scene_id, model, input, voice, response_format}]}` | sync |
| T2 | `trigger_tts_generation` | `/audio/speech` через LiteLLM | `{model, input, voice, response_format?, speed?}` | `ToolResult{task_id}` или `ToolResult{file_path}` | sync/async |
| T3 | `poll_tts_status` | Статус задачи | `{task_id}` | `TaskStatus{status, progress}` | sync (вырождается если sync-провайдер) |
| T4 | `download_and_rename_audio` | Скачать + `{video_slug}_tts_{scene_id}.wav` + verify | `{task_id, video_slug, scene_id}` | `ToolResult{file_path, verified, duration_sec}` | sync |

**Цепочка:** `T1 → T2 → T3 (опционально) → T4`
**Сбои:** `PROVIDER_FAILED` (технический) / `CONTENT_REJECTED` (редко для TTS)
**Порядок:** TTS (Шаг 6) → STT (Шаг 7)

### 1.3. IMG — генерация изображений (LiteLLM) · Воркфлоу Шаг 9

| # | Инструмент | Назначение | Вход | Выход | Режим |
|---|---|---|---|---|---|
| I1 | `trigger_image_generation` | `/image/generations` через LiteLLM | `{model, prompt, size?, n?}` | `ToolResult{task_id}` или `ToolResult{image_paths}` | sync/async |
| I2 | `poll_image_status` | Статус задачи | `{task_id}` | `TaskStatus{status, progress}` | sync (вырождается если sync-провайдер) |
| I3 | `download_and_rename_image` | Скачать + `{video_slug}_img_{scene_id}.png` + verify | `{task_id, video_slug, scene_id}` | `ToolResult{file_path, verified}` | sync |

**Цепочка:** `I1 → I2 (опционально) → I3`
**Сбои:** `PROVIDER_FAILED` / `CONTENT_REJECTED` (часто ложный для картинок)
**Важно:** одна сцена → НЕСКОЛЬКО запросов `I1` (фон, персонаж, компоненты)

---

## 2. Соответствие инструментов и адаптеров

| Инструмент | Адаптер | Метод адаптера |
|---|---|---|
| `trigger_transcription` | `StableTSAdapter` | `trigger_transcription()` |
| `poll_transcription_status` | `StableTSAdapter` | *(через progress_callback)* |
| `parse_timestamps_and_silence` | `StableTSAdapter` | `parse_timestamps()` |
| `prepare_tts_input` | — (логика инструмента, не адаптера) |
| `trigger_tts_generation` | `LiteLLMTTSAdapter` | `trigger_generation()` |
| `poll_tts_status` | `LiteLLMTTSAdapter` | `poll_status()` |
| `download_and_rename_audio` | `LiteLLMTTSAdapter` | `download_audio()` |
| `trigger_image_generation` | `LiteLLMIMGAdapter` | `trigger_generation()` |
| `poll_image_status` | `LiteLLMIMGAdapter` | `poll_status()` |
| `download_and_rename_image` | `LiteLLMIMGAdapter` | `download_image()` |

---

## 3. Размещение файлов

```
video_pipeline_mcp/
├─ tools/
│  └─ media/                          ← НОВЫЕ ФАЙЛЫ
│     ├─ __init__.py
│     ├─ stt_tools.py                 ← trigger_transcription, poll_transcription_status,
│     │                                  parse_timestamps_and_silence
│     ├─ tts_tools.py                 ← prepare_tts_input, trigger_tts_generation,
│     │                                  poll_tts_status, download_and_rename_audio
│     └─ img_tools.py                 ← trigger_image_generation, poll_image_status,
│                                        download_and_rename_image
│
├─ core/providers/                    ← УЖЕ ЕСТЬ (заглушки)
│  ├─ stt/stable_ts_adapter.py        ← реализовать TODO
│  ├─ tts/litellm_tts.py              ← реализовать TODO
│  └─ img/litellm_img.py              ← реализовать TODO
│
├─ config/
│  ├─ channel_config.yaml             ← ЕДИНЫЙ ИСТОЧНИК (resource_limits расширен)
│  ├─ server_reactions.yaml           ← УЖЕ ЕСТЬ
│  ├─ firewall.yaml                   ← УЖЕ ЕСТЬ
│  └─ tunnel.yaml                     ← УЖЕ ЕСТЬ
│
├─ docs/dev/                          ← ЭТОТ ФАЙЛ
│  └─ media_tools_deployment.md
│
└─ server.py                          ← реестрация инструментов (engine.register)
```

**Чего НЕТ и не нужно:**
- `channel_config.xlsx` — заменён на `config/channel_config.yaml`
- `config/ops/media.ops.yaml` — реестр операций встроен в `resource_limits`
- `config/model_routing.yaml` — fallback-цепочки в `resource_limits[].fallback_provider`

---

## 4. Порядок реализации (приоритет)

### Фаза 1: Минимальный жизнеспособный цикл

**Цель:** TTS → STT → таймкоды (озвучка + транскрибация работают)

| Шаг | Что делаем | Файлы | Блокеры |
|---|---|---|---|
| 1.1 | Заполнить `prepare_tts_input` (логика инструмента) | `tools/media/tts_tools.py` | нет |
| 1.2 | Заполнить `trigger_tts_generation` (LiteLLM API) | `core/providers/tts/litellm_tts.py` | нет |
| 1.3 | Заполнить `download_and_rename_audio` + verify | `core/providers/tts/litellm_tts.py` | нет |
| 1.4 | Заполнить `trigger_transcription` (stable-ts) | `core/providers/stt/stable_ts_adapter.py` | FFmpeg, PyTorch, GPU |
| 1.5 | Заполнить `parse_timestamps_and_silence` | `core/providers/stt/stable_ts_adapter.py` | нет |
| 1.6 | Зарегистрировать TTS + STT инструменты в `engine.register()` | `server.py` | нет |
| 1.7 | Заполнить `resource_limits` в `channel_config.yaml` (TTS + STT строки) | `config/channel_config.yaml` | нет |
| 1.8 | Протестировать end-to-end: текст → аудио → таймкоды | `tests/` | нет |

### Фаза 2: Генерация изображений

| Шаг | Что делаем | Файлы | Блокеры |
|---|---|---|---|
| 2.1 | Заполнить `trigger_image_generation` (LiteLLM API) | `core/providers/img/litellm_img.py` | нет |
| 2.2 | Заполнить `download_and_rename_image` + verify | `core/providers/img/litellm_img.py` | нет |
| 2.3 | Зарегистрировать IMG инструменты в `engine.register()` | `server.py` | нет |
| 2.4 | Заполнить `resource_limits` в `channel_config.yaml` (IMG строка) | `config/channel_config.yaml` | нет |
| 2.5 | Протестировать: промпт → изображение → verify | `tests/` | нет |

### Фаза 3: Error handling + fallback

| Шаг | Что делаем | Файлы | Блокеры |
|---|---|---|---|
| 3.1 | Полная обработка ошибок в адаптерах (3 природы) | `core/providers/*/` | нет |
| 3.2 | Fallback-цепочки через `resource_limits[].fallback_provider` | `config/channel_config.yaml` | нет |
| 3.3 | Retry-логика: `retry_count` + `retry_delay` перед fallback | `core/providers/*/` | нет |
| 3.4 | Проверка контент-отказа + ручное вмешательство | `tools/media/` | нет |
| 3.5 | Аудит-трейл: запись всех media-операций | `tools/media/` | нет |

---

## 5. Контракты входов/выходов (детально)

### 5.1. STT-инструменты

```python
# trigger_transcription
input_schema = {
    "audio_path": str,          # путь к WAV файлу ({video_slug}_tts_{scene_id}.wav)
    "model_size": str = None,   # large/medium/base — ЕСЛИ None, берётся из RESOURCE_LIMITS
    "word_timestamps": bool = True,
    "suppress_silence": bool = True,
    "vad": bool = True
}
# model_size по умолчанию = RESOURCE_LIMITS[stt_characters].model_size

# parse_timestamps_and_silence
input_schema = {
    "raw_segments": list       # от stable-ts: [{start, end, text, words: [{word, start, end}]}]
}

output_schema = {
    "segments": list,           # обработанные сегменты
    "silence_map": list,        # [{start, end, duration}]
    "word_count": int,
    "duration_sec": float,
    "pace_wpm": float           # слов в минуту
}
```

### 5.2. TTS-инструменты

```python
# prepare_tts_input
input_schema = {
    "scenes": list,             # [{scene_id, text, pause_before?, pause_after?, speed?}]
    "voice": str = None,        # ЕСЛИ None, берётся из RESOURCE_LIMITS
    "default_model": str = None # ЕСЛИ None, берётся из RESOURCE_LIMITS
}
# voice/model/response_format/speed по умолчанию = RESOURCE_LIMITS[tts_characters].*

output_schema = {
    "prepared_inputs": list     # [{scene_id, model, input, voice, response_format}]
}

# trigger_tts_generation
input_schema = {
    "model": str = None,        # из RESOURCE_LIMITS если None
    "input": str,               # текст для озвучки
    "voice": str = None,        # из RESOURCE_LIMITS если None
    "response_format": str = None, # из RESOURCE_LIMITS если None
    "speed": float = None       # из RESOURCE_LIMITS если None
}

# download_and_rename_audio
input_schema = {
    "task_id": str,
    "video_slug": str,
    "scene_id": str
}

output_schema = {
    "file_path": str,           # assets/audio/{video_slug}_tts_{scene_id}.wav
    "verified": bool,
    "duration_sec": float
}
```

### 5.3. IMG-инструменты

```python
# trigger_image_generation
input_schema = {
    "model": str = None,        # из RESOURCE_LIMITS если None
    "prompt": str,              # промпт на ОДИН исходник
    "size": str = None,         # из RESOURCE_LIMITS.img_size если None
    "n": int = None             # из RESOURCE_LIMITS.img_n если None
}

# download_and_rename_image
input_schema = {
    "task_id": str,
    "video_slug": str,
    "scene_id": str
}

output_schema = {
    "file_path": str,           # assets/img/{video_slug}_img_{scene_id}.png
    "verified": bool
}
```

**Правило:** Все параметры модели/голоса/формата — из `RESOURCE_LIMITS`. Инструмент
может переопределить, но дефолты берутся из конфига канала. Человек правит Excel,
не код.

---

## 6. Три природы сбоев → маппинг

| Код | Природа | Кто чинит | Retry? | Где обрабатывается |
|---|---|---|---|---|
| `LOCAL_INFERENCE_FAILED` | ресурс/среда | среда | модель↓ | `StableTSAdapter._map_error()` |
| `PROVIDER_FAILED` | инфраструктура | сервер | retry + fallback | `LiteLLMTTSAdapter._map_error()`, `LiteLLMIMGAdapter._map_error()` |
| `CONTENT_REJECTED` | модерация | Claude | **нет** (реформулировка) | оба LiteLLM-адаптера |

**Верификация файла — ДОГМА 3:** после download проверять что файл ≠ 0 байт и валиден
(аудио играбельно, изображение открывается). Пустой файл = `LOCAL_INFERENCE_FAILED` или
`PROVIDER_FAILED`, не успех.

---

## 7. Именование ассетов

| Тип | Шаблон | Пример |
|---|---|---|
| Аудио (TTS) | `{video_slug}_tts_{scene_id}.wav` | `crypto_bull_tts_scene01.wav` |
| Изображение (IMG) | `{video_slug}_img_{scene_id}.png` | `crypto_bull_img_scene01_fond.png` |

Путь: `assets/audio/` для TTS, `assets/img/` для IMG.

---

## 8. Зависимости и инфраструктура

| Зависимость | Где используется | Статус |
|---|---|---|
| FFmpeg | stable-ts, WAV-обработка | нужна установка |
| PyTorch (GPU) | stable-ts | желателен GPU |
| stable-ts | STT-адаптер | репозиторий архивирован, рабочий |
| LiteLLM | TTS + IMG адаптеры | нужен API ключ + URL |
| Python httpx/requests | LiteLLM вызовы | в зависимостях |

---

## 9. Чеклист перед боем

- [ ] `config/channel_config.yaml` — секция `resource_limits` для TTS, STT, IMG заполнена
- [ ] `core/providers/tts/litellm_tts.py` — TODO заполнены, читает из `resource_limits`
- [ ] `core/providers/stt/stable_ts_adapter.py` — TODO заполнены, читает из `resource_limits`
- [ ] `core/providers/img/litellm_img.py` — TODO заполнены, читает из `resource_limits`
- [ ] `tools/media/tts_tools.py` — инструменты зарегистрированы
- [ ] `tools/media/stt_tools.py` — инструменты зарегистрированы
- [ ] `tools/media/img_tools.py` — инструменты зарегистрированы
- [ ] `server.py` — `engine.register()` для всех media-инструментов
- [ ] `tests/` — happy path + error path + containment для каждого инструмента
- [ ] stable-ts + FFmpeg + PyTorch установлены
- [ ] `docs/dev/history_tools_media.md` — история файлов создана

---

## 10. Доноры: паттерны для адаптации

> **Важно:** НОВЫЕ ПРОВАЙДЕРЫ НЕ ДОБАВЛЯЮТСЯ. Доноры — только для паттернов.

### 10.1. modelcontextprotocol/scores (MCP-серверы)
- **Паттерн:** path-validation через `safe_resolve` → `is_relative_to(root)`
- **Где применить:** `tools/media/` — валидация путей к ассетам
- **Как:** проверять что `audio_path` внутри `assets/audio/`, `image_path` внутри `assets/img/`

### 10.2. PrefectHQ/fastmcp (MCP-фреймворк)
- **Паттерн:** ToolResult как единый контракт возврата
- **Где применить:** все инструменты — `ToolResult{status, data, error, facts}`
- **Как:** у нас уже есть `core/contracts/` — следовать ему строго

### 10.3. langgenius/dify (Agentic workflows)
- **Паттерн:** fallback-цепочки с retry + human-gate
- **Где применить:** PROVIDER_FAILED → retry (`retry_count`) → fallback (`fallback_provider`) → CONTENT_REJECTED → Claude
- **Как:** `RESOURCE_LIMITS.fallback_provider` определяет цепочку, `server_reactions.yaml` — реакции

### 10.4. modelcontextprotocol/python-sdk (контракты)
- **Паттерн:** input_schema как JSON Schema + валидация на входе
- **Где применить:** все инструменты — валидация `input_schema` до вызова адаптера
- **Как:** Pydantic модели для каждого инструмента

---

## 11. Write-back

При реализации каждого инструмента обновить:
1. `docs/dev/history_tools_media.md` — история файлов для `tools/media/`
2. `docs/dev/history_core_providers_tts.md` — история для TTS-адаптера
3. `docs/dev/history_core_providers_stt.md` — история для STT-адаптера
4. `docs/dev/history_core_providers_img.md` — история для IMG-адаптера

**Проход не считается закрытым, пока не обновлён write-back.**
