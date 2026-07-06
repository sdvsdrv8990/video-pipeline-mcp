# 02 — Реестр находок (F#)

> Пополняется каждым прогоном. Severity: 🔴 блокер enterprise · 🟠 важно · 🟡 желательно · ⚪ мелочь.
> «Пруф» — как проверено. «→» — воркстрим-получатель из `01_master_roadmap.md`.
> Сессия 1: посев тремя прогонами на **скелетной** глубине (структурные находки). Глубина по коду — в фазовых сессиях.

## Прогон 1 — Качество (skill: code-quality)

| F# | Sev | Находка | Пруф | → |
|---|---|---|---|---|
| F1 | 🔴 | `server.py` — 1521-строчный монолит: вся регистрация/логика инструментов в одном файле вместо тонких обёрток | `wc -l server.py` | A2 |
| ~~F2~~ | ⚪ | **ОТОЗВАНО S4:** «config/ops пуст = архитектура сломана» — НЕВЕРНО. ops/model_routing **намеренно упразднены**, консолидированы в `channel_config.yaml → resource_limits` (`media_tools_deployment.md §0`). Не дефект — устаревший README. См. `05` §0 | design-решение владельца | A4 (README), не A1 |
| F3 | 🟠 | Провайдеры ffmpeg/tts/stt/img = `NotImplementedError` (честные стабы — G16 соблюдён, но продукта нет) | grep `NotImplementedError` | P1–P4 |
| ~~F4~~ | 🟡 | **ПОНИЖЕНО S15 (обмер под-2):** «0 тестов, не ревьюен» — НЕВЕРНО. `tests/quick/test_search.py` содержателен (happy+контракт+adversarial D36 traversal+edge+QueryPlanner+`_detect_entity_type`); в коде следы D36/D37 (находили+чинили). Остаётся: relevance-eval (F31) + quality-находки F38–F42. Незрелость переоценена | `test_search.py`; D36/D37 в коде | A5, I7 |
| ~~F5~~ | ✅ | **ЗАКРЫТ S16 (A6/R-CONTRACT1):** DEFAULT-fallback в `reactions.py` теперь единообразен с обычной веткой — тянет `class`/`message_template`/`recovery` из записи `DEFAULT` реестра (было: хардкод `'Непредвиденная ошибка'` без точки, `reaction_class` не ставился). Регрессия `test_audit_fixes` (`get_error(unknown).message == DEFAULT.message_template`) 🟡→🟢. Осталось (не F5): `raw_message` для ИЗВЕСТНЫХ кодов перекрывает template — это НАМЕРЕННО (специфика важнее шаблона; template = дефолт при пустом raw) | `reactions.py` get_error DEFAULT-ветка; `test_audit_fixes` PASS | A6 ✔ |
| F6 | 🟡 | `id_generator` / `IDGenerator` — D28: dead inject-param / недёрнутые пути (проверить актуальность после table-tools) | память `table-tools-implemented` (D28 частично закрыт) | A6 |

## Прогон 2 — Стиль / структура (skills: project-conventions + anti-hardcode)

| F# | Sev | Находка | Пруф | → |
|---|---|---|---|---|
| ~~F7~~ | ✅ | **РЕШЕНО S6 (A4/OQ1):** README переписан под фактическую консолидированную архитектуру — убраны несуществующие `config/ops`/`paths.yaml`/`model_routing.yaml`/`.xlsx`; добавлены реальные `core/{firewall,search,excel,tables,paths}` + `config/{firewall,channel_config,tunnel}` + templates/workspace; маркеры ✅/🟠/🔲; ссылка на `docs/roadmap/`. «Ловить ложные сигналы» больше не с чего | README ↔ диск | A4 ✔ |
| F20 | 🔨 | Таблично-схемный слой (в работе S7): формат определён (`spec/TABLE_SCHEMA_FORMAT.md`) + proof `network_config.schema.yaml`. Осталось: loader (`table_materializer` + фаза ТАБЛИЦЫ) + авторинг 6 схем. `structure_create` откладывает книги в `tables_pending` (template_engine:155) | формат+proof готовы | A1′ |
| F21 | 🟠 | `scripts/introspect_tables.py` не существует → нет генерации схем из ~90 готовых Excel-книг (руками = недели). Причина пустого `scripts/` | `find introspect_tables.py` ∅ | A1′ |
| ~~F22~~ | ✅ | **РЕШЕНО S5 (OQ4):** 15 спек-файлов импортированы в трекаемый `docs/roadmap/spec/` (schemas/ + instructions/ + Бриф/project_memory.spec/media_tools_deployment) + индекс `spec/README.md` + ledger улучшений `spec/IMPROVEMENTS.md`. Оригиналы в `/home/admin/projects/` оставлены | `find docs/roadmap/spec` = 17 файлов | OQ4 ✔ |
| F8 | 🟠 | Пустые заскаффолженные каталоги: `tools/{5}`, `pipeline/{entry_points,steps}`, `scripts/` — структура-обещание без наполнения | `ls -R` | A1/A2/P5/P6 |
| F9 | 🟠 | `docs/dev/audit/` отсутствует целиком — словарь D#/G# без артефактов, невоспроизводим/нешарибелен | `ls docs/dev/audit` → нет | X1/I8 |
| F10 | 🟡 | Хардкод-хуки (память audit-v2): stt `device=cuda` захардкожен; ffmpeg `render_full_pipeline` busy-loop без sleep | память `audit-v2-task` deferred-hooks | P1, P3, anti-hardcode |
| F11 | 🟡 | Провайдеры: api_key-гигиена tts/img; `raw_response` может течь в `_map_error` (D23) | память audit-v2 deferred | I6, P2/P4 |
| ~~F19~~ | ✅ | **РЕШЕНО S14:** `LICENSE` = **PolyForm Noncommercial 1.0.0** (владелец выбрал — защита IP: код виден, коммерц-использование запрещено). pyproject классификатор обновлён. DIM-9 repo-гигиена ↑ | ранее: Нет файла `LICENSE` — публичный репо без лицензии = юридически «all rights reserved», блокирует внешний вклад/использование. Владелец не выбрал лицензию (в pyproject помечено `Private :: Do Not Upload`) | `ls LICENSE*` → нет (S3) | I2/I8 (ждёт решения владельца) |

