# 15 — Программа развития СИСТЕМЫ защиты (воркстрим I6 + `core/firewall`)

> Разделение: [`06_threat_catalog.md`](06_threat_catalog.md) = **какие угрозы** (IN1–IN10 / OUT1–OUT8 / §F/§G/§H + приоритет P0–P2) ·
> [`14_testing_system_plan.md`](14_testing_system_plan.md) = **чем доказываем** · **этот документ = ЧТО и В КАКОМ ПОРЯДКЕ строим в самой защите**
> (этапы S0–S8 с приёмкой). Пара к `14`: у каждого этапа защиты есть тест-хозяин, иначе этап не закрывается.
> Исполнение — по блокам из [`16_execution_matrix.md`](16_execution_matrix.md) (фикс → тест → защита в одной сессии).

---

## 1. Факт защиты на 2026-08-11 (проверено чтением + мутациями S18)

**Что реально работает** (не декларация — подтверждено тем, что тесты краснеют при поломке):

| Слой | Состояние | Пруф |
|---|---|---|
| `core/firewall` — 4 правила в порядке IP-blocklist → rate → injection → anomaly | подключены, конфиг грузится (`create_server`), hot-reload **fail-closed** (битый конфиг → держим прежние правила) | мутации M8 (injection), M9b (rate) → наборы краснеют; D2-регрессия в `test_audit_fixes` |
| Containment путей `core/paths.safe_resolve` (G17 choke-point) | единая реализация, импортят все модули с путями; типизированный `PathEscapeError` | мутация M7 → `test_audit_fixes` + `test_search` краснеют |
| Bearer-auth (D3) | код есть с первого коммита: `compare_digest`, 401 **до** файрвола | `server.py:67/181` |
| Санитайзер секретов в `ErrorDetail` (D23) | `***REDACTED***` по ключам + вложенно | мутация M6 → красный |
| No-root инвариант (§G) | exec-sinks (`os.system`/`eval`/`exec`/`shell=True`/`pickle`) — **∅**; uid 1000; единственный subprocess = cloudflared arg-списком | grep ∅; bandit -ll в CI |
| Контракт инструментов (анти-rug-pull) | эталон `tools_inventory.golden.json` — тихая правка описания ловится | мутация M10 → красный |

**Чего нет** (каждая дыра = этап ниже):

| Дыра | F# | Суть |
|---|---|---|
| auth **fail-open** по умолчанию | F14 🔴 | `MCP_AUTH_TOKEN` не задан ⇒ auth молча выключена; нигде не выставляется; 0 тестов |
| конфиг **лжёт** | F54 🟠 | `enabled:` в трёх секциях `firewall.yaml` код не читает — выключатель мёртв |
| нет write-type allowlist | F34 🟠 | пишем любой тип файла (`.sh`/`.html`/`.exe`) — §F default-deny не построен |
| outbound = сплошной gap | F33 🔴 | нет провенанс-маркировки вывода (OUT1), сырой `Fact.data` в `_SESSION_LOG` (OUT6), containment/confirm на деструктиве не сплошной (OUT5), search-poisoning (OUT8) |
| origin-лимиты | F36 🟠 | per-IP rate бесполезен за туннелем (G18) → нужен identity-rate; нет slowloris-таймаутов, нет `client_max_size`, edge-настройки Cloudflare не зафиксированы |
| no-root — без регрессии | F35 🟠 | baseline чист, но теста «не root / нет exec-sinks» нет: держится на честном слове |
| красная команда | F32 🔴 | 36 паттернов в `patterns.yaml` без раннера — защита не проверяется под смесью honest+attacker |
| уязвимые зависимости | F47 🟠 | aiohttp/litellm/click/setuptools/torch/python-dotenv; pip-audit в CI = advisory |

⚠️ **Проектное ограничение (не нарушать):** `destructiveHint: true` **намеренно не назначен** ни одному
инструменту — он триггерит auth-гейт коннектора Claude.ai (`tools/_context.py:33`, память
`claude-ai-destructive-hint-auth-gate`). Значит OUT5 закрываем **не хинтом**, а серверными механизмами:
containment + `force`-подтверждение + allowlist + провенанс.

