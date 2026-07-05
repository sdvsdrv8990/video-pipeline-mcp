# 06 — Каталог угроз и митигаций (threat catalog)

> Восстанавливает отсутствующий `threat_landscape.md` (F16) и питает агентный adversarial-харнесс (F32, план `03_testing_plan.md`). Две стороны: **inbound** (атакующий → сервер) и **outbound** (скомпрометированный сервер → клиент Claude AI Web + человек). Каждая запись = вектор + наш surface + текущая защита + gap + **как чинить** + роль в рое агентов.
>
> Наш surface: `fs_*` (read/write/create/delete/move/rename/get_directory_tree) · `table_*` (get_row/get_column/append/set/delete/file) · `excel_*` (12 структурных) · `search_*` (quick/multi/tables) · `structure_*` (create/link/migrate/status/check_integrity) · `media_*` (ffmpeg/tts/stt/img — стабы P1–P4).
>
> Порядок firewall: IP-blocklist → rate → injection → anomaly (`core/firewall/firewall.py`). Ключевой контекст: **G18** — за туннелем один клиент, IP-гранулярность бесполезна, auth обязан быть app-level; **D3** (открыт) — нет app-level auth.
>
> Источники: наш `references/malicious-server-threats.md` (T1–T7); MCP-38 таксономия (arXiv 2603.18063); «When MCP Servers Attack» (2509.24272); NSA/CISA «MCP Security Design» (2026); OWASP LLM Top-10 2025 (LLM01 prompt injection, RAG/vector-poisoning); Willison «lethal trifecta». Адаптировано под наш код, не слепая копия.

---

## A. INBOUND — атакующий → сервер

| # | Вектор | Наш surface | Текущая защита / D# | Gap | Митигация | Роль в рое |
|---|---|---|---|---|---|---|
| IN1 | **Path traversal** (`../`, симлинки, абсолютные пути) | `fs_*`, `structure_migrate`, `table_file` | `core.paths.safe_resolve` (D1/D29 — read частично, containment) | запись/move/delete containment проверить сплошняком; симлинк-эскейп | canonical `resolve()`+`is_relative_to(workspace)` НА КАЖДОМ пути вкл. write/move/delete; отказ на симлинк наружу | atk-inbound: traversal |
| IN2 | **Injection в параметрах** (prompt-injection payload, control-символы) | все инструменты с строковыми параметрами | `injection_detector` (D7/D15) | паттерны актуализировать; unicode/ANSI-escape | detector подключён + свежие паттерны; нормализация unicode; отказ на управляющие последовательности | atk-inbound: injection |
| IN3 | **Rate-limit bypass / bot army** | транспорт `/mcp` | `rate_limiter` (D6/D14/D16), bot_army-сим | G18: IP-гранулярность за туннелем бесполезна; `ban_duration=0` footgun (D14) | rate по app-identity, не только IP; `ban_duration>0`; связать с app-auth (D3) | atk-inbound: rate-bypass |
| IN4 | **Cache-атаки** (переполнение, отравление кэша) | серверный кэш, `search_*` индекс | cache_overflow/cache_injection-симы → `MEMORY_EXHAUSTED` | подтвердить, что реально ловится (не театр) | квоты/таймауты/эвикция; санитизация ключей кэша | atk-inbound: cache-overflow |
| IN5 | **Anomaly / поведенческие** (аномальные последовательности) | firewall pipeline | `anomaly_detector` (D8 — был мёртвым, закрыт; D17–D19) | проверить проводку регрессией | правило реально вызывается (тест «подключено»); пороги из конфига | atk-inbound: anomaly |
| IN6 | **Command injection** (если появится shell в media/ffmpeg) | `media_*` (P1–P4, стабы) | стабы `NotImplementedError` (пока нет) | при реализации ffmpeg — риск shell | `execFile`/`subprocess` списком аргументов, без `shell=True`; allow-list кодеков из `render_config` | atk-inbound: cmd-injection (P-фаза) |
| IN7 | **Resource exhaustion** (CPU/диск/память через большие payload) | `fs_write`, `json_execute_queue`, `excel_*` | частично (cache) | размер входа/очереди без жёсткого лимита? | лимиты размера файла/очереди/книги; таймауты; backpressure | atk-inbound: resource |
| IN8 | **Insecure deserialization** (YAML/JSON payload) | конфиг hot-reload, `table_*` json | `yaml.safe_load` (проверить везде) | нет `yaml.load` без safe? | только `safe_load`/`json.loads`; схема-валидация | atk-inbound: deser |
| IN9 | **DNS-rebinding / Origin-bypass** на локальный сервер | транспорт (`server.py`) | bind `127.0.0.1` ok; Origin-проверка off-by-default (D12) | Origin omit-bypass | валидировать `Host`/`Origin` строго; app-auth (D3) | atk-inbound: transport |
| IN10 | **Секрет-эксфильтрация с сервера** (токен туннеля, креды) | `tunnel.yaml` (D31), env | gitignore tunnel.yaml; gitleaks | хардкод-креды в трекнутых файлах | секреты только из env/непубличных; gitleaks в CI; ротация | atk-inbound: secret-steal |