## Прогон 3 — Системы / безопасность (skills: security-reviewer + test-master)

| F# | Sev | Находка | Пруф | → |
|---|---|---|---|---|
| ~~F12~~ | ✅ | **РЕШЕНО S2 (I1):** `tests/` был свален в секцию «ДАННЫЕ» с `workspace/`. Убран из `.gitignore` (тесты = код); 23 файла (6 quick + 6 симуляций + scenarios.yaml) в git; `__pycache__`/`.pytest_cache` остаются игнорированы. Секретов в tests/ нет (проверено) | `git diff --cached` чист | I1 ✔ |
| F13 | 🔴 | Нет CI/CD — ни линта, ни типов, ни прогона тестов, ни security-scan на PR | `.github/workflows` нет | I3 |
| F14 | 🔴 | Нет app-level auth: за туннелем один клиент, IP-гранулярность бесполезна (G18); нет аутентификации на уровне приложения | память `firewall-audit`, G18 | I6 |
| F15 | 🟠 | D3 (открыт) — предполагаемый дефект из v2-аудита, не закрыт; D29 (открыт) — traversal через `state_manager`, D1 закрыт лишь частично | память `audit-v2-task` (D3 OPEN, D29 🟠) | I6 |
| F16 | 🟠→🔨 | `threat_landscape.md` отсутствует на диске (заявлен в скиле security-reviewer как источник угроз). **Частично закрыт S8:** каталог угроз восстановлен в git-tracked `06_threat_catalog.md` (IN1–IN10 + OUT1–OUT8 + митигации + приоритет). Осталось: при желании зеркалить в `docs/dev/threat_landscape.md` | `find threat_landscape*` → ∅; `06_threat_catalog.md` создан | I8, security |
| F36 | 🟠 | **DDoS/пакетная защита не задокументирована как двухуровневая + origin-gaps.** Сервер за cloudflared → edge держит L3/4+L7 DDoS/WAF/rate/bot **бесплатно always-on** (origin не выставлен) [C4] — но: (1) edge-настройки не зафиксированы (DDoS=High, rate-rules, Bot-Fight `Definitely Automated=Allow` иначе туннель падает `websocket: bad handshake`); (2) origin-gaps: per-IP rate бесполезен за туннелем (G18) → нужен identity-rate; нет slowloris-таймаутов/conn-лимита; нет явного payload-size-лимита; строгий JSON-RPC/reject-CL+TE не подтверждён. Кэша польз.данных НЕТ (только `template_engine._cache` git-данные, ⚪) — практики §H.3 forward. Все практики §H [C1–C7] | `06_threat_catalog.md §H`; `template_engine.py:73` | I6 (P0 edge+identity-rate), A5 (кэш) |
| F35 | 🔴→✅baseline | **No-root инвариант (эскалация привилегий через сервер).** Требование владельца: физически невозможно получить root на машине клиента через сервер. **Эмпирика 2026-07-05 ЧИСТО:** нет `os.system`/`eval`/`exec`/`pickle`/`shell=True` в коде инструментов; сервер не от root (uid 1000); единственный subprocess = cloudflared arg-списком (`tunnel.py:270`, без shell). Задача = НЕ регрессировать: инварианты G-1..G-6 (`06_threat_catalog.md §G`) — не исполнять контент workspace (делает `.py`-allowlist безопасным), нет shell, не root/least-priv, containment блокирует persistence-файлы, safe_load, media-subprocess arg-list. Регрессия = bandit-gate в CI + тест-не-root + рой-агент. **Deploy-hardening §G.1** (проверенные практики, не выдумка): non-root+`NoNewPrivileges`+cap-drop ALL, Landlock (writable=workspace), seccomp-профиль, контейнер по Anthropic Agent SDK baseline (`--cap-drop ALL`/`no-new-privileges`/`--read-only`/`--tmpfs`/`--user 1000`), gVisor/Kata для media. Плюс: транспорт HTTP не STDIO → мимо CVE-2025-49596 | grep exec-sinks ∅; `id -u`=1000; `tunnel.py:270` arg-list; §G.1 [S1–S7] | I6 (P0), I3 (CI bandit), agent-swarm |
| F34 | 🟠 | **Нет write-type allowlist — материализуем любой тип файла.** `fs_create_file`/`fs_write_file` берут произвольный `content` и путь без проверки расширения → можно записать `.sh`/`.html`/`.js`/`.exe`/малварь (усиливает OUT5, цель рой-атакующего «create virus»). Легитимный словарь узок: `.json/.md/.xlsx` (шаблоны), `.yaml` (конфиг), `.py` (скрипты); медиа-ассеты по фазам. Fix = **default-deny allowlist** декларативно в конфиге + единый choke-point на всех путях записи + код `FILE_TYPE_FORBIDDEN` (`06_threat_catalog.md §F`). React/css/веб-типы серверу не нужны вообще | `server.py:169/180` пишут без type-guard | I6 (P0), anti-hardcode, reactions-errors |
| F33 | 🔴 | **Outbound-защита клиента = сплошной gap.** Каталог `06_threat_catalog.md` §B: OUT1 (нет провенанс-маркировки workspace-вывода) / OUT5 (containment на write/move/delete + destructiveHint не подтверждены сплошняком) / OUT6 (`_SESSION_LOG.md` пишет сырой `Fact.data` → exfil) / OUT8 (search-poisoning) — ни один не закрыт. Плюс IN: app-level auth отсутствует (D3/G18) = фундамент под IN3/IN9. Приоритет фиксов — `06 §D` | `06_threat_catalog.md` §B/§D | I6 (P0-митигации), security-reviewer, agent-swarm |
| F17 | 🟠 | Секрет-гигиена: `tunnel.yaml` (D31) — уже в .gitignore, но проверить отсутствие ключей в трекнутых файлах; `.env`-стратегия | grep `token/api_key`, gitleaks | I3, I6 |
| F18 | 🟡→🟢 | Симуляционные тесты **существуют на диске и теперь в git** (S2): `virus_injection`, `bot_army`, `cache_injection`, `cache_overflow`, `config_change`, `render_draft_final`. Остаётся: подтвердить, что зелёные в CI (часть требует живого сервера, как firewall 1/4) | `find tests/` после I1 | I7 |

