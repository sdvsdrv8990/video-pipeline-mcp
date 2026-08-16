# Каталог тестов — зоны ответственности + правило «не плодить»

> Зачем: чтобы **развивать существующие** тесты, а не плодить. Каждый тест имеет ЗОНУ ОТВЕТСТВЕННОСТИ (что покрывает только он) и ЗАПАС РАСШИРЕНИЯ (что ещё впитает). Новый тест заводим ТОЛЬКО когда сценарий вне зон ВСЕХ существующих И запас хозяина-кандидата исчерпан.
>
> Полный тест-план (E-матрица, слои, приоритеты) — `docs/roadmap/03_testing_plan.md`. Угрозы для симуляций — `docs/roadmap/06_threat_catalog.md`. Скил — `test-master`.
>
> **Машинерия тестирования** (раннер, фикстуры, харнесс живого сервера, гейт, покрытие) — `docs/roadmap/14_testing_system_plan.md`, этапы T0–T10. **T0 закрыт (S18):** гейт = `pytest -m "not live"`, наборы находит `tests/test_suites.py` сам (новый файл попадает в CI без правки `ci.yml`); напрямую набор по-прежнему запускается скриптом (`python3 tests/<...>/test_<...>.py`, exit 0 = ok).

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
| `test_search.py` | `core/search` coverage+регрессия (FsSearcher/QueryPlanner, D36 traversal) **+ личность файлов из реестра и фильтры owner_id/chain_prefix (F60)** | search-контракт/поведение | relevance-eval (E-I), новые `search_*`, poisoning-outbound | search-качество как отдельный eval-слой перерастёт unit |
| `test_structure.py` | `TemplateEngine` Ф1 (depth-control, PATH_ESCAPE, ID) **+ система ID: таксономия из шаблонов, реестр связей, резолвер цепочки, ручная ветка ФС (S18-g/S18-h)** | структура/шаблоны/глубина/адресация | E-A/E-B/E-C эмуляция, `structure_link/migrate`, F25 reconcile | эмуляция «реальной работы ИИ» станет тяжёлой сценарной (→ dir) |
| `test_tables.py` | table/excel smoke-контракт (Кат.2+3) | инструменты таблиц отвечают контрактом | E-D деструктив, формулы/устойчивость (F30), `table_materializer` (Ф3) | деструктив-над-таблицами станет adversarial-симуляцией |
| `test_tunnel.py` | `core/transport/tunnel` парсер+автомат оффлайн (D11) | регрессия логики туннеля без cloudflared | новые режимы туннеля, форматы логов | — (узкая стабильная зона) |
| `test_providers.py` | **Провайдерский слой целиком**: реестр адаптеров, опись и установка моделей, спеки `model_specs.yaml`, расход, ключи канала, локальные адаптеры (piper/sd-turbo/onnx) | подъём модели и отказ провайдера — оба должны быть слышны | новые адаптеры, новые виды ресурса, спеки моделей | — расширяем; это дом P-оси |
| `test_uniqueness.py` | `core/uniqueness`: n-gram-слой, «тихий столбец» из данных канала, `readiness` full/partial/empty и `fragment_gaps` | «нет данных» не должно выглядеть как «ноль уникальности» | листы сцен, реакции-рекомендации A7 | — |
| `test_tools_inventory.py` | **Контракт инвентаря** `tools/list`: состав (сегодня 68, число живёт в эталоне, а не здесь) + group/title/description/annotations/input_schema против эталона `tools_inventory.golden.json` | структурные правки (A2-распил, переезды групп) не двигают контракт клиента молча | новые инструменты/группы (эталон обновляется `--bless`, diff виден в ревью) | никогда — это единый дом инвентаря; поведение инструмента проверяют тесты его зоны |

## B. Симуляции (adversarial / system)