---

## B. OUTBOUND — сервер → клиент (Claude AI Web + человек)

> **Lethal trifecta** держится целиком на машине клиента: приватные данные (`fs_read`/`table_get_*`/`search_*`) + недоверенный `workspace/`-контент в контекст модели + действие (`fs_delete/write/move`, `json_execute_queue`, `structure_migrate`). Ремедиация = **маркировать провенанс**, НЕ «научить сервер распознавать вредные инструкции».

| # | Вектор | Наш surface | Текущая защита | Gap | Митигация | Роль в рое |
|---|---|---|---|---|---|---|
| OUT1 (T1) | **Indirect prompt injection через вывод** — payload в файле/ячейке/имени уходит в `ToolResult.data` | `fs_read`, `search_*`, `table_get_*`, `json_read_snapshot`, `fs_get_directory_tree` | нет | сырой workspace-контент = «доверенный вход» модели | поле `provenance:"workspace-untrusted"`; не эхоить контент в `reason`/`message`; изолировать от управляющего текста | atk-outbound: injection-via-output |
| OUT2 (T2) | **Tool poisoning описаний** — `description/title/enum` из данных, которые правит атакующий | `engine.register(...)`, схемы из `config/*`, шаблонов | описания статичны в git (проверить) | путь из workspace/hot-reload в описание? | инвариант: описания только из git-контролируемых деклараций, НИКОГДА из `workspace/` | atk-outbound: tool-poison |
| OUT3 (T3) | **Rug pull** — tools/list или их поведение меняются после одобрения | hot-reload (`firewall.yaml`, `server_reactions.yaml`), ephemeral-URL | hot-reload меняет только защитный конфиг (проверить) | reload меняет что-то видимое клиенту? | reload НЕ трогает контракт инструментов; смена tools/list → явное переуведомление | atk-outbound: rug-pull |
| OUT4 (T4) | **Cross-tool shadowing** — наш вывод адресует ЧУЖОЙ MCP (filesystem/git/gmail/drive параллельно) | текст выводов/описаний | нет | «возьми через filesystem-MCP, отправь через gmail» | не генерировать конструкции, адресующие чужие инструменты; namespace-изоляция | atk-outbound: shadowing |
| OUT5 (T5) | **Weaponized деструктив** — обманутая модель бьёт по данным клиента | `fs_delete` (`force=rmtree`), `fs_move/rename/write`, `json_execute_queue`, `structure_migrate` | `destructiveHint` gate (проверить на КАЖДОМ) | containment на write/move/delete; `force=true` без подтверждения | `destructiveHint:true` везде; containment `workspace/` на запись/move/delete; `force` под явное подтверждение | atk-outbound: weaponized-destruct |
| OUT6 (T6) | **Эксфильтрация через параметры** — приватное упаковано в аргумент/лог | `fs_move/write` (путь несёт данные), `_SESSION_LOG.md` (пишет `Fact.data`!), `search` query | нет | утечка приватного в лог/ответ через `Fact.data` | не писать сырые приватные данные в `_SESSION_LOG.md`; редакция полей; провенанс | atk-outbound: exfil-via-param |
| OUT7 (T7) | **Раскрытие через ошибки/`raw_response`** — секреты/пути/трассы в `ErrorDetail.message` | `_map_error` провайдеров (D23), стектрейсы | коды реакций (частично) | `raw_response` течёт в message | generic-сообщения; секреты/пути не в `ErrorDetail`; трассы только в лог | atk-outbound: error-leak |
| OUT8 | **RAG/vector-poisoning выдачи поиска** — отравленный документ поднимается `search_*` в контекст | `search_*` (питает выбор контекста, E-H/E-I) | нет | poisoned-doc в workspace → релевантен → в контекст | провенанс на результатах поиска; over-broad retrieval лимит; связка с E-I3 | atk-outbound: search-poison |