## Прогон 4 — Конформность структуре (skill: project-conventions)

| F# | Sev | Находка | Пруф | → |
|---|---|---|---|---|
| ~~F23~~ | ✅ | **РЕШЁН S8 (OQ6 = reconcile-by-purpose):** `config/ops/{filesystem,tables,excel}.ops.yaml` = реестр операций tool-обёрток (закон §3) → **строим** (A3, пара к A2); **media** остаётся в `channel_config.resource_limits` (media-план), своего `media.ops.yaml` нет; `model_routing`/`paths` минорно. Оба документа правы в своём скоупе | решение владельца | A3 |
| F24 | 🟠 | Конформность структуре не достигнута: инструменты в монолите `server.py` вместо `tools/<group>/`; `pipeline/` пуст; доки не зеркалят код (`docs/dev/tools/`). Закон §0/§2 нарушен | `ls tools/ pipeline/` ∅ | A2, P5/P6, I8 |
| F32 | 🔴 | **Симуляции одновариантны — нет агентного adversarial-харнесса.** `bot_army`/`virus_injection`/`cache_*` бьют по `Firewall` в один поток одинаковыми payload'ами; нет (а) множества честных клиентов с РАЗНЫМИ моделями поведения, (б) злоумышленников, (в) СМЕСИ честные+атакующие в одном прогоне, (г) **outbound-атак** (сервер-как-оружие против Claude AI Web: обход защиты Claude, кража данных, удаление файлов пользователя, генерация вредоносного ПО). Симуляции — штатный движок многовариантных тестов, но используются на 10% | `tests/bot_army/*` один поток, нет agent-swarm; outbound-слой в таблице слоёв = 🔴 gap | I7, security-reviewer (обе стороны), test-master |
| F31 | 🟠 | **Нет eval-слоя качества пайплайна (сверх контракта).** Контрактные тесты проверяют «валидный ToolResult», но не КАЧЕСТВО: насколько хорош выбор информации для сценария/аудио/картинок, релевантность умного поиска (`core/search`), поток данных. Ключевой диагностический разрыв: не отделено «хромает ПРОМПТ» от «плох ПОТОК ДАННЫХ» (лишний/ненужный контекст vs недостаток). Поисковый слой (`FsSearcher`/`QueryPlanner`, `search_quick/multi/tables`) существует и тестируем сейчас (quick 24/24, но не ревьюен — F4, без relevance-eval); генерация данных/асетов и eval выбора-контекста — forward к P1–P7. Экономия токенов = синтетика+локальные модели, не облачные LLM в тестах | `core/search/*` без relevance-eval; eval-харнесса нет | A5 (search), P1–P7, test-master (E-G/H/I) |
| F30 | 🟠 | **Устойчивость формул к неполным данным не заложена в loader (Ф3).** Требование владельца: при отсутствии части данных (нет/часть асетов, нет конкурентов, частичные данные канала) формулы НЕ должны ломаться — деградировать (пусто/PENDING/0), не `#DIV/0!`/`#REF!`. Дизайн для этого В СПЕКЕ есть: `scene_profile.enabled=false` → формула уникальности пропускает тип («тихий столбец»), `niche_weight` — вес, `automation_rules.condition` — пороги, `signal_on_reuse/reuse_threshold` — сигналы уникальности + вариации асетов `AST_x`. Но table-loader/фаза формул (Ф3) не построены → механизма нет. Обязан быть ДЕКЛАРАТИВНЫМ (флаги конфига), не `if type missing` в коде (anti-hardcode) | `channel_config.schema.md §6 scene_profile`; Ф3 loader ∅ | A-tables (Ф3), anti-hardcode, test-master (E-F) |
| F28 | 🟠 | **Деструктив над столбцами молча ломает формулы.** `excel_delete_column`/`excel_move_column` работают по сырым ячейкам openpyxl (`delete_cols`/`insert_cols`/копия `.value`) — формулы, ссылающиеся на затронутый столбец, съезжают/ломаются БЕЗ реакции в момент операции. Нет пред-проверки «на этот столбец ссылается формула». Сервер должен предупреждать/отклонять или авто-валидировать после | `excel_core.py:242/253` не трогают формульные ссылки | A-tables, reactions-errors |
| F29 | 🟠 | **`validate_formulas` — слепой детектор.** Ищет только cached error-ТОКЕНЫ (`#REF!/#VALUE!/…`) grep-ом по строкам (`excel_core.py:343`). openpyxl НЕ пересчитывает формулы → свежесломанная ссылка (валидная, но указывает не туда) токена не даёт → не ловится. Детектор ≈ театр для move/delete-коррапта | `validate_formulas` = grep токенов, нет recalc | A-tables, test-master (E-D5/6) |
| F26 | 🔴 | **Cold-start advisory отсутствует.** На пустой нише (0 объектов) сервер обязан вести онбординг: уведомить «данных для анализа нет» + рекомендованный путь (изучить конкурентов — скриншоты каналов + 2-3 транскрибации сценариев) + fallback (начать с личного канала). В коде НЕТ: `structure_status` (server.py:624) выдаёт лишь orphans+childless → на пустом реестре молчит. Спека-требование владельца (2026-07-05), поведение продукта, не мелочь. Тест = xfail-spec (E-A1) | grep advisory/cold-start в core/ ∅; `structure_status` возвращает пустые списки | A3/structure, honest-stub G16 |
| F27 | 🟠 | **Reconcile-оркестрация ручная.** `structure_migrate` физически переносит (`shutil.move` server.py:616) + правит реестр, но ОДНУ сущность с явным `new_path` от ИИ. Сценарий владельца «конкурент появился → сервер сам связал конкурента↔наш канал И фоном переместил ОБА в `competitors/{наш_канал}/`» требует авто-оркестрации (вычислить целевой путь §4 + переместить обе сущности + link одним шагом) — её нет | `structure_migrate` сигнатура `(entity_id, new_path)`; нет авто-вычисления пути §4 | A3/reconcile (пара к F25/F26) |
| F25 | 🟠 | **Integrity-слепое пятно ФС ↔ реестр.** `structure_check_integrity` аудирует ТОЛЬКО `LinkRegistry` (`_id_registry.json`), не файловую систему. Реестр наполняет лишь `structure_create`; `fs_create_project_structure`/`fs_write`/тесты создают те же папки МИМО реестра. Итог: инструмент рапортует `total_entities:0, issues_count:0`, пока на диске лежит незарегистрированное дерево (`workspace/niches/_TEST_finance`, `workspace/channels/test` — реестра нет). Персист-бага НЕТ (эмпирически: реальный `create_node`+`register` в venv → `total_entities:1`, файл пишется) — дыра в ПОКРЫТИИ проверки, а не в записи. Fix = reconcile-скан ФС↔реестр (найти on-disk узлы без записи и наоборот) в `check_integrity` | эмпирика: venv-прогон `create_node`+`register`=1 сущность; live `check_integrity`=0 при непустом `workspace/` | A3/reconcile (пара к F23), project-conventions |