| Набор | Зона ответственности | Зачем | Запас расширения (впитывает векторы каталога) | Новый рядом оправдан, если… |
|---|---|---|---|---|
| `bot_army/` | массовое подключение → rate-limit + ban (**IN3**) | армия ботов реальна | agent-swarm honest+attacker, **identity-rate**, **slowloris** (§H.1), anomaly (IN5) | — обычно расширяем; рой уходит в `agent_swarm/` |
| `cache_injection/` | injection-паттерны в данные/кеш (**IN2/IN8**) | отравление данных | **HTTP-smuggling/malformed/jsonrpc-abuse** (§H.2), cache-key poison (§H.3), deser (IN8) | протокол-атаки перерастут «инъекцию» тематически |
| `cache_overflow/` | устойчивость при переполнении (**IN4/IN7**) | сервер не падает, кеш чистится | **payload-overflow**, cache-**stampede** (§H.3), resource-quotas (DIM-11) | — |
| `config_change/` | адаптация к смене конфига + уведомление (hot-reload, **OUT3 rug-pull**) | конфиг защищён, клиент уведомлён | rug-pull tools/list (OUT3), secret-hygiene (IN10/D31) | — |
| `virus_injection/` | блокировка malware/payload (**IN2/IN6**) | вирус не проходит | **write-allowlist** forbidden-filetype (§F/F34), **no-root** exec-workspace (§G/F35), outbound injection-via-output (OUT1) | — расширяем; это дом «сервер-как-канал-вреда» inbound |
| `render_draft_final/` | media/pipeline e2e workflow (**сейчас стабы**) | сквозной рендер | P1–P7 когда провайдеры готовы; E-D формулы таблиц | продукт-пайплайн станет многошаговым (→ `pipeline/`) |

## C. Рой (декларация + раннер, S24)

| Артефакт | Зона ответственности | Статус |
|---|---|---|
| `agent_swarm/patterns.yaml` | **дом мульти-вариантных/эмерджентных сценариев**: N честных клиентов + N злоумышленников (inbound+outbound) в одном прогоне; 36 паттернов со `status` | ✅ реестр (до S24 не парсился вовсе — F85) |
| `agent_swarm/test_agent_swarm.py` | раннер: исполняет паттерны против ЖИВОГО сервера (харнесс T2), 4 фазы = 4 процесса (файрвол считает по IP — один прогон банил бы сам себя) | ✅ F32 закрыт S24. 28 защищено · 5 известных дыр · 1 «не достали» · 2 отложены |

