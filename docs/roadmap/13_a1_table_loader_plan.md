# A1′ — план табличного loader (`table_materializer`) — готов к исполнению

> Статус: **ЗАПЛАНИРОВАНО** (перенесено из локальной памяти в git, session 16). Вторая крупная
> запланированная-но-не-начатая задача (первая — A2, `12_a2_split_plan.md`). Продукт-ядро: без loader
> табличный слой незрел (F20 🔨 / F30 🟠). Формат + proof готовы; строим loader + фазу + схемы.

## Что строим
`core/engine/table_materializer.py` — по `tables_pending` (их откладывает `structure_create`,
`template_engine.py:155`) грузит `config/templates/tables/*.schema.yaml` и материализует .xlsx-книгу
через `core/excel` (`create_workbook`/`add_sheet`/`add_column(formula=)`/`set_validation`). Подключить
как отдельную **фазу ТАБЛИЦЫ** после `structure_create` (НЕ ломать structure 35/35).

## Готово (git-tracked)
- Формат схемы: `docs/roadmap/spec/TABLE_SCHEMA_FORMAT.md` (мост `spec.schema.md` → YAML → `core/excel`;
  флаги id/W(ritable)/F(ormula)/fk, тип `enum` → `set_validation`).
- Proof-схема: `config/templates/tables/network_config.schema.yaml` (валидна).
- Движок структуры `core/excel` — CRUD формы книги (S16: `validate_formulas` уже считает формулы через LO).

## Незрело / строить (findings)
- **F20 🔨** — самого loader (`table_materializer`) + фазы ТАБЛИЦЫ нет; книги висят в `tables_pending`.
- **F30 🟠 (устойчивость формул к неполным данным)** — требование владельца: при отсутствии части данных
  (нет/часть ассетов, нет конкурентов, частичные данные канала) формулы НЕ ломаются, а деградируют
  (пусто/PENDING/0), не `#DIV/0!`/`#REF!`. Дизайн в спеке ЕСТЬ (`channel_config.schema.md §6`):
  `scene_profile.enabled=false` → «тихий столбец» (формула пропускает тип), `niche_weight`,
  `automation_rules.condition` (пороги), `signal_on_reuse`/`reuse_threshold`, вариации `AST_x`.
  **Обязано быть ДЕКЛАРАТИВНЫМ** (флаги конфига), НЕ `if type missing` в коде (anti-hardcode).
- **F28** (смежное) — `delete_column`/`move_column` = сырой openpyxl `delete_cols`/сдвиг, ломает формулы
  молча (нет пересчёта зависимостей). Тот же корень «нет модели зависимостей формул».

## Порядок исполнения (инкрементально, baseline зелёный)
1. **Loader на proof** — `table_materializer` материализует `network_config.schema.yaml` e2e (create→sheets→
   columns→formula→validation). Новый тест. Коммит.
2. **Фаза ТАБЛИЦЫ** — подключить loader отдельным вызовом после `structure_create` (по `tables_pending`);
   structure 35/35 держится. Коммит.
3. **Деградация неполных данных (F30)** — декларативный механизм (флаги из спеки: `scene_profile.enabled`,
   `niche_weight`, `automation_rules.condition`, `signal_on_reuse`); тесты матрицы E-F (устойчивость формул).
4. **Авторинг 6 схем** из `spec/schemas/` — начать с малой `competitor_channel_data`; `video_data` со
   `SCENES`/статусами руками по `ИНСТРУКЦИЯ_шаблоны §5.2/5.3`. Формат: `enum` = тип (не флаг).
5. **F28** — dependency-aware delete/move (или запрет молчаливого слома формул).

## Критерий приёмки
- Loader e2e материализует книгу из `.schema.yaml`; `validate_formulas` (LO recalc, S16) зелёный на результате.
- Неполные данные → деградация (пусто/PENDING/0), НЕ `#DIV/0!`/`#REF!` (тест E-F).
- structure 35/35 + tables 33/33 держатся; ruff/mypy PASS; реал-пруф на живом сервере.

## Открытый вопрос владельцу
Где реальные `.xlsx` — интроспектор существующих книг (`scripts/introspect_tables.py`, F21) vs ручной
авторинг схем? (Влияет на объём шага 4.)