## Прогон 5 — Обмер S15 (ступень A §6: состояние vs ожидания, широкий проход)

> Первый заход Блока 1. Цель — общая карта «что есть vs что ожидалось» + спорные места, чтобы
> размерить глубокие поды-проходы. Всё проверено ЧТЕНИЕМ кода на диске (не по памяти).

**✅ Лучше, чем подразумевала память (снять тревогу):**
- **G17 choke-point РЕАЛЬНО консолидирован:** `core/paths.safe_resolve` — единственная реализация
  containment, импортируется ВСЕМИ 6 модулями с путями (`server.py:47`, `state_manager:15`,
  `excel_core:28`, `template_engine:32`, `fs_searcher:100`, `query_planner:65`). `server.py:_safe_resolve`
  = тонкая back-compat обёртка-делегат. Безопасность-паттерн (DIM-16) по путям — не россыпь, а choke-point.
- **D29 закрыт в state_manager:** `safe_resolve(...)` на каждом entity-path (`state_manager.py:64/83/100/131/161/205`).
- **Wiring чист (`create_server` server.py:96):** D2 (firewall-config грузится), D4 (реакции→engine),
  D24 (state_manager→engine). Не «мёртвые объекты».
- **Хендлеры ТОНКИЕ:** каждый = `_safe(lambda: <engine>.method(...))` → логика в `core/*` движках
  (`table_engine`/`excel_engine`/`template_engine`/`link_registry`), НЕ в обёртке. `_safe` мапит
  исключения в коды реакций; типизированные `TableError/ExcelError/TemplateError/LinkError` несут `.code`.
