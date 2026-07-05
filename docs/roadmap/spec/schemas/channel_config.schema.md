# СХЕМА КОНФИГА КАНАЛА: `config/channel_config.yaml`

> Уровень: **канал**. Правила выполнения, расписание, лимиты, профиль фрагментов сцены,
> параметры рендера КОНКРЕТНОГО канала. Это **конфиг-данные** (человек правит),
> которые сервер ЧИТАЕТ при валидации. Почти всё `W`. Легенда: `W`·`F`·`enum`.
>
> Файл: `config/channel_config.yaml` — единый yaml с секциями по логическим группам.
> Заменяет монолитный `channel_config.xlsx` на структурированный yaml.
>
> Файл: `config/channel_config.yaml` — единый yaml с секциями по логическим группам.

---

## Структура файла

```
config/channel_config.yaml
├─ workflow_sequences      ← лист 1: разрешённые переходы + human-gate
├─ publishing_schedule     ← лист 2: расписание публикаций
├─ resource_limits         ← лист 3: лимиты + провайдеры + модели + media-параметры
├─ metadata_defaults       ← лист 4: шаблоны метаданных
├─ automation_rules        ← лист 5: когда ИИ сам, когда зовёт человека
├─ scene_profile           ← лист 6: профиль активных типов фрагментов сцены
└─ render_config           ← лист 7: параметры рендера канала
```

---

## Секция 1: `workflow_sequences` — разрешённые переходы + human-gate

> `allowed_next_tools` — это **allow-list + gate**, НЕ жёсткий маршрут. Отвечает
> «что РАЗРЕШЕНО дальше и где нужен человек», а не «что ДЕЛАТЬ дальше» (маршрут выбирает Claude).

```yaml
workflow_sequences:
  - sequence_id: string          # id
    trigger_tool: string         # инструмент-триггер
    allowed_next_tools: list     # СПИСОК разрешённых, не предписание порядка
    requires_human_approval: bool # TRUE → human_gate
    approval_ui_hint: string     # что показать человеку
    fallback_action: string      # если человек отклонил
```

---

## Секция 2: `publishing_schedule` — расписание публикаций

```yaml
publishing_schedule:
  - schedule_id: string          # id
    day_of_week: string          # monday/tuesday/...
    time: string                 # HH:MM
    timezone: string             # Europe/Moscow/...
    frequency: enum              # WEEKLY/BIWEEKLY
    status: enum                 # ACTIVE/PAUSED
    min_uniqueness_required: float # порог публикации
```

---

## Секция 3: `resource_limits` — лимиты, провайдеры, модели, media-параметры

> **Единый источник для всех media-инструментов** (TTS/STT/IMG). Одна строка на тип
> ресурса = провайдер + модель + параметры + лимит + fallback. Адаптер читает строку
> и получает всё для вызова. Отдельных `model_routing.yaml` / `media.ops.yaml` НЕТ.

```yaml
resource_limits:
  # ── TTS ──
  - resource_type: "tts_characters"   # id: tts_characters / stt_characters / image_generations / svg_tracing
    provider: string                  # ElevenLabs / OpenAI / Fal / Local / ...
    fallback_provider: string         # следующий провайдер при PROVIDER_FAILED ("")
    daily_limit: int                  # лимит запросов/символов в день (-1 = unlimited)
    current_usage: int                # сервер обновляет, человек сбрасывает
    warning_threshold: int            # порог → automation_rules AUTO_02
    # Media-параметры (конкретные значения для типа ресурса)
    model: string                     # модель провайдера (tts-1 / eleven_multilingual_v2 / flux-pro / ...)
    model_size: string                # для STT: large / medium / small / base
    voice: string                     # для TTS: голос (alloy / echo / Rachel / ...)
    response_format: string           # для TTS: wav / mp3 / opus / flac
    speed: float                      # для TTS: скорость речи (0.25–4.0)
    img_size: string                  # для IMG: 1024x1024 / 1920x1080 / ...
    img_n: int                        # для IMG: количество вариантов за запрос
    timeout: int                      # таймаут запроса к провайдеру (сек)
    retry_count: int                  # количество повторов при PROVIDER_FAILED до fallback
    retry_delay: int                  # задержка между повторами (сек)
    sync_mode: bool                   # TRUE = sync (результат в ответе), FALSE = async (task_id → poll → download)
```

