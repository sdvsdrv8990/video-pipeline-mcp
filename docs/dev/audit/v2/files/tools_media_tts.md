# Q&A: TTS — озвучка (LiteLLM)

> **Роль:** облачный/локальный TTS через LiteLLM.
> **Сквозное:** [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download), [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст).
> **Статус:** заглушка.
> **Навигация:** `tools/media/tts_*.py` → `core/providers/tts/litellm_tts.py`.

## Инструменты
| Инструмент | Назначение |
|---|---|
| `prepare_tts_input` | подготовить текст (разбивка, SSML/паузы) |
| `trigger_tts_generation` | `/audio/speech` через LiteLLM |
| `poll_tts_status` | статус задачи |
| `download_and_rename_audio` | скачать + имя `{video_slug}_tts_{scene_id}.wav` + verify |

## Вход/выход
**Вход:** JSON-запрос (`{model, input, voice, response_format, speed, ...}`).
**Выход:** аудио, которое потом идёт в STT за таймкодами.

## Порядок в воркфлоу
Сначала TTS, потом транскрибация озвучки (STT).

## Гипотезы
- Большинство TTS будут sync (poll вырождается)
- Неверный API URL → все TTS операции сломаются

## Сбои
Технический → `PROVIDER_FAILED` (retry + цепочка). Контент-отказ → `CONTENT_REJECTED` (редко для TTS).