---

## C. Сквозные принципы (G-уровень)

- **Провенанс, не фильтр.** Весь `workspace/`-контент недоверен; сервер его МАРКИРУЕТ, не «распознаёт вредность» (OWASP: prompt injection не патчится, только defense-in-depth + сегрегация недоверенного). Кандидат в новый `G#`.
- **App-level auth обязателен** (D3/G18). За туннелем IP бессмыслен → identity на уровне приложения — фундамент для IN3/IN9 и любого rate/ban.
- **Containment `workspace/` на ВСЕ операции**, не только read (IN1 + OUT5). Один choke-point `core.paths.safe_resolve` (G17).
- **Деструктив под gate + подтверждение** (OUT5): `destructiveHint` + containment + `force`-confirm.

---

## D. Приоритет митигаций (что чинить, чтобы усилить защиту)

| Приоритет | Митигация | Закрывает | → воркстрим |
|---|---|---|---|
| 🔴 P0 | App-level auth (identity, не IP) | IN3, IN9, D3, G18 | I6 |
| 🔴 P0 | Провенанс-маркировка workspace-вывода + не эхоить в reason/message | OUT1, OUT6, OUT8 | I6, security |
| 🔴 P0 | Containment на write/move/delete + `destructiveHint` везде + `force`-confirm | IN1, OUT5 | I6 |
| 🔴 P0 | **Write-type allowlist (default-deny)** — §F | OUT5, малварь-ген, IN2/IN6 payload-drop | I6 |
| 🔴 P0 | **No-root инвариант** — §G (не исполнять workspace, нет shell, не root, bandit-gate) | эскалация привилегий, RCE через `.py`, IN6 | I6, I3 (CI) |
| 🟠 P1 | Подтвердить проводку firewall (anomaly/injection/cache реально ловят) | IN2, IN4, IN5 | I7 (agent-swarm) |
| 🟠 P1 | Инвариант «описания только из git» + reload не трогает контракт | OUT2, OUT3 | I6 |
| 🟠 P1 | Лимиты размера входа/очереди/книги + таймауты | IN7 | I6 |
| 🟡 P2 | generic-ошибки, секреты/пути не в `ErrorDetail`/лог | OUT7, IN10 | I3, I6 |
| 🟡 P2 | search-провенанс + retrieval-лимит | OUT8 | A5, E-I |

---

## F. Сокращение поверхности: write-type allowlist (default-deny, F34)

