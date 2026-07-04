# Q&A: IMG — генерация картинок (LiteLLM)

> **Роль:** облачная/локальная генерация изображений через LiteLLM.
> **Сквозное:** [G4](../global.md#g4-три-фазы-инструмента-trigger--poll--download), [G5](../global.md#g5-философия-ошибок-ловим-и-кодируем-не-чиним-отдаём-полный-текст).
> **Статус:** заглушка (NotImplementedError).
> **Навигация:** `tools/media/img_*.py` → `core/providers/img/litellm_img.py`.

## Инструменты
| Инструмент | Назначение |
|---|---|
| `trigger_image_generation` | `/image/generations` через LiteLLM |
| `poll_image_status` | статус задачи |
| `download_and_rename_image` | скачать + имя `{video_slug}_img_{scene_id}.png` + verify |

## Что генерится
Диффузия выдаёт строительные блоки: фон, персонаж, SVG-компоненты. **Одна сцена → НЕСКОЛЬКО запросов** (по запросу на исходник). Результаты в `SCENES.bg_assets`/`character_assets`/`component_assets`.

## Гипотезы
- Контент-отказ будет в ~10% случаев (ложные срабатывания)
- Переиспользование исходников сэкономит 30-50% запросов

## Сбои
Технический → `PROVIDER_FAILED`. Контент-отказ → `CONTENT_REJECTED` (частый, бывает ложным).