- **Следствие для A2:** монолит `server.py` = все хендлеры в ОДНОЙ функции `register_basic_tools`
  (стр. 127→1248, ~1120 стр), но логики в них нет. **A2 = механическая экстракция в `tools/<group>/`,
  риск НИЗКИЙ** (движки трогать не надо; `engine.register(group=...)` уже группирует). Пересматривает F1: дефект = размещение, не «логика в обёртке».

**🟠 Подтверждено на диске (как и ожидалось):** F1/F24 (монолит-размещение), F3 (провайдеры-стабы),
F4 (search 0 тестов), F5 (реакции fallback частичен), F13/F14 (нет CI/auth), F25/F26 (integrity/cold-start),
F28–F30 (формулы-театр). Глубокая проверка каждого — в подах Блока 1.

**Новая находка:**
| F# | Sev | Находка | Пруф | → |
|---|---|---|---|---|
| F37 | 🟡 | **`_safe` ловит голый `ValueError` → всегда `PATH_ESCAPE`.** Любой не-путёвый `ValueError` из глубины core (напр. плохой аргумент) получит вводящую в заблуждение реакцию «путь выходит за workspace». Типизированные исключения уже есть (`TableError` и пр.) — голый `ValueError` только для traversal, но ловится слишком широко | `server.py:513` `except ValueError → PATH_ESCAPE` | A2/A6, reactions-errors |

### Под-2 обмера — `core/search` (S15, прочитаны оба модуля целиком)

**❗ F4 НЕВЕРНА (правка):** `core/search` НЕ «0 тестов, не ревьюен». `tests/quick/test_search.py` содержателен:
happy-path (ext/name/content/limit/subdir), контракт `FileResult`, `_extract_id`, **adversarial D36
path-traversal** (`PATH_ESCAPE` на `/etc`, `../../../../etc`, `docs/../../..`), edge `PATH_NOT_FOUND`,
`load_query` YAML, `QueryPlanner` containment, `_detect_entity_type` по всей иерархии. В коде следы
**D36** (traversal-containment) и **D37** (`_detect_entity_type` FIXED) — находки уже находили и чинили.
→ **F4 понизить: search тестирован+ревьюен; остаётся relevance-eval (F31), не «сырьё».**