**Правило роя (иначе он превращается в театр):** паттерн без исполнителя красит гейт, исполнитель без
паттерна — тоже; `xfail-spec` со СРАБОТАВШЕЙ защитой красит («статус устарел» — так нашлись 10 штук);
проба, не достающая до поверхности (калитка провайдера), — отдельный вердикт «НЕ ДОСТАЛИ», не зелёный.

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
| **F93** | обратный проход целостности не доставал до сгруппированной ветки: `F25` был зелёным только на неветвящемся дереве | behavioral | `quick/test_structure.py` (§40) | 🟢 **ЗАКРЫТ S24** (мутации M110/M110a → красный) |
| **F98** | эталон инвентаря фиксировал контракт «как есть»: свойство без типа и два без описания прошли молча | behavioral | `quick/test_tools_inventory.py` (8 инвариантов схемы) | 🟢 **ЗАКРЫТ S24** (мутация M109 → красный) |
| **F97** | `structuredContent: null` в проводе против схемы спеки — внешнего арбитра сериализации у нас нет | behavioral | `quick/test_firewall.py` (§4, живой сервер) | 🟢 **ЗАКРЫТ S24** (мутация M108 → красный) |
| **F96** | названный ребёнок чужого уровня исчезал из ответа: ИИ считал, что создал дерево | behavioral | `quick/test_structure.py` (§39) | 🟢 **ЗАКРЫТ S24** (мутация M107 → красный) |
| **F95** | объявленный `DUPLICATE_PATH` был недостижим: клиент получал `INTERNAL_ERROR` и «нужен человек» | behavioral | `quick/test_structure.py` (§38, через `engine.call`) | 🟢 **ЗАКРЫТ S24** (мутация M106 → красный) |
| **F94** | `parent_path` без слэша склеивался с именем: каталог-двойник рядом с контейнером, целостность зелёная | behavioral | `quick/test_structure.py` (§37) | 🟢 **ЗАКРЫТ S24** (мутация M105 → красный) |
| **F92** | `structure_migrate` уносил с диска поддерево, а в реестре правил путь только самой сущности | behavioral | `quick/test_structure.py` (§36) | 🟢 **ЗАКРЫТ S24** (мутация M104 → красный) |
| **F26** | на пустом реестре сервер молчал двумя пустыми списками вместо онбординга | behavioral | `quick/test_structure.py` (§17b) | 🟢 **ЗАКРЫТ S24** (мутация M103 → красный) |
| **F25** | целостность смотрела только реестр: каталог мимо сервера был невидим | behavioral | `quick/test_structure.py` (§17, обратный проход) | 🟢 **ЗАКРЫТ S24** (мутация M102 → красный) |
| **F55** | слепые зоны: `bot_army` не читал боевые лимиты, `test_search` не проверял фильтры/сортировку | behavioral | `bot_army/test_bot_army.py` (`test_shipped_config_is_enforced`) + `quick/test_search.py` (раздел фильтров) | 🟢 **ЗАКРЫТ S24** (мутации M99/M100 → красный) |
| **F90** | при `order: desc` нечисловые значения всплывали наверх — «топ» начинался с заглушек | behavioral | `quick/test_search.py` (сортировка в обе стороны) | 🟢 **ЗАКРЫТ S24** (мутация M101 → красный) |
| **F91** | набор провайдеров зависел от весов вне git — зелёным был только на одной машине | behavioral | `quick/test_providers.py` (пропуск с причиной) | 🟢 **ЗАКРЫТ S24** (пруф — красный CI-прогон) |
| **F32** | симуляции одновариантны — нет агентного роя (честные+атакующие в одном прогоне, outbound) | behavioral | `agent_swarm/test_agent_swarm.py` (34 исполнителя против живого сервера) | 🟢 **ЗАКРЫТ S24** |
| **F85** | `patterns.yaml` не парсился — «декларация», которую машина не читала | behavioral | `agent_swarm/test_agent_swarm.py` (грузит реестр первым действием; падение = красный гейт) | 🟢 **ЗАКРЫТ S24** |
| **F86** | сигнал аномалии немой: `reason` выбрасывался на ALLOW, `log_suspicious` — мёртвый knob | behavioral | `agent_swarm/test_agent_swarm.py` → `atk_anomaly_sequence` (след в консоли живого сервера) | 🟢 **ЗАКРЫТ S24** (мутация M98: `if False and …` → красный) |
| **F87** | провенанс не доехал до результатов поиска (остаток OUT8) | behavioral | `agent_swarm/test_agent_swarm.py` → `atk_search_poison` (объявлен `xfail-spec`) | 🟡 **OPEN-CONFIRMED S24** — дыра доказана живьём, ждёт фикса |
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
| **F68** | обход allowlist переименованием + исполняемое содержимое под «безопасным» расширением | behavioral | `test_structure` §32 (сигнатуры под чужим расширением, `.txt`→`.sh`/`.exe`, каркас скрипта, фрагменты) | 🟢 **регрессия ЗЕЛЁНАЯ** (мутации M44/M45/M46 краснеют) |
| **F72/F73** | шаблон проекта пишет мимо allowlist; `kind: config` тянет файл из-за пределов `config/` | behavioral | `test_structure` §34 (враждебный `evil.tpl.yaml`: `.sh`, shebang под `.md`, `source: ../.env`) | 🟢 **регрессия ЗЕЛЁНАЯ** (мутации M50/M51/M52 краснеют) |
| **F69** | `cryptography` не объявлена → подпись S9 молча выключена в чистом окружении | static | — (объявлена в `pyproject.toml`; `test_structure` §28 краснел в `.venv` без неё) | ✅ подтверждён (`ModuleNotFoundError` + grep по манифестам) |

**Статус C1 (S15) → фиксы (S16) — ВСЕ behavioral-находки ЗАКРЫТЫ:** A6 (F43·F5·F40) + A5-TypeError (F42) + A-tables (F29) переведены strict-xfail→регрессия. **`test_audit_fixes` 49/49, 0 OPEN** (S18: +5 проверок F54 «выключатель выключает»). F11 🟢 (D23). **static-находки** F38/F44 закрыты (I4); остаток F28/F37/F39/F41/F45/F10/F30 — эвидентны из чтения, закрываются A2/A-tables(F30)/anti-hardcode. Дальше — C2 симуляции+консоль (накоплены фиксы).

> **Правило статусов (git-native):** этот реестр — единственный источник «что подтверждено». Обновлять после
> каждого C1-теста (⬜→🟢), коммитить. Прогон целиком не нужен — гоняем зону находки, статус фиксируем в git.

---

## F. Мутационная проверка тестов (S18, 2026-08-11) — «а тест вообще ловит?»

