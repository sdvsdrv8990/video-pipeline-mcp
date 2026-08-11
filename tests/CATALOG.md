# Каталог тестов — зоны ответственности + правило «не плодить»

> Зачем: чтобы **развивать существующие** тесты, а не плодить. Каждый тест имеет ЗОНУ ОТВЕТСТВЕННОСТИ (что покрывает только он) и ЗАПАС РАСШИРЕНИЯ (что ещё впитает). Новый тест заводим ТОЛЬКО когда сценарий вне зон ВСЕХ существующих И запас хозяина-кандидата исчерпан.
>
> Полный тест-план (E-матрица, слои, приоритеты) — `docs/roadmap/03_testing_plan.md`. Угрозы для симуляций — `docs/roadmap/06_threat_catalog.md`. Скил — `test-master`.
>
> **Машинерия тестирования** (раннер, фикстуры, харнесс живого сервера, гейт, покрытие) — `docs/roadmap/14_testing_system_plan.md`, этапы T0–T9. ⚠️ До этапа T0 **`pytest` на репо даёт ложный зелёный** (F50): гоняй наборы скриптами (`python3 tests/<...>/test_<...>.py`, exit 0 = ok), как это делает `ci.yml`.

## Правило «не плодить» (жёстко)

1. **Сначала — расширить существующий** в его зоне ответственности (добавить сценарий/параметр/вектор). Каждый тест ниже указывает свой запас расширения.
2. **Новый тест ТОЛЬКО если оба верны:**
   - (а) сценарий **вне зоны ответственности ВСЕХ** существующих тестов, И
   - (б) **лимит расширения** естественного хозяина исчерпан — впитать сценарий сделало бы тест разнородным/нечитаемым/смешало бы уровни (unit vs симуляция vs e2e).
3. **Перед новым — «не дублирую ли?»** (README §4): найти похожий, проверить, не решалось ли.
4. **Размещение нового** (project-rules §3): быстрый/гипотеза → `tests/quick/` (после прогона удалить); постоянный → `tests/<name>/`.

## Два класса тестов

- **Простые** (`tests/quick/`) — in-process / unit / contract / регрессия. Быстрые, точечные, ассерт на контракт `ToolResult`/`ErrorDetail`/код реакции. Часть постоянные (регрессии), часть — «создал→прогнал→удалил».
- **Симуляции** (`tests/<name>/`, adversarial/system) — сценарные, многовариантные, против `core/firewall` / живого сервера. Первый класс: проверяют, что защита реально ловит И подключена. Развивает их скил `test-master`.

## A. Простые (`tests/quick/`)

| Тест | Зона ответственности (ТОЛЬКО он) | Зачем | Запас расширения (впитывает) | Новый рядом оправдан, если… |
|---|---|---|---|---|
| `test_audit_fixes.py` | **Дом ВСЕХ регрессий закрытых `D#`** (D1–D13) | откат фикса → красный | +регрессия на каждый новый закрытый `D#`/`F#` | никогда — это единый дом D#-регрессий |
| `test_firewall.py` | firewall happy/block контракт (injection/rate/IP) против живого сервера | защита отклоняет атаки, пропускает легит | новые firewall-правила, векторы IN2/IN3/IN5, identity-rate | правило переросло в отдельную СИМУЛЯЦИЮ (→ dir) |
| `test_search.py` | `core/search` coverage+регрессия (FsSearcher/QueryPlanner, D36 traversal) | search-контракт/поведение | relevance-eval (E-I), новые `search_*`, poisoning-outbound | search-качество как отдельный eval-слой перерастёт unit |
| `test_structure.py` | `TemplateEngine` Ф1 (depth-control, PATH_ESCAPE, ID) | структура/шаблоны/глубина | E-A/E-B/E-C эмуляция, `structure_link/migrate`, F25 reconcile | эмуляция «реальной работы ИИ» станет тяжёлой сценарной (→ dir) |
| `test_tables.py` | table/excel smoke-контракт (Кат.2+3) | инструменты таблиц отвечают контрактом | E-D деструктив, формулы/устойчивость (F30), `table_materializer` (Ф3) | деструктив-над-таблицами станет adversarial-симуляцией |
| `test_tunnel.py` | `core/transport/tunnel` парсер+автомат оффлайн (D11) | регрессия логики туннеля без cloudflared | новые режимы туннеля, форматы логов | — (узкая стабильная зона) |
| `test_tools_inventory.py` | **Контракт инвентаря** `tools/list`: состав 52 + group/title/description/annotations/input_schema против эталона `tools_inventory.golden.json` | структурные правки (A2-распил, переезды групп) не двигают контракт клиента молча | новые инструменты/группы (эталон обновляется `--bless`, diff виден в ревью) | никогда — это единый дом инвентаря; поведение инструмента проверяют тесты его зоны |