**Новые находки (обмер, НЕ чиним — фиксация):**
| F# | Sev | Находка | Пруф | → |
|---|---|---|---|---|
| ~~F38~~ | ✅ | **ЗАКРЫТ S16 (I4):** мёртвый `self._lock = threading.Lock()` + `import threading` удалены из обоих search-классов (0 `.acquire`/`with`). Найдено и снято при адаптации линта | `fs_searcher.py`/`query_planner.py` — lock+import убраны | I4 ✔ |
| F39 | 🟠 | **`QueryPlanner` лезет в приватный `table_engine._load`** (`query_planner.py:232`). Кросс-модульный доступ к приватному API движка → хрупко (смена `_load` тихо ломает поиск). Нужен публичный read-метод | `sheet_data = self.table_engine._load(task.table)` | A5, code-quality (DIM-13) |
| F40 | 🟢 | **ЧАСТИЧНО ЗАКРЫТ S16 (A6):** коды `QUERY_NOT_FOUND`/`PATH_NOT_FOUND` добавлены в `server_reactions.yaml` (class `ai_recoverable` + recovery) → search-хендлеры `_err(e.code,…)` теперь мапят их через реестр (регрессия `test_audit_fixes` 🟡→🟢). Остаётся (→ A5): дубль exception-классов `FsSearchError`/`SearchError` (одинаковая форма) — консолидировать при хардене search | реестр: `QUERY_NOT_FOUND`/`PATH_NOT_FOUND` есть; `test_audit_fixes` PASS | A5 (дубль-классы), ~~A6~~ |
| F41 | 🟡 | **Захардкожена иерархия workspace + формат ID (DIM-14, дубль конфига).** `ENTITY_PATH_PATTERNS`, маркеры `_detect_entity_type` (`niches/networks/channels/videos/competitors`), `FILE_TYPE_MAP`, ID-regex `[A-Z]+_[0-9a-f]{32}` зашиты в код — та же структура живёт в `config/templates/workspace/*.tpl.yaml` + `IDGenerator`. Два источника правды → дрейф; **ломается при кастомной структуре канала (решение S14 — шаблоны настраиваемы)** | `fs_searcher.py:145/158/324`, дубль `templates/workspace` | A5, A7 (per-project override), anti-hardcode |
| F42 | 🟡 | **Overclaim «умного» поиска (honest/quality).** `analyze_dependencies` в доке обещает «граф зависимостей + оптимальный порядок», реально = group-by-table (`query_planner.py:151`). «Умный поиск» = структурная фильтрация точных совпадений, не семантика (ties F31 relevance-eval нет). Плюс `_match_filter` gt/lt и `_apply_sort` сравнивают разнотипное (str vs num) → `TypeError` посреди поиска, без guard (ties F30) | дока vs `_parse`/`analyze_dependencies`; `_match_filter:269`, `_apply_sort:329` | A5, test-master (F30/F31) |

### Под-3 обмера — реакции/ошибки (S15, `reactions.py`+`server_reactions.yaml`+движки прочитаны)

**B2-корень ДОКАЗАН — две расходящиеся ветки эмиссии ошибок:**
- **Ветка хендлеров** (`server.py:_safe`→`_err`, ВСЕ fs/table/excel/structure): `_err(code, message, reason, suggested_tool)` строит `ErrorDetail` **напрямую из полей исключения** (движки хардкодят message: `TableError("INVALID_ACTION","В action отсутствует 'sheet'.",…)`), **реестр `server_reactions.yaml` НЕ трогает**. → теряются `reaction_class` (не ставится), `message_template`, `recovery.suggested_params` (реестр их определяет, клиент не получает).
- **Ветка `engine.execute`** (`engine.py:64`): единственная, кто **использует** реестр (`get_error`), но `raw_message` перекрывает `message_template` (F5).

**✅ Паритет кодов ЧИСТ (позитив):** все 9 кодов движков (COLUMN_EXISTS/…/VALIDATION_ERROR) есть в реестре (34 кода) — орфан-дрейфа нет. **Но** search-коды `QUERY_NOT_FOUND`/`PATH_NOT_FOUND` (F40) в реестре ОТСУТСТВУЮТ — подтверждено.

**Что есть vs что должно:** есть = пассивный маппер + доминирующая ветка мимо него; должно = **единое ядро** — любой путь через `get_error(code, params)`, движки эмитят ТОЛЬКО код (+динам. параметры), реестр даёт class/message/recovery; DEFAULT — тоже через реестр; `message_template` с подстановкой параметров.

**Новое:**
| F# | Sev | Находка | Пруф | → |
|---|---|---|---|---|
| ~~F43~~ | ✅ | **ЗАКРЫТ S16 (A6/R-CONTRACT1, B2-корень).** `_err` теперь роутит через `engine.reactions.get_error()` (yaml = единственный источник `class`/`recovery`; raw message сохраняет специфику; fallback лишь для кодов вне реестра — как `engine.execute`). Плюс 24 прямых `ErrorDetail(...)` в `fs_*`-хендлерах сведены к `_err(code, msg)` → **все пути ошибок через реестр**, дубль recovery-строк код↔yaml устранён (анти-хардкод). Регрессия `test_audit_fixes`: error несёт `reaction_class` И `recovery.reason` из реестра (🟡→🟢, 2 проверки). Baseline держится: 33/33 tables + 35/35 structure + 24/24 search | `server.py` `_err`+24 сайта; `test_audit_fixes` PASS | A6 ✔, reactions-errors |