> Зачем: зелёный тест доказывает что-то только если он **краснеет на сломанном коде**. Метод: откатываю фикс
> (или ослабляю защиту), гоняю тест-хозяина, восстанавливаю (`git checkout --`). Дерево после прогона чисто.
> Скрипт одноразовый (правило `quick`: создал → прогнал → удалил), результаты — здесь. Находки → `02` F54/F55.
>
> ⚠️ **Реестр отстал (сверено S24).** Таблица останавливается на `M57`, а мутации `M58`–`M98` (F20,
> F56, F28, F30, F59, A7.2, F77, F78, ключи провайдеров, F86) закрыты и описаны в `_sessions.md` и
> `02_findings.md`. То есть **источник истины по мутациям — сессии и реестр находок**, а этот раздел
> — исторический срез S18. Либо его дописывают в ту же сессию, что делает мутацию, либо с него
> снимается звание канона: реестр, который догоняют раз в 40 мутаций, вводит в заблуждение сильнее,
> чем его отсутствие.

| # | Что сломано | Файл | Тест-хозяин | Результат |
|---|---|---|---|---|
| M112 | ORPHAN-политика расширена до объявления предков (`channel`→ниша, `video`→канал) | `core/ids/link_registry.py` | `test_structure` §41 | 🔴 ловит 2 ассерта (S24) |
| M111 | в описание инструмента вписан `U+200B` — невидимая правка манифеста | `tools/tables/__init__.py` | `test_tools_inventory` | 🔴 ловит (S24) |
| M111a | описание несёт `<!-- … -->` — текст, скрытый от ревью, но видимый модели | `tools/tables/__init__.py` | `test_tools_inventory` | 🔴 ловит (S24) |
| M111b | описание несёт `SYSTEM: ignore previous instructions` | `tools/tables/__init__.py` | `test_tools_inventory` | 🔴 ловит (S24) |
| M111c | описание несёт внешний URL и `curl` — эксфильтрация подсказана манифестом | `tools/tables/__init__.py` | `test_tools_inventory` | 🔴 ловит (S24) |
| M111d | латинская `c` внутри русского слова — confusable-подмена имени/термина | `tools/tables/__init__.py` | `test_tools_inventory` | 🔴 ловит (S24) |
| M110 | токен `{parent:<тип>}` не раскрывается — сгруппированная ветка снова невидима (откат F93) | `core/ids/link_registry.py` | `test_structure` | 🔴 ловит (S24) |
| M110a | спуск только по прямым детям — пропущенный уровень иерархии снова невидим | `core/ids/link_registry.py` | `test_structure` | 🔴 ловит (S24) |
| M109 | снять описание у свойства схемы — контракт для клиента-LLM теряется (откат F98) | `tools/search/__init__.py` | `test_tools_inventory` | 🔴 ловит 2 ассерта (S24) |
| M108 | пустой `structuredContent` снова уезжает как `null` (откат F97) | `core/transport/transport.py` | `test_firewall` | 🔴 ловит 2 ассерта (S24) |
| M107 | `children_unfulfilled` всегда пуст — невыполненное снова молчит (откат F96) | `tools/structure/__init__.py` | `test_structure` | 🔴 ловит (S24) |
| M106 | регистрация узлов мимо `ctx.safe` — код реакции обезличивается (откат F95) | `tools/structure/__init__.py` | `test_structure` | 🔴 ловит 3 ассерта (S24) |
| M105 | нормализация контейнера снята: путь без слэша снова склеивается (откат F94) | `core/engine/template_engine.py` | `test_structure` | 🔴 ловит 3 ассерта (S24) |
| M104 | перенос правит путь только самой сущности, потомков в реестре не трогает (откат F92) | `core/ids/link_registry.py` | `test_structure` | 🔴 ловит 7 ассертов (S24) |
| M103 | холодный старт не срабатывает (откат F26) | `tools/structure/__init__.py` | `test_structure` | 🔴 ловит (S24) |
| M102 | обратный проход диск→реестр отключён (откат F25) | `core/ids/link_registry.py` | `test_structure` | 🔴 ловит (S24) |
| M99 | файрвол игнорирует объявленный порог (`max_requests=10⁹`) | `core/firewall/firewall.py` | `bot_army` | 🔴 ловит (S24) |
| M100 | `_match_filter` пропускает всё | `core/search/query_planner.py` | `test_search` | 🔴 ловит (S24) |
| M101 | чужеродные значения снова участвуют в развороте сортировки (откат F90) | `core/search/query_planner.py` | `test_search` | 🔴 ловит (S24) |
| M98 | сигнал аномалии снова выбрасывается на ALLOW-пути (откат F86) | `core/firewall/firewall.py` | `agent_swarm` | 🔴 ловит (S24) |
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
| M13 | боевой `injection_detection.enabled: false` | `config/firewall.yaml` | 12 наборов → **никто 🟢** | **было:** ключ `enabled` код не читал (F54). **S18: исправлено** — см. M14 |
| M14 | откат фикса F54 (`enabled` снова игнорируется) | `core/firewall/firewall.py` | `test_audit_fixes` (5 новых проверок) | 🔴 ловит ✅ |
| M15 | детектор injection обнулён — проверка САМОГО ГЕЙТА `pytest -m "not live"` | `core/firewall/rules/injection_detector.py` | `pytest` → 3 failed, 11 passed | 🔴 ловит ✅ (гейт T0 живой) |