---

## 2. Целевая архитектура защиты

```
INBOUND (атакующий → сервер)
  edge (cloudflared): DDoS/WAF/bot — бесплатно always-on, настройки зафиксировать в доке
  ├── auth: fail-CLOSED, identity (не IP)          ← S1
  ├── firewall: IP → rate(identity) → injection → anomaly, все с ЖИВЫМ enabled  ← S0, S4
  ├── лимиты входа: client_max_size, таймауты, строгий JSON-RPC   ← S4
  └── containment путей: safe_resolve (есть) + write-type allowlist  ← S2

OUTBOUND (сервер → Claude AI Web + человек)
  ├── провенанс: workspace-контент помечен как НЕдоверенный, не эхоится в reason/message  ← S3
  ├── деструктив: containment + force-confirm (НЕ destructiveHint)  ← S3
  ├── контракт инструментов только из git (анти-rug-pull), reload не меняет tools/list  ← S6
  └── логи/аудит: без сырого Fact.data, без секретов  ← S3

ИЗОЛЯЦИЯ ХОСТА
  └── не root · нет shell/exec · NoNewPrivileges · cap-drop · read-only rootfs  ← S5

ПРОВЕРКА (иначе этап не закрыт)
  └── unit/contract (T0) · симуляции с чтением консоли (T2) · рой honest+attacker (T6=S7)
```

---

## 3. Этапы

| # | Этап | Что строим | Приёмка (тест-хозяин) | Приоритет `06 §D` | Сессий |
|---|---|---|---|---|---|
| **S0** | **Конфиг не лжёт** (F54) | `Firewall._make_rules` читает `enabled` у всех правил (выключено → правило не создаётся/no-op); документировать, что `patterns: []` — не единственный способ; выключатели — в `06` | новый тест «выключатель выключает»: `enabled:false` → поведение изменилось; мутация M13 из 🟢 в 🔴 | — (гигиена, дёшево) | 0.5 |
| **S1** | **Auth fail-CLOSED** (F14, P0) | нет `MCP_AUTH_TOKEN` ⇒ сервер **отказывается стартовать** в сетевом режиме (явный `MCP_ALLOW_ANONYMOUS=1` для локалки); `.env.example`; токен в `run.sh`; identity вместо IP как ключ rate-limit | тесты: без токена → 401 `AUTH_REQUIRED`; неверный → 401 `AUTH_FAILED`; верный → 200; старт без токена и без явного разрешения → отказ. Против **живого сервера** (нужен T2) | 🔴 P0 | 1 |
| **S1′** | OAuth 2.1 Resource Server (DIM-2 L3) | PKCE, scoped-токен на сервер, метаданные ресурса | conformance + live-тест потока | 🔴 P0 (после S1) | 2 |
| **S2** | **Write-type allowlist** (F34, §F, P0) | default-deny список **в конфиге** (не хардкод), единый choke-point на ВСЕХ путях записи (`fs_create_file`/`fs_write_file`/скрипты/материализация), код реакции `FILE_TYPE_FORBIDDEN` в реестре + recovery | `virus_injection` (его зона по CATALOG): `.sh`/`.html`/`.exe` → блок с кодом; `.json/.md/.xlsx/.yaml/.py` → проходят; медиа-типы включаются по фазам | 🔴 P0 | 1 |
| **S3** | **Outbound P0** (F33: OUT1/5/6/7) | провенанс-обёртка вокруг workspace-контента в `ToolResult` (маркер «данные, не инструкции») · не эхоить сырой контент в `reason`/`message` · `force`-подтверждение на delete/move + containment сплошняком · санитизация `Fact.data` перед `_SESSION_LOG` | сим-набор «сервер-как-атака»: инъекция в файле → в выводе помечена, не выполняется как инструкция; `fs_delete` без `force` → отказ; лог без сырых данных | 🔴 P0 | 1–2 |
| **S4** | **Origin-лимиты** (F36, P1) | identity-rate (ключ = принципал из S1, не IP) · `client_max_size` · таймауты чтения/заголовков (slowloris) · строгий JSON-RPC (reject CL+TE, лишние поля) · зафиксировать edge-настройки Cloudflare в `06 §H` как runbook | `bot_army` расширяется: смена IP при одном принципале **не** обходит лимит; тело > лимита → отказ; медленный клиент отваливается по таймауту | 🟠 P1 | 1 |
| **S5** | **No-root регрессия + deploy-hardening** (F35, §G.1) | тест-инвариант (uid≠0, нет exec-sinks, workspace не исполняется) + systemd/Docker baseline: `NoNewPrivileges`, cap-drop ALL, read-only rootfs, tmpfs, `--user 1000` | инвариант-тест краснеет, если кто-то добавит `os.system`/`shell=True`; bandit-гейт уже есть | 🔴 P0 (инвариант) / 🟠 (deploy) | 1 |
| **S6** | **Анти-rug-pull** (OUT2/OUT3, P1) | инвариант «описания и схемы инструментов — только из git»; hot-reload конфига **не меняет** `tools/list`; версия контракта в ответе | `test_tools_inventory` (эталон уже ловит тихую правку — мутация M10) + `config_change`: reload → инвентарь бит-в-бит прежний | 🟠 P1 | 0.5 |
| **S7** | **Красная команда** (F32) = этап **T6** тест-плана | раннер `agent_swarm` исполняет 36 паттернов: 5 честных + 10 inbound + 8 outbound + эскалация + cache/ddos | 4 критерия `03`: inbound блокирован с кодом · outbound не пробит · честные без ложных банов · эмерджентность | 🟠 P1 | 1–1.5 |
| **S8** | **Supply-chain** (F47) | бамп `aiohttp`→3.14.3 (транспорт — с регрессией на живом сервере), `python-dotenv`, `click`, `setuptools`, `torch`; pip-audit из advisory → хард-гейт | CI: pip-audit зелёный и обязательный; live-регрессия транспорта после бампа | 🟡 P2 | 1 |