**F5 уточнён:** к DEFAULT-хардкоду (UNKNOWN_ERROR + класс не ставится + message_template игнорится) добавить: **`raw_message` перекрывает `message_template` и для ИЗВЕСТНЫХ кодов** → template реестра почти не используется.

### Под-4 обмера — монолит `server.py` (карта распила A2, S15)

**Структура (доказано чтением):** `register_basic_tools` (127→1248, ~1120 стр) = **11 `engine.register`**, но 44+
инструмента → регистрация **циклами по спек-спискам**: `fs_tools`/`memory_tools`/… = `list[tuple(name,
title,desc,schema,handler,annot)]` + `for …: engine.register(…, group=…)`. Стили: **loop-driven** (filesystem/
memory/excel/tables/search) + **явные** (structure ×5, часть tables). Хендлеры тонкие (под-1).

**Есть vs должно:**
- **Есть:** декларативные метаданные инструментов УЖЕ живут как Python-спек-списки + inline JSON-схемы в `server.py`.
- **Должно (A2/A3):** `tools/<group>/` на группу (filesystem/tables/excel/search/structure/memory) с хендлерами +
  `config/ops/<group>.ops.yaml` (вынесенный спек-список); `server.py` = `create_server`/`run_server`/`main`.
  **Подтверждает OQ-B2:** A3 = ВЫНЕСТИ спек-списки в yaml (единый источник), НЕ слой поверх. **Риск НИЗКИЙ** (механика).

**Новое:**
| F# | Sev | Находка | Пруф | → |
|---|---|---|---|---|
| ~~F44~~ | ✅ | **ЗАКРЫТ S16 (I4):** 53 function-local `from core.contracts import …` сведены к одному module-top импорту (устранил заодно 52 ruff F821 на строковых аннотациях `-> "ToolResult"`). Единичные локальные `re`/`shutil`/`yaml`/`datetime` (по одному месту использования) оставлены — не smell, ruff чист | `server.py` module-top import; ruff `All checks passed` | I4 ✔ (остаток стиля → A2) |
| F45 | 🟡 | **Захардкожен Python-skeleton (DIM-14).** `fs_create_python_script` держит codegen-шаблон `.py` как inline f-string в хендлере (`server.py:440`) — шаблон должен быть файлом в `config/templates/`, не в коде | `server.py:440 skeleton = f'…'` | A2, anti-hardcode |
| F46 | 🟡 | **Таксономия entity_type захардкожена ТРЕТИЙ раз (расширяет F41).** JSON-schema enum `["niche","network","channel","video","competitor_channel","competitor_video","asset","scene","render"]` в `server.py:897` = та же в `fs_searcher.ENTITY_PATH_PATTERNS` + `templates/workspace` → три источника правды типов сущностей. Дрейф; ломается при кастомной структуре (S14) | `server.py:897` ↔ `fs_searcher.py:145` ↔ templates | A2/A3, A7, anti-hardcode |

### Под-5/6/7 обмера — формулы / провайдеры / контракты-state-transport-engine (S15, подтверждения)

**Под-5 формулы (`excel_core`) — F28/F29/F30 ПОДТВЕРЖДЕНЫ на диске:** `delete_column`/`move_column` = сырой
openpyxl (`delete_cols`/`insert_cols`) без проверки формульных ссылок (F28); `validate_formulas` = grep токенов
`#REF!/#VALUE!/…` без пересчёта (F29, театр); `core/engine/table_materializer.py` **НЕ существует** → loader
формул/устойчивости не построен (F30/F20). **Data-формульный слой незрел** (что должно: loader + пред-проверка ссылок + деградация неполных данных).

**Под-6 провайдеры — подтверждения + позитив:** F3 ✅ — все 4 провайдера (img/tts/stt/ffmpeg) = `NotImplementedError`
с внятным сообщением → **честные стабы (G16 соблюдён идеально)**, не тихий success. F10 ✅ — stt `device="cuda"`
захардкожен дефолтом (`stable_ts_adapter.py:20`). **F11 УТОЧНЁН/митигирован:** `raw_response` имеет активный
санитайзер `ErrorDetail._sanitize_raw_response` (маскирует секреты, D23) → утечка raw_response ЗАКРЫТА; остаётся api_key-гигиена (стабы, не горит).