| M16 | инвариант пути снят (второй ID на занятый каталог) | `core/ids/link_registry.py` | `test_structure` | 🔴 ловит ✅ |
| M17 | `check_integrity` не сверяет путь с диском (откат F65) | `core/ids/link_registry.py` | `test_structure` | 🔴 ловит ✅ |
| M18 | резолвер не берёт готовых предков (`find_by_path` → None) | `core/ids/chain_resolver.py` | `test_structure` | 🔴 ловит ✅ |
| M19 | `structure_create` снова с `parent_ids=None` (откат F63) | `tools/structure/__init__.py` | `test_structure` | 🔴 ловит ✅ (71/82) |
| M20 | молчаливое усыновление (снята блокировка `unresolved`) | `tools/structure/__init__.py` | `test_structure` | 🔴 ловит ✅ |
| M21 | перенос мимо реестра (откат F65) | `tools/filesystem/__init__.py` | `test_structure` | 🔴 ловит ✅ (77/82) |
| M22 | ручное создание без владельца (откат F61) | `tools/filesystem/__init__.py` | `test_structure` | 🔴 ловит ✅ |

| M23 | повторный `assign_id` плодит второй ID на путь | `tools/filesystem/__init__.py` | `test_structure` | 🔴 ловит ✅ |
| M24 | префикс файла мимо объявленного класса | `core/ids/taxonomy.py` | `test_structure` | 🔴 ловит ✅ (92/95) |

| M25 | подстановка `{parent:channel}` снята (откат F62) | `core/engine/template_engine.py` | `test_structure` | 🔴 ловит ✅ (103/107) |
| M26 | неоднозначность без списка кандидатов (откат F64) | `core/ids/link_registry.py` | `test_structure` | 🔴 ловит ✅ |

| M27 | личность файла не из реестра (откат F60) | `core/search/fs_searcher.py` | `test_search` | 🔴 ловит ✅ |
| M28 | `chain_prefix` игнорируется | `core/search/fs_searcher.py` | `test_search` | 🔴 ловит ✅ (32/35) |

| M29 | `structure_find` не фильтрует по тексту | `core/ids/link_registry.py` | `test_structure` | 🔴 ловит ✅ (117/122) |
| M30 | гигиена ярлыка снята (длина/переводы строк) | `core/ids/link_registry.py` | `test_structure` | 🔴 ловит ✅ |

| M31 | конверт провенанса снят (ярлык голой строкой) | `tools/structure/__init__.py` | `test_structure` | 🔴 ловит ✅ |
| M32 | пометка `instruction_like` отключена | `core/contracts/untrusted.py` | `test_structure` | 🔴 ловит ✅ (128/129) |
| M33 | детектор берёт свою копию паттернов вместо `firewall.yaml` | `tools/_context.py` | `test_structure` | 🔴 ловит ✅ (127/129) |

| M34 | пустой ожидаемый токен снова «открывает» сервер (откат F14 fail-open) | `core/auth.py` | `test_audit_fixes` | 🔴 ловит ✅ (62/63) |
| M35 | права `0600` на файле секрета не выставляются | `core/auth.py` | `test_audit_fixes` | 🔴 ловит ✅ (61/63) |
| M36 | `compare_digest` по строкам (не-ASCII токен → 500 вместо 401) | `core/auth.py` | `test_audit_fixes` | 🔴 ловит ✅ |