## B. Симуляции (adversarial / system)

| Набор | Зона ответственности | Зачем | Запас расширения (впитывает векторы каталога) | Новый рядом оправдан, если… |
|---|---|---|---|---|
| `bot_army/` | массовое подключение → rate-limit + ban (**IN3**) | армия ботов реальна | agent-swarm honest+attacker, **identity-rate**, **slowloris** (§H.1), anomaly (IN5) | — обычно расширяем; рой уходит в `agent_swarm/` |
| `cache_injection/` | injection-паттерны в данные/кеш (**IN2/IN8**) | отравление данных | **HTTP-smuggling/malformed/jsonrpc-abuse** (§H.2), cache-key poison (§H.3), deser (IN8) | протокол-атаки перерастут «инъекцию» тематически |
| `cache_overflow/` | устойчивость при переполнении (**IN4/IN7**) | сервер не падает, кеш чистится | **payload-overflow**, cache-**stampede** (§H.3), resource-quotas (DIM-11) | — |
| `config_change/` | адаптация к смене конфига + уведомление (hot-reload, **OUT3 rug-pull**) | конфиг защищён, клиент уведомлён | rug-pull tools/list (OUT3), secret-hygiene (IN10/D31) | — |
| `virus_injection/` | блокировка malware/payload (**IN2/IN6**) | вирус не проходит | **write-allowlist** forbidden-filetype (§F/F34), **no-root** exec-workspace (§G/F35), outbound injection-via-output (OUT1) | — расширяем; это дом «сервер-как-канал-вреда» inbound |
| `render_draft_final/` | media/pipeline e2e workflow (**сейчас стабы**) | сквозной рендер | P1–P7 когда провайдеры готовы; E-D формулы таблиц | продукт-пайплайн станет многошаговым (→ `pipeline/`) |

## C. Рой (декларация, раннер = TODO)

| Артефакт | Зона ответственности | Статус |
|---|---|---|
| `agent_swarm/patterns.yaml` | **дом мульти-вариантных/эмерджентных сценариев**: N честных клиентов + N злоумышленников (inbound+outbound) в одном прогоне; 36 паттернов со `status` | декларация готова; раннер `test_agent_swarm.py` — TODO (I7). Сюда впитываются E-матрица под нагрузкой + IN/OUT-векторы, что не влезают в одиночный сим |

## D. Маршрут «куда класть новый сценарий» (сначала — в существующий)

| Хочу протестировать | Хозяин (расширяем ЕГО) | Новый только если… |
|---|---|---|
| закрытый дефект `D#`/`F#` | `test_audit_fixes` | — никогда |
| firewall-правило/вектор | `test_firewall` (unit) / `bot_army`+`virus_injection`+`cache_*` (сим) | вектор — отдельная угроза вне их тем |
| структура/рекомендации/проходы (E-A/B/C) | `test_structure` → при утяжелении `tests/structure_emulation/` | эмуляция переросла unit |
| деструктив/формулы таблиц (E-D/E-F) | `test_tables` → сим при утяжелении | нужен живой .xlsx с формулами (Ф3) |
| поиск/relevance (E-I) | `test_search` | eval-качество — отдельный слой |
| протокол/DDoS/пакеты (§H) | `cache_injection`/`cache_overflow`/`bot_army` | — |
| allowlist/no-root/outbound (§F/§G/OUT) | `virus_injection` | — |
| рой honest+attacker (F32) | `agent_swarm/` (раннер) | это и есть дом роя |

**Итог:** почти всё расширяет существующий тест. Реально НОВЫЕ постоянные наборы на горизонте — только `agent_swarm/test_agent_swarm.py` (раннер роя) и, при утяжелении, `tests/structure_emulation/` (E-матрица структуры). Всё прочее — сценарии внутри уже имеющихся зон.

---

## E. Реестр подтверждения находок (F# → тест → статус) — git-tracked