**Как читается:**
1. Адаптер получает `resource_type` → находит строку в `resource_limits`
2. Берёт `provider`, `model`, `voice`, и т.д. — это параметры вызова
3. При `PROVIDER_FAILED` → `retry_count` повторов → переход на `fallback_provider`
4. `sync_mode` определяет: инструмент сразу отдаёт результат (sync) или task_id (async)

**Пример строк:**

```yaml
resource_limits:
  - resource_type: "tts_characters"
    provider: "ElevenLabs"
    fallback_provider: "OpenAI"
    daily_limit: 100000
    model: "eleven_multilingual_v2"
    voice: "Rachel"
    response_format: "wav"
    speed: 1.0
    timeout: 60
    retry_count: 2
    sync_mode: true

  - resource_type: "image_generations"
    provider: "Fal"
    fallback_provider: "OpenAI"
    daily_limit: 500
    model: "flux-pro-1.1"
    img_size: "1024x1024"
    img_n: 1
    timeout: 120
    retry_count: 2
    sync_mode: false

  - resource_type: "stt_characters"
    provider: "Local"
    fallback_provider: ""
    daily_limit: -1
    model: "whisper-large-v3"
    model_size: "large"
    timeout: 300
    retry_count: 2
    sync_mode: true
```

---

## Секция 4: `metadata_defaults` — шаблоны метаданных

> Переменные `{topic}`, `{niche}`, `{channel_name}` подставляются при генерации.

```yaml
metadata_defaults:
  - metadata_type: string            # id
    template_string: string          # шаблон с переменными
    variables_allowed: list          # допустимые переменные
    example_output: string           # пример результата
```

---

## Секция 5: `automation_rules` — когда ИИ сам, когда зовёт человека

> Конфиг human-gate-логики на уровне канала. Сервер читает условия и применяет;
> `severity: CRITICAL` → жёсткий блок + человек.

```yaml
automation_rules:
  - rule_id: string                  # id
    condition: string                # напр. `uniqueness_score < 0.60`
    action: string                   # блокировать/алерт/добавить шаг
    severity: enum                   # CRITICAL/HIGH/MED
```

---

## Секция 6: `scene_profile` — профиль активных типов фрагментов сцены

> Один тумблер на канал. `enabled=false` → «тихий столбец» (гасит поведение, не структуру).
> Per-video сбор идёт ТОЛЬКО из включённых здесь типов.

```yaml
scene_profile:
  - fragment_type: string            # svg_bg / svg_character / svg_component / music / sound / filter / transition
    enabled: bool                    # вкл/выкл тип на канале
    niche_weight: float              # вес типа в формуле уникальности сцены
    signal_on_reuse: bool            # слать ли сигнал переиспользования
    reuse_threshold: int             # порог переиспользования для сигнала
```

---

## Секция 7: `render_config` — параметры рендера канала

> Значения для FFmpeg. H.265/ProRes и пр. встроены в движок, тут — какие именно использовать.

```yaml
render_config:
  - param: string                    # codec / resolution / aspect_ratio / fps / crf / bitrate / container
    value: string                    # напр. h265, 1920x1080, 30
    notes: string                    # пояснение/ограничение
```

---

## РЕШЕНИЯ

1. `channel_config.yaml` — конфиг-ДАННЫЕ (правит человек), сервер их читает при валидации.
   Не путать с YAML-конфигами сервера (`firewall.yaml`/`server_reactions.yaml`): те — поведение
   движка для всех; этот — настройки одного канала.
2. `allowed_next_tools` — allow-list/gate, не маршрут. Маршрут за Claude (принцип сохранён).
3. `resource_limits` — единый источник для media-инструментов. Одна строка на тип
   ресурса = провайдер + модель + параметры + лимит + fallback + retry. Адаптер читает
   одну строку и получает всё для вызова. Отдельных `model_routing.yaml` / `media.ops.yaml` НЕТ.
4. `resource_limits[].sync_mode` — определяет поведение инструмента:
   TRUE = sync (результат в ответе, poll вырождается),
   FALSE = async (task_id → poll → download).
5. 🆕 `scene_profile` — профиль активных типов фрагментов на канал. Один флаг гасит и
   расчёт (формула уникальности пропускает тип), и сигналы (молчат по типу). Гасит
   ПОВЕДЕНИЕ, не структуру: столбцы остаются («тихий столбец»). Per-video сбор в
   `video_data.SCENES` идёт только из включённых здесь типов.