| M37 | защита записи снята: чужой инстанс пишет в реестр (откат S9) | `core/ids/link_registry.py` | `test_structure` | 🔴 ловит ✅ (144/147) |
| M38 | отпечаток машины не проверяется | `core/integrity.py` | `test_structure` | 🔴 ловит ✅ (146/147) |
| M39 | разрушающие инструменты выпали из `dangerous_tools` | `config/firewall.yaml` | `test_structure` | 🔴 ловит ✅ (146/147) |

| M40 | allowlist типов выключен (пишем `.sh`/`.exe`) | `core/write_policy.py` | `test_structure` | 🔴 ловит ✅ |
| M41 | удаление без подтверждения снова молчит | `tools/filesystem/__init__.py` | `test_structure` | 🔴 ловит ✅ |
| M42 | конверт содержимого без провенанса и пометки | `tools/filesystem/__init__.py` | `test_structure` | 🔴 ловит ✅ (170/172) |
| M43 | журнал пишет сырой `Fact.data` | `core/state/state_manager.py` | `test_structure` | 🔴 ловит ✅ (170/172) |

| M44 | проверка СОДЕРЖИМОГО снята (скрипт под видом .md проходит) | `core/write_policy.py` | `test_structure` | 🔴 ловит ✅ (10 провалов) |
| M45 | `fs_rename`/`fs_move` не проверяют тип цели (обход allowlist) | `tools/filesystem/__init__.py` | `test_structure` | 🔴 ловит ✅ (4 провала) |
| M46 | каркас скрипта пишет мимо единой двери (`description` от ИИ не проверяется) | `tools/filesystem/__init__.py` | `test_structure` | 🔴 ловит ✅ (2 провала) |

| M47 | `kind: config` игнорируется — копии конфига проекта нет | `core/engine/template_engine.py` | `test_structure` | 🔴 ловит ✅ (3 провала) |
| M48 | копия затирается серверным дефолтом при каждом проходе (правка проекта теряется) | `core/engine/template_engine.py` | `test_structure` | 🔴 ловит ✅ |
| M49 | нет дефолта на диске → выдуманный файл вместо честного пропуска | `core/engine/template_engine.py` | `test_structure` | 🔴 ловит ✅ (падением) |

| M50 | шаблон пишет `.sh` мимо allowlist (проверка типа снята) | `core/engine/template_engine.py` | `test_structure` §34 | 🔴 ловит ✅ (5 провалов) |
| M51 | shebang под `.md` из шаблона проходит (проверка содержимого снята) | `core/engine/template_engine.py` | `test_structure` §34 | 🔴 ловит ✅ (в тех же 5) |
| M52 | `kind: config` без containment — `source: ../.env` вычерпывает секрет сервера | `core/engine/template_engine.py` | `test_structure` §34 | 🔴 ловит ✅ (3 провала) |

| M53 | ротация снова печатает значение ключа (секрет оседает в логах) | `server.py` | `test_audit_fixes` | 🔴 ловит ✅ (static) |
| M54 | `.env` выпал из `.gitignore` (класс F70) | `.gitignore` | `test_audit_fixes` | 🔴 ловит ✅ |
| M55 | сравнение по значению, а не по хэшу (откат хранения-отпечатка) | `core/auth.py` | `test_audit_fixes` | 🔴 ловит ✅ (4 провала) |
| M56 | миграция оставляет открытое значение соседом с хэшем | `core/auth.py` | `test_audit_fixes` | 🔴 ловит ✅ |
| M57 | значение ключа печатается вне терминала (осядет в логе) | `server.py` | `test_audit_fixes` | 🔴 ловит ✅ (static) |

**Итог (обновлено S21): 53 мутации, поймано 39+.** первый прогон S18 дал 11/14. Три пробоя (M4-search, M7-structure, M9-конфиг) + мёртвый выключатель (M13)
заведены как **F55** и **F54**. Зоны в §A/§B выше описывают ЖЕЛАЕМОЕ покрытие — расширять хозяев по F55:
`test_search` (+фильтры/сортировка), `test_structure` (+containment через `safe_resolve`, не только имя),
`bot_army` (+проверка боевых лимитов из `config/firewall.yaml`), любой (+auth `MCP_AUTH_TOKEN`).
