# Каталог тестов — зоны ответственности + правило «не плодить»

> Зачем: чтобы **развивать существующие** тесты, а не плодить. Каждый тест имеет ЗОНУ ОТВЕТСТВЕННОСТИ (что покрывает только он) и ЗАПАС РАСШИРЕНИЯ (что ещё впитает). Новый тест заводим ТОЛЬКО когда сценарий вне зон ВСЕХ существующих И запас хозяина-кандидата исчерпан.
>
> Полный тест-план (E-матрица, слои, приоритеты) — `docs/roadmap/03_testing_plan.md`. Угрозы для симуляций — `docs/roadmap/06_threat_catalog.md`. Скил — `test-master`.

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
> Статусы: ✅ подтверждён · ⬜ нужен C1-тест · 🔨 тест пишется · 🟢 регрессия зелёная после фикса.

| F# | Что | Метод | Тест-хозяин (не плодить) | Статус |
|---|---|---|---|---|
| **F43** | реестр обходится хендлерами → error без `reaction_class`/`recovery` | behavioral | `test_audit_fixes` (контракт ошибки: вызвать тул с ошибкой → assert error.structuredContent несёт class/recovery) | ⬜ нужен C1 |
| **F5** | DEFAULT-fallback хардкодит UNKNOWN_ERROR, роняет class, игнорит template | behavioral | `test_audit_fixes` (`Reactions.get_error("НЕИЗВЕСТНЫЙ")` → assert class/template) | ⬜ нужен C1 |
| **F40** | search-коды `QUERY_NOT_FOUND`/`PATH_NOT_FOUND` НЕ в реестре | behavioral | `test_search` (assert коды search ⊂ `server_reactions.yaml`) | ⬜ нужен C1 |
| **F42** | `_match_filter`/`_apply_sort` на разнотипном → TypeError | behavioral | `test_search` (фильтр str vs num → assert деградация, не краш) | ⬜ нужен C1 |
| **F28/F29** | delete/move_column ломает формулы молча; `validate_formulas`=театр | behavioral | `test_tables` (создать .xlsx с формулой → delete_column → assert `validate_formulas` НЕ ловит = красный) | ⬜ нужен C1 (нужен .xlsx с формулами) |
| **F37** | `_safe` ловит голый ValueError → всегда PATH_ESCAPE | behavioral | `test_audit_fixes` (core бросает не-путёвый ValueError → assert не PATH_ESCAPE) | ⬜ нужен C1 |
| **F11** | raw_response митигирован (D23-санитайзер) | behavioral | `test_audit_fixes` (ErrorDetail с секретом в raw_response → assert замаскирован) | ⬜ регрессия D23 |
| **F38** | мёртвый `_lock` в обоих search-классах | static | — (закрывается lint/vulture, I4) | ✅ подтверждён (grep) |
| **F39** | `QueryPlanner` лезет в приватный `table_engine._load` | static | — (архитектура, фикс A5) | ✅ подтверждён (чтение) |
| **F41/F46** | таксономия entity_type захардкожена 3× (search+schema+templates) | static→behavioral | `test_structure` (parity: три источника совпадают — metamorphic) | ✅ static; ⬜ parity-тест опционально |
| **F44** | повторные function-local импорты в ~15 хендлерах | static | — (lint, I4/A2) | ✅ подтверждён (grep) |
| **F45** | inline Python-skeleton (hardcode) | static | — (A2, вынести в template) | ✅ подтверждён (чтение) |
| **F10** | stt `device="cuda"` хардкод | static | — (config/anti-hardcode) | ✅ подтверждён (grep) |
| **F30** | `table_materializer` не построен (loader формул) | static | — (постройка A-tables/Ф3) | ✅ подтверждён (ls ∅) |
| **F3** | провайдеры = честные стабы (G16) | behavioral | `render_draft_final` (стаб → NotImplementedError-код, не фейк-success) | ✅ покрыт (стаб-контракт) |

**Приоритет C1 (behavioral, самые системные первыми):** F43 → F5 (ядро реакций, B2) · F40 → F42 (search) · F28/F29 (формулы) · F37 · F11. **static-находки** (F38/F39/F41/F44/F45/F10/F30) тестами не подтверждаются — закрываются lint (I4) или постройкой; в реестре помечены ✅ по обмеру.

> **Правило статусов (git-native):** этот реестр — единственный источник «что подтверждено». Обновлять после
> каждого C1-теста (⬜→🟢), коммитить. Прогон целиком не нужен — гоняем зону находки, статус фиксируем в git.