> Зачем: чтобы ИИ видел, какая находка обмера `02` чем подтверждается и в каком статусе. **Статусы —
> в git** (полный прогон тестов в лимит контекста не влезает → трекаем инкрементально, как и историю).
> Метод: **static** = подтверждено чтением/grep/ls (тест не нужен — закрывается lint I4 или постройкой);
> **behavioral** = нужен C1-тест против кода/живого сервера (§6 ступень C1 = код-пруф теории).
>
> Статусы: ✅ подтверждён · ⬜ нужен C1-тест · 🟡 OPEN-CONFIRMED (C1 красный = находка доказана, ждёт фикса) · 🔨 пишется · 🟢 регрессия зелёная после фикса.
>
> **Механика strict-xfail** (`test_audit_fixes.py` `xcheck`): открытая находка подтверждается «ожидаемо
> красным» (`[OPEN-CONFIRMED F#]`), но baseline остаётся зелёным (exit-код держат только регрессии). Если
> находка внезапно «проходит» (`[UNEXPECTED-PASS F#]`) → сигнал: фикс применён → обнови §E ⬜/🟡→🟢 и перенеси в регрессию.

| F# | Что | Метод | Тест-хозяин (не плодить) | Статус |
|---|---|---|---|---|
| **F43** | реестр обходится хендлерами → error без `reaction_class` из реестра | behavioral | `test_audit_fixes` (регрессия: `table_get_row` на нет-таблице → `reaction_class` И `recovery.reason` из реестра) | 🟢 **ЗАКРЫТ S16 (A6)** — `_err`→`get_error` + 24 `fs_*`-сайта; регрессия (2 проверки) зелёная |
| **F5** | DEFAULT-fallback игнорит `DEFAULT.message_template` | behavioral | `test_audit_fixes` (`get_error(unknown)` → assert message==template) | 🟢 **ЗАКРЫТ S16 (A6)** — DEFAULT-ветка тянет class/template/recovery из реестра; регрессия зелёная |
| **F40** | search-коды `QUERY_NOT_FOUND`/`PATH_NOT_FOUND` НЕ в реестре | behavioral | `test_audit_fixes` (assert коды ⊂ реестр) | 🟢 **ЗАКРЫТ S16 (A6)** — коды добавлены в `server_reactions.yaml`; регрессия зелёная. Остаток дубль-классов → A5 |
| **F42** | `_match_filter`/`_apply_sort` на разнотипном → TypeError | behavioral | `test_audit_fixes` (фильтр str vs int gt + сортировка разнотипного) | 🟢 **ЗАКРЫТ S16 (A5)** — type-safe фильтр+сортировка; регрессия зелёная (2 проверки). Остаток relevance/overclaim → A5 (F31) |
| **F29** | `validate_formulas`=grep токенов без пересчёта (театр) | behavioral | `test_audit_fixes` (`=1/0` → `validate_formulas` ловит через LO recalc; гардед по soffice) | 🟢 **ЗАКРЫТ S16 (A-tables)** — LibreOffice headless recalc; регрессия зелёная. F30 loader / F28 → отдельно |
| **F28** | delete/move_column ломает формулы молча (сырой `delete_cols`) | static | — (эвидентно из чтения `excel_core.py:242/253`; фикс A-tables) | ✅ подтверждён (чтение); одно корневое с F29 (нет пересчёта/зависимостей) |
| **F37** | `_safe` ловит голый ValueError → всегда PATH_ESCAPE | static | — (тригер контрив; `server.py:513` эвидентен; фикс A2/A6 = типизир. PathEscapeError) | ✅ подтверждён (чтение) |
| **F11** | raw_response митигирован (D23-санитайзер) | behavioral | `test_audit_fixes` (ErrorDetail с секретом → замаскирован) | 🟢 **регрессия ЗЕЛЁНАЯ** (api_key/nested token → `***REDACTED***`) |
| **F38** | мёртвый `_lock` в обоих search-классах | static | — (закрывается lint/vulture, I4) | ✅ подтверждён (grep) |
| **F39** | `QueryPlanner` лезет в приватный `table_engine._load` | static | — (архитектура, фикс A5) | ✅ подтверждён (чтение) |
| **F41/F46** | таксономия entity_type захардкожена 3× (search+schema+templates) | static→behavioral | `test_structure` (parity: три источника совпадают — metamorphic) | ✅ static; ⬜ parity-тест опционально |
| **F44** | повторные function-local импорты в ~15 хендлерах | static | — (lint, I4/A2) | ✅ подтверждён (grep) |
| **F45** | inline Python-skeleton (hardcode) | static | — (A2, вынести в template) | ✅ подтверждён (чтение) |
| **F10** | stt `device="cuda"` хардкод | static | — (config/anti-hardcode) | ✅ подтверждён (grep) |
| **F30** | `table_materializer` не построен (loader формул) | static | — (постройка A-tables/Ф3) | ✅ подтверждён (ls ∅) |
| **F3** | провайдеры = честные стабы (G16) | behavioral | `render_draft_final` (стаб → NotImplementedError-код, не фейк-success) | ✅ покрыт (стаб-контракт) |

