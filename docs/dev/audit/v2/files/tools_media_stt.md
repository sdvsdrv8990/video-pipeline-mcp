# Q&A: STT — транскрибация (stable-ts, локально)

> **Роль:** локальный движок транскрибации. Whisper/faster-whisper на нашей машине.
> **Сквозное:** [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download), [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст).
> **Статус:** заглушка.
> **Навигация:** `tools/media/stt_*.py` → `core/providers/stt/stable_ts_adapter.py`.

## Инструменты
| Инструмент | Назначение |
|---|---|
| `trigger_transcription` | запустить stable-ts на аудио |
| `poll_transcription_status` | прогресс локального джоба |
| `parse_timestamps_and_silence` | пословные таймкоды + границы тишины (VAD) |

## Вход/выход
**Вход:** аудио-файл озвучки (`{video_slug}_tts_*.wav`).
**Выход:** JSON с пословными таймкодами и границами тишины → листы SCENES и TRANSCRIPT.

## Особенности
- Локальный STT обычно синхронный (один вызов)
- `suppress_silence=True`, `word_timestamps=True`, `vad=True`
- Карта тишины — ВЫХОД STT, не вход TTS

## Гипотезы
- Пословные таймкоды точнее, чем посекундные
- Нет GPU → fallback на CPU (медленно)
- Модель не загружается → все STT операции сломаются

## Сбой → `LOCAL_INFERENCE_FAILED`
Не PROVIDER_FAILED: это ресурс/среда. Fallback: large→medium→base, не помогло → человек.