**Под-7 контракты/state/transport/engine — ЗРЕЛЫЕ (позитив) + уточнения:**
- **Контракты зрелые:** `ToolResult.status`/`TaskStatus.status` = `Literal[…]` (эталон G15); `ErrorDetail` с санитайзером D23.
- **Транспорт D30 ЗАКРЫТ:** error-ветка (`transport.py:125–129`) кладёт `code`/`reaction_class`/`recovery` в `structuredContent`; facts → structuredContent (success). Транспорт **готов** донести данные реестра до клиента.
- **⚠️ Обостряет F43:** раз транспорт готов, единственная причина обеднённого `ErrorDetail` у клиента — **хендлеры (`_err`) не заполняют reaction_class/recovery из реестра**. Фикс F43 даёт полный payoff (данные дойдут).
- **Коррекция D24:** `log_event` **ВЫЗЫВАЕТСЯ** (`engine.py:130`) — «0 callers» устарело; но пишет сырой `fact.data` в `_SESSION_LOG` (OUT6/B1 exfil подтверждён).
- **Engine error-path** идёт через реестр (`engine.py:65 get_error`) — как и в под-3.

## Реестр спорных моментов (обмер S15 — решения владельца, «плохо продумано?»)

> Не дефекты-факты, а **дизайн-споры**: система работает, но продуманность под вопросом. **✅ Все решены владельцем S15** (проверено на диске).

- ~~**OQ-B1 (`_SESSION_LOG.md`):**~~ ✅ **РЕШЕНО.** Владелец: лог = **техническая инфа, не клиентские файлы** →
  корень репо **намеренно** (не нарушение «двух вселенных» — это server-технический артефакт, не данные
  клиента). **Дубля НЕТ** (пруф: на диске один `./_SESSION_LOG.md` 47KB, единственный write-путь
  `state_manager.py:184`; прежний «дубль» ИИ уже схлопнут). Остаётся ОДНА реальная проблема — **OUT6-exfil
  сырого `Fact.data`** в лог → это security (F33/I6), не размещение. Действие: санитизировать запись в лог,
  не переносить файл.
- ~~**OQ-B2 (дубль ошибок код↔yaml):**~~ ✅ **РЕШЕНО** (владелец переформулировал в суть). Проблема: ошибки
  **живут в питон-коде** (движки бросают `TableError(code=, message="...")` с зашитыми строками) **И** есть
  `server_reactions.yaml` — но **своего ЯДРА у ошибок нет** (`reactions.py` = частичный загрузчик, F5), отсюда
  **расхождение** двух источников. **Решение:** дать реакциям настоящее ядро — **yaml = единственный источник**
  сообщений/кодов, движки эмитят ТОЛЬКО код, ядро подставляет message/recovery из реестра. Дубли убрать.
  **Принцип (обобщение):** есть `.yaml` → у него обязано быть ядро-единственный-источник; параллельного
  хардкода в питоне быть не должно. Тот же принцип к A3 (если строим `ops.yaml` — он ЗАМЕНЯЕТ аргументы
  inline `engine.register`, не поверх). → A6 (реакции), reactions-errors.
- ~~**OQ-B3 (два движка):**~~ ✅ **РЕШЕНО — дубля НЕТ, но и не CQRS-над-одним-store.** Пруф: `table_engine`
  (`tables_core.py`) работает на **`read.json`** (снапшот) + write-очереди, **`.xlsx` не трогает**
  (синк отложен, `xlsx_synced=False`, G16); `excel_engine` = openpyxl на **`.xlsx`** (структура/формулы).
  Оба поиска (табличный `query_planner→table_engine._load`, файловый `fs_searcher`) читают **`read.json`**
  (догадка владельца про «второй движок для поиска» — почти: поиск читает read.json через table_engine, не
  excel_engine). Итог: **ДВА ХРАНИЛИЩА** — `read.json` (данные, SoT данных+поиска) vs `.xlsx` (структура+формулы),
  **мост синхры не построен** (G16). **Источник правды ячейки-ДАННЫХ = `read.json`.** ⚠️ **A7-следствие:**
  формулы уникальности живут в `.xlsx`, но данные — в `read.json` → локальный скрипт уникальности (S14)
  **должен читать `read.json`** и туда же писать результат; либо сперва закрыть G16-синк. → A1′/A7, A5, G16.
- ~~**OQ-B4 (`_safe` широкий catch):**~~ ✅ **РЕШЕНО (моё решение, владелец делегировал).** Ввести
  **типизированный `PathEscapeError(ValueError)`** в `core/paths.py`; `safe_resolve` бросает его; `_safe`
  ловит `PathEscapeError` → `PATH_ESCAPE`, а голый `ValueError` из core → общая реакция (`INTERNAL_ERROR`),
  не мислейбл. Back-compat цел (подкласс `ValueError`). Ложится на B2-принцип (ошибки из ядра). → A2/A6 (с §6-тестом), F37.

## Открытые вопросы (решить в фазах)

- **OQ1:** README переписать под факт (быстро) или догнать код до README (долго)? → влияет на A2/A4 vs пересмотр дизайна.
- **OQ2:** Аудит D#/G# — реконструировать `docs/dev/audit/` или единый источник = этот реестр F#? → X1.
- **OQ3:** `docs/dev` разгитигнорить (версионировать доки) или оставить приватным? → I1/I8 + пожелание владельца «отозвать позже».