> Часть угроз (OUT5 weaponized-write, дроп малвари/payload'ов, генерация вредоносного ПО) закрывается **на уровне сервера**: физически запретить материализацию файлов, которые серверу НЕ нужны. Модель — **default-deny allowlist** (не blocklist): разрешён только известный узкий набор, всё прочее блокируется без перечисления.

**Легитимный словарь (из реального кода + намерения владельца):**
| Класс | Расширения | Кто пишет |
|---|---|---|
| данные/структура | `.json` `.md` `.xlsx` | шаблоны `structure_*`, `table_*` |
| конфиг | `.yaml` `.yml` | человек/скрипты |
| логика | `.py` | `scripts/` |
| медиа-ассеты (фаза-gated) | `.svg` audio/video/img | провайдеры media_* (P1–P4), в `assets/` — включать по фазам, НЕ раньше |

**Запрещено по умолчанию (серверу не нужны даже для заглушек):** `.css` `.jsx` `.tsx` `.js` `.html` `.sh` `.exe` `.bat` `.dll` и любое неперечисленное. React/веб-компоненты (`jsx/tsx/css/html/js`) — вне назначения сервера; `.yaml` — это конфиг/данные, НЕ React-компонент.

**Дизайн контроля:**
1. **Default-deny** — разрешаем перечисленное, режем остальное (не гоняемся за списком «плохих»).
2. **Декларативно** (anti-hardcode) — allowlist в конфиге (`config/firewall.yaml` или новый `config/write_policy.yaml`), НЕ `if ext in [...]` в коде; возможен per-context (данные workspace vs `scripts/`).
3. **Единый choke-point** (G17) — гард на ВСЕХ путях записи: `fs_create_file`, `fs_write_file`, `fs_create_project_structure`, материализация `structure_create` (`template_engine`), `json_execute_queue`, `structure_migrate`.
4. **Реакция** — блок → `ErrorDetail` код `FILE_TYPE_FORBIDDEN` в `server_reactions.yaml` + recovery «этот тип не разрешён; используй yaml/json/md/py».
5. Дополнительно к типу — **content-guard** для `.py`: РАЗРЕШЁН к записи, но сервер обращается с ним как с **инертными данными** — никогда не `exec`/`import`/`subprocess` содержимое `workspace/` (см. §G), запрет исполняемого бита.

**Что закрывает:** OUT5 (нельзя записать опасный тип), генерацию малвари (рой-атакующий «create virus» → блок), IN2/IN6 дроп payload'ов, cross-tool exfil-beacon (`.html`/`.js`).

---

## G. No-root / анти-эскалация привилегий (жёсткий инвариант, F35)

> Требование владельца: **через сервер физически нельзя получить root на машине клиента.** Не «сложно», а невозможно by design. Defense-in-depth: нет исполнения кода + нет shell + не от root + нет persistence-файлов.

**Эмпирический baseline (проверено 2026-07-05, ЧИСТО):** в коде инструментов НЕТ `os.system`/`eval`/`exec`/`pickle.load`/`__import__`/`shell=True`; сервер бежит не от root (uid 1000); единственный `subprocess` — `tunnel.py:270` спавнит `cloudflared` arg-**списком** (`self._build_command()`, без shell, фикс. бинарь). Позиция хорошая — задача НЕ регрессировать.

**Жёсткие инварианты (держать + проверять регрессией):**
| # | Инвариант | Как держим |
|---|---|---|
| G-1 | **Сервер НЕ исполняет контент `workspace/`** | `.py`/`.yaml`/`.json` = данные; никогда `exec`/`eval`/`import`/`subprocess` их. Делает `.py`-в-allowlist безопасным |
| G-2 | **Нет shell** | ни `os.system`/`os.popen`/`shell=True`; единственный subprocess (cloudflared) = arg-список + фикс. бинарь |
| G-3 | **Не от root, least privilege** | сервер бежит непривилегированным; при старте от root — drop privileges/отказ; нет `sudo`/`su`/setuid |
| G-4 | **Нет persistence/escalation-путей** | containment `workspace/` (IN1) + write-allowlist (§F) физически не дают писать в `~/.ssh/authorized_keys`, `~/.bashrc`, `/etc/`, cron, systemd, PATH-каталоги |
| G-5 | **Нет опасной десериализации** | только `yaml.safe_load`/`json.loads`; ни `pickle`, ни `yaml.load` |
| G-6 | **media/ffmpeg (P1–P4) при появлении** | subprocess только arg-списком + allowlist бинарей/кодеков из `render_config`; без shell; путь бинаря не из пользователя |

**Регрессия (CI + рой):** `bandit -r core/ server.py` красит новый exec-sink (G-1/G-2/G-5); тест «сервер не root» (G-3); атакующий-агент пишет `.py` и пытается заставить сервер его исполнить / дропнуть `authorized_keys` → блок (G-1/G-4); успех = ни один вектор не даёт исполнения/эскалации.

### G.1 — Проверенные практики изоляции (defense-in-depth, с источниками)

> G-1..G-6 — это уровень КОДА (не регрессировать). Ниже — уровень РАЗВЁРТЫВАНИЯ: реальные, отраслевые механизмы ОС/контейнера, которыми процесс сервера запирается так, что даже при компрометации он не эскалирует и не выходит из workspace. Не выдумка ИИ — индустриальные практики [S1–S7]. Наш плюс: транспорт HTTP за cloudflared, НЕ STDIO → мимо флага STDIO-command-exec (CVE-2025-49596, 200k уязвимых серверов [S7]).

| Слой | Механизм | Что даёт | Как применяем | Ист. |
|---|---|---|---|---|
| **Baseline контейнера** | `--cap-drop ALL` · `--security-opt no-new-privileges` · `--read-only` · `--tmpfs /tmp` · `--user 1000:1000` · `--pids-limit`/`--memory`/`--cpus` · `--network` ограничить | эталон «сервер как недоверенный код»: 0 привилегий, нельзя setuid-эскалация, rootfs immune к дропу веб-шеллов, лимит blast-radius | обернуть `run.sh` в контейнер с этим набором (эталон Anthropic Agent SDK) | [S5] |
| **systemd hardening** (bare-metal) | `NoNewPrivileges=yes` · `User=`/`DynamicUser=` · `CapabilityBoundingSet=` (пусто) · `SystemCallFilter=@system-service` · `ProtectSystem=strict` · `ProtectHome=yes` · `PrivateTmp` | то же на голом процессе без Docker; `systemd-analyze security` даёт балл (<3.0 = хорошо) | unit для сервера, аудит `systemd-analyze security` | [S1] |
| **Kernel: seccomp-bpf** | фильтр syscall: отклонять `execve` неожиданных бинарей, блок `ptrace`/`process_vm_readv`, запрет сетевых syscall если не нужны | режет RCE-примитивы и вынос памяти на уровне ядра | seccomp-профиль (Docker default −лишнее, либо кастом) | [S2][S4] |
| **Kernel: Landlock** | unprivileged FS-LSM: правила «что процесс МОЖЕТ читать/писать» с учётом путей (seccomp не видит путь аргумента) | FS-containment на уровне ядра — дополняет `safe_resolve` (workspace-only) в глубину | Landlock-правила: writable только `workspace/`, RO остальное | [S2][S6] |
| **Runtime-изоляция** (при исполнении недовер. кода — media P-фаза) | gVisor (I/O) / Kata Containers (выделенное ядро убирает kernel-escape) | если ffmpeg/провайдеры начнут гонять внешние бинари — изолировать от хост-ядра | media-фаза → провайдеры в gVisor/Kata | [S4][S5] |
| **Образ/цепочка** | non-root `USER`, digest-pinned образы, скан образа в CI (Falco/Trivy) | supply-chain + воспроизводимость | Dockerfile `USER 1000`, pin по digest, скан в CI | [S3][S5] |

**Порядок внедрения:** (1) non-root + `NoNewPrivileges` + cap-drop — дёшево, сразу; (2) FS-containment Landlock (writable=`workspace/`) — закрывает G-4 в глубину; (3) seccomp-профиль; (4) контейнеризация с Anthropic-baseline; (5) gVisor/Kata — только когда появится исполнение внешних бинарей (media). Каждый слой независим — стакаются.

**Источники [S#]:**
[S1] systemd hardening (`NoNewPrivileges`/`SystemCallFilter`/`ProtectSystem`, `systemd-analyze security`) · [S2] Landlock+seccomp для процесса, который контролируешь (arXiv Sandlock 2605.26298; Science-Gateways 2509.18548) · [S3] OWASP Docker Security Cheat Sheet · [S4] Docker seccomp/cap-drop default profile · [S5] Anthropic Agent SDK secure-deployment baseline (`cap-drop ALL`/`no-new-privileges`/`read-only`/gVisor/Kata) · [S6] bubblewrap/nsjail (для чужих бинарей) · [S7] Docker «MCP Security» + CVE-2025-49596 (STDIO-flaw, которого у нас нет). URL — в ответе сессии.

---

## H. Кэш / DDoS / манипуляция пакетами — практики защиты (F36)

> Реальные отраслевые практики [C1–C7], заземлённые на нашу архитектуру. **Ключевой факт: сервер за cloudflared → защита ДВУХУРОВНЕВАЯ.**

### H.0 — Двухуровневая модель (edge + origin)

Cloudflare-edge стоит ПЕРЕД туннелем; origin (наш сервер) не выставлен наружу (нет открытых портов), трафик проходит edge-фильтры и только потом доходит до туннеля [C4]. Что даёт edge **бесплатно и always-on**: L3/4 + **L7 DDoS**-защита (без конфига), WAF-рулсеты, rate-limiting-правила, Bot Fight Mode. Наша задача — (а) выставить edge-настройки правильно, (б) закрыть на origin то, что edge не видит (identity, целостность данных).

⚠️ **Caveat туннеля [C4]:** в Super Bot Fight Mode держать `Definitely Automated = Allow`, иначе туннель падает с `websocket: bad handshake`.

### H.1 — DDoS / L7

| Слой | Практика | Наш шаг | Ист. |
|---|---|---|---|
| **Edge** | DDoS managed rulesets = High sensitivity; L7 HTTP-протекция; rate-limiting-правила по пути/expression; Bot Fight Mode | включить/проверить в Cloudflare-панели (не код) | [C4] |
| **Origin: identity-rate** | per-IP бесполезен за туннелем (G18) → rate по app-identity | связать с app-auth (D3), `rate_limiter` (D6/D14/D16) на identity | [C2] |
| **Origin: low-and-slow / Slowloris** | таймауты чтения заголовков/тела, max одновременных соединений, backpressure | выставить read/header-timeout + conn-limit на ASGI/uvicorn | [C2] |
| **Origin: request coalescing** | дорогие операции (search) — один расчёт на N одинаковых запросов | коалесцинг для `search_*`/тяжёлых | [C1] |

L7-атаки не ловятся L3/4-инструментами (один GET неотличим от юзера на уровне пакета) → нужна **прикладная** интеллектуальность, не только per-IP [C2].

### H.2 — Манипуляция пакетами / протоколом

| Вектор | Практика | Наш шаг | Ист. |
|---|---|---|---|
| **HTTP request smuggling** (CL.TE/TE.CL/TE.TE) | строгий парсинг по стандарту; reject запросов с ОБОИМИ `Content-Length` и `Transfer-Encoding`; нормализация | edge CF нормализует [C4]; origin — доверять фреймворку с strict-parse, не самопал | [C3] |
| **Malformed packets** | не «прощать» кривые заголовки (лишние пробелы/дубли) — отклонять | reject на ASGI-уровне | [C3] |
| **Payload-переполнение** | жёсткий лимит размера тела/JSON-RPC, глубины JSON | `max_body_size` + лимит вложенности перед парсингом | [C3] |
| **JSON-RPC abuse / unicode** | валидация схемы JSON-RPC 2.0; нормализация unicode; отказ на `>0xFF` в заголовках | строгая проверка метода/params до firewall | [C3] |

### H.3 — Целостность кэша

**Текущее состояние (проверено):** реального кэша пользовательских данных НЕТ — единственный `template_engine._cache` (`template_engine.py:73`) держит тела шаблонов из git (доверенные, малый фикс. набор). Cache-поверхность сейчас мала (unbounded, но данные доверенные — ⚪).

**Если добавится кэш пользовательских данных (напр. индекс `search_*`) — обязательные практики [C1][C5]:**
- **Cache-key нормализация** — убрать вариативность запроса из ключа (снижает cache-poisoning); исключить атакер-контролируемое из ключа [C4].
- **Валидация входа ДО кэша** (не класть непроверенное); provenance (связка OUT1/OUT8).
- **Bounded + eviction** (`allkeys-lru`/`lfu`) при заполнении → `MEMORY_EXHAUSTED`; TTL.
- **Anti-stampede**: lock / request-coalescing / probabilistic early recompute при истечении hot-key [C1].
- **Кэшировать только идемпотентное** (GET/HEAD-семантика), никогда мутирующее [C5].
- Чеклист cache-уязвимостей — [C6].

### H.4 — Приоритет

| Приоритет | Практика | → |
|---|---|---|
| 🔴 P0 | Правильные edge-настройки (DDoS High, rate-rules, Bot-Fight caveat) | I6 (Cloudflare-панель) |
| 🔴 P0 | Origin: identity-rate (не IP) + slowloris-таймауты + payload-size-лимит | I6, D3/G18 |
| 🟠 P1 | Строгий JSON-RPC/протокол-парсинг, reject CL+TE | I6 |
| 🟡 P2 | Кэш-практики — при добавлении кэша пользовательских данных | A5/search |

**Источники [C#]:** [C1] cache stampede prevention (lock/coalescing/probabilistic) · [C2] L7/low-and-slow DDoS mitigation (Slowloris, app-layer intelligence, не только per-IP) · [C3] HTTP request smuggling + strict-parse/reject CL+TE (PortSwigger/HackTricks/Akamai CVE-2025-66373) · [C4] Cloudflare WAF/DDoS/tunnel best practices (edge-фильтр перед origin, Bot-Fight caveat, cache-key без query) · [C5] web caching strategies/eviction · [C6] Comprehensive Cache Vulnerabilities Checklist (GitHub) · [C7] OWASP. URL — в ответе сессии.

---

## E. Связь с агентным роем (F32) и E-матрицей

- Каждая строка `atk-inbound:*` / `atk-outbound:*` = **модель поведения агента-злоумышленника** в `tests/agent_swarm/`.
- Честные-агенты параллельно проверяют, что митигации не дают false-positive (баланс безопасность↔доступность, G18).
- IN-векторы наследуют сим-наборы: `bot_army`(IN3) · `virus_injection`(IN2/IN6) · `cache_*`(IN4).
- OUT-векторы — новый outbound-слой (сейчас 🔴 gap): каждый = сценарий «сервер-как-оружие», критерий = не пробивается.
- Каждая доказанная эксплуатация → `D#` (эмпирика, реальный payload → реальный вывод), регрессия в рой.