**Статус C1 (S15) → фиксы (S16) — ВСЕ behavioral-находки ЗАКРЫТЫ:** A6 (F43·F5·F40) + A5-TypeError (F42) + A-tables (F29) переведены strict-xfail→регрессия. **`test_audit_fixes` 41/41, 0 OPEN.** F11 🟢 (D23). **static-находки** F38/F44 закрыты (I4); остаток F28/F37/F39/F41/F45/F10/F30 — эвидентны из чтения, закрываются A2/A-tables(F30)/anti-hardcode. Дальше — C2 симуляции+консоль (накоплены фиксы).

> **Правило статусов (git-native):** этот реестр — единственный источник «что подтверждено». Обновлять после
> каждого C1-теста (⬜→🟢), коммитить. Прогон целиком не нужен — гоняем зону находки, статус фиксируем в git.

---

## F. Мутационная проверка тестов (S18, 2026-08-11) — «а тест вообще ловит?»

> Зачем: зелёный тест доказывает что-то только если он **краснеет на сломанном коде**. Метод: откатываю фикс
> (или ослабляю защиту), гоняю тест-хозяина, восстанавливаю (`git checkout --`). Дерево после прогона чисто.
> Скрипт одноразовый (правило `quick`: создал → прогнал → удалил), результаты — здесь. Находки → `02` F54/F55.

| # | Что сломано | Файл | Тест-хозяин | Результат |
|---|---|---|---|---|
| M1 | `ToolContext.err` мимо реестра реакций (откат F43) | `tools/_context.py` | `test_audit_fixes` | 🔴 ловит |
| M2 | DEFAULT-fallback хардкодит message (откат F5) | `core/reactions/reactions.py` | `test_audit_fixes` | 🔴 ловит |
| M3 | `QUERY_NOT_FOUND` убран из реестра (откат F40) | `config/server_reactions.yaml` | `test_audit_fixes` | 🔴 ловит |
| M4 | типонебезопасный `_match_filter` (откат F42) | `core/search/query_planner.py` | `test_audit_fixes` 🔴 / **`test_search` 🟢** | частично — **слепа зона search** |
| M5 | `validate_formulas` без LO-пересчёта (откат F29) | `core/excel/excel_core.py` | `test_audit_fixes` | 🔴 ловит |
| M6 | санитайзер секретов выключен (откат D23/F11) | `core/contracts/error_detail.py` | `test_audit_fixes` | 🔴 ловит |
| M7 | containment путей снят (откат D1/G17) | `core/paths.py` | `test_audit_fixes` 🔴, `test_search` 🔴 / **`test_structure` 🟢** | **слепа зона structure** |
| M8 | детектор prompt-injection обнулён (IN2) | `core/firewall/rules/injection_detector.py` | `virus_injection` 🔴, `cache_injection` 🔴, `test_audit_fixes` 🔴 | ловят |
| M9 | дефолты rate-limit сняты (IN3, проводка) | `core/firewall/rules/rate_limiter.py` | **`bot_army` 🟢 22/22** | **слепа боевая конфигурация** |
| M9b | сам механизм rate-limit сломан | `core/firewall/rules/rate_limiter.py` | `bot_army` 🔴 15/22, `test_audit_fixes` 🔴 | ловят (механизм покрыт) |
| M10 | тихо изменён `description` инструмента | `tools/filesystem/__init__.py` | `test_tools_inventory` | 🔴 ловит (эталон+`--bless`) |
| M11 | `_valid_name` → `True` (обход через имя) | `core/engine/template_engine.py` | `test_structure` | 🔴 ловит |
| M12 | боевой `max_requests_per_minute: 10⁹` | `config/firewall.yaml` | 12 наборов → **только `test_audit_fixes` 🔴** | частично (D2-загрузка конфига) |
| M13 | боевой `injection_detection.enabled: false` | `config/firewall.yaml` | 12 наборов → **никто 🟢** | **ключ `enabled` код не читает — F54** |

**Итог: 11/14 мутаций пойманы.** Три пробоя (M4-search, M7-structure, M9-конфиг) + мёртвый выключатель (M13)
заведены как **F55** и **F54**. Зоны в §A/§B выше описывают ЖЕЛАЕМОЕ покрытие — расширять хозяев по F55:
`test_search` (+фильтры/сортировка), `test_structure` (+containment через `safe_resolve`, не только имя),
`bot_army` (+проверка боевых лимитов из `config/firewall.yaml`), любой (+auth `MCP_AUTH_TOKEN`).