**Порядок:** `S0` → `S1` → `S2` → `S3` → `S4` ∥ `S5` → `S6` → `S7` → `S8` → (`S1′` OAuth когда остальное стоит).
Обоснование: S0 дёшев и снимает ложное доверие к конфигу; S1 даёт **identity**, без которой S4 (identity-rate)
невозможен; S2/S3 — оба P0 и не зависят друг от друга; S7 (рой) идёт после того, как есть что проверять.

---

## 4. Правила, которые держим

- **Эмпирика вместо гипотез:** находка — только с воспроизведением (PoC, не дальше); `file:line` обязателен.
- **Проводка важнее наличия:** правило считается работающим, если ломается тест при его отключении
  (мутационная приёмка, `tests/CATALOG.md §F`) — иначе это D8 «мёртвое правило».
- **Fail-closed по умолчанию:** битый конфиг → прежние правила (уже так); нет токена → не стартуем (S1);
  неизвестный тип файла → отказ (S2). Тихая деградация в «разрешено» — дефект.
- **Декларативно:** allowlist, лимиты, паттерны — в `config/*.yaml`, не в коде (`anti-hardcode`).
- **Ошибки — через реестр реакций:** каждый новый отказ = код в `server_reactions.yaml` + recovery,
  не сырой текст (`reactions-errors`).
- **Не ломать коннектор:** `destructiveHint` не включаем (auth-гейт Claude.ai) — защита серверная.
- **Безопасность НЕ ищем на GitHub** (`08 §6` ступень B, исключение): механизмы берём из `06`.

---

## 5. Definition-of-Closed и зрелость

Этап защиты закрыт, когда: (1) механизм построен и **декларативен**; (2) есть тест, который **краснеет при
откате**; (3) симуляция с чтением консоли показывает наблюдаемый эффект (C2 из `08 §6`, требует T2).

| DIM (`07`) | Сегодня | Поднимают |
|---|---|---|
| DIM-2 auth | L0 | S1 → L2, S1′ → L3 |
| DIM-3 sec-inbound | L2 | S0+S4 → L3 |
| DIM-4 sec-outbound | L0 | S3 → L2, S7 → L3 |
| DIM-5 изоляция | L1 | S2 → L2, S5 → L3 |
| DIM-9 supply-chain | L1 | S8 → L2 |
