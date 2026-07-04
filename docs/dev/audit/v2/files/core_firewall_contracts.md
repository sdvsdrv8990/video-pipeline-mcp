# Q&A: core/firewall/contracts.py

> **Роль:** общий словарь файрвола — три модели (`FirewallDecision`/`FirewallRequest`/`FirewallResult`), которыми обмениваются фасад и правила. Существует, чтобы разорвать цикл импорта.
> **Сквозное:** [G3](../global.md#g3-firewall-перед-ядром) (firewall перед ядром); [G13](../global.md#g13-контракты-вынесены-для-разрыва-цикла-импорта) (контракты = лист-модуль, разрыв цикла; тот же приём, что `core/contracts/*`).
> **Статус кода:** реализован, стабилен; чистые stdlib-`@dataclass` + `Enum`, без зависимостей. Внутренний DTO — на провод MCP не выходит.
> **Аудит-линзы:** `mcp-developer` (осн. — контракты/проводка), `security-reviewer` (недоверенный вход на краю). Находки доказаны запуском на `.venv` Python.

## Решение 1: отдельный файл контрактов = разрыв цикла импорта
**Q:** почему модели живут не в `firewall.py` и не в `rules/`, а в третьем файле?
**A:** `rules/*` импортируют `FirewallRequest` из `contracts`, а `firewall.py` импортирует и `rules/*`, и `contracts`. Если типы положить в `firewall.py` — `rules → firewall → rules` даёт цикл. Вынос в лист-модуль (`contracts` ничего из файрвола не импортирует) разрывает его. Тот же приём, что `core/contracts/*` на уровне всего сервера → G-кандидат.
**Alt:** типы в `firewall.py` — отброшено (цикл); дублировать модели в каждом правиле — отброшено (рассинхрон).
**Регрессия:** если `contracts.py` начнёт импортировать что-то из `rules/`/`firewall.py` — цикл вернётся; модуль обязан оставаться листом графа.
**Связь:** потребители — [core_firewall_firewall.md · Решение 2](core_firewall_firewall.md), `rules/anomaly_detector.py:40`; порядок зависимостей задокументирован в docstring файла.

## Решение 2: `@dataclass`, а не Pydantic v2 (в отличие от `core/contracts/*`)
**Q:** проект стандартизован на Pydantic v2 для контрактов — почему тут stdlib-`@dataclass`?
**A:** это **внутренний DTO**, он никогда не пересекает провод MCP: `FirewallResult` на краю (`server.py:382`) конвертируется в JSON-RPC-ошибку, `FirewallRequest` собирается из уже-распарсенного тела. Схема/сериализация/`model_dump` не нужны → лёгкий `@dataclass` без оверхеда Pydantic на горячем пути (каждый запрос). Оправданно.
**Alt:** Pydantic-модель — дала бы валидацию, но лишний парсинг/валидацию на каждом запросе ради типов, которые и так не сериализуются наружу.
**Регрессия:** `@dataclass` **не валидирует** — `FirewallRequest(ip=123, method=None, params=[...], timestamp="nope")` принимается молча (доказано запуском). Пока безопасно: край (`server.py:367-371`) гардит типы (`isinstance(req_data, dict)`, `request.remote or "127.0.0.1"`). Если этот гард ослабнет — в правила поедут не-строки/не-dict → `TypeError` внутри правил (fail-closed через D10, но грязно). Не добавлять мутабельные дефолты (`params: dict = {}`) — классический shared-default-баг (сейчас его НЕТ, поля без дефолтов).
**Связь:** край и гарды — [../files/server.md] (`server.py:365-373`); контрактный envelope ядра (Pydantic) — [../files/core_contracts_tool_result.md].

## Решение 3: `FirewallResult` = {decision, reason, error_code}, `decision` обязателен
**Q:** какой минимум несёт результат проверки?
**A:** `decision: FirewallDecision` (обязателен) + `reason: str=""` (для лога/ответа) + `error_code: str=""` (машинный код исхода). ALLOW создаётся одним позиционным аргументом (`FirewallResult(FirewallDecision.ALLOW)`), reason/error_code пустые.
**Alt:** возвращать голый enum — отброшено (теряем причину для лога); бросать исключение на блок — отброшено (исход блокировки — штатный, не ошибка потока).
**Регрессия / находки:**
- **`error_code` — write-only мёртвое поле (D20).** Все 4 ветки `firewall.py` пишут различимые коды (`IP_BLOCKED`/`RATE_LIMIT_EXCEEDED`/`SECURITY_VIOLATION`/`ANOMALY_DETECTED`), но единственный потребитель `server.py:382-385` читает только `.reason`; `error_code` не доходит до Claude и не маппится в `server_reactions`. Доказано grep'ом (писатели только в `firewall.py`, читателей нет). → [../AUDIT.md#-d20](../AUDIT.md#-d20).
- **`decision` схлопывается у потребителя (D21).** `server.py:382` сравнивает `fw_result.decision.value != "allow"` — `BLOCK` и `RATE_LIMIT` дают идентичный `-32000 "Blocked:"` + HTTP 200 (доказано: оба `!= "allow" → True`). Замысел D6 (мягкий 429 vs жёсткий бан) на проводе невидим. Плюс stringly-сравнение вместо `!= FirewallDecision.ALLOW` (направление отказа безопасное: неизвестное → блок). → [../AUDIT.md#-d21](../AUDIT.md#-d21).
**Связь:** производитель — [core_firewall_firewall.md · Решение 3](core_firewall_firewall.md); реестр кодов — `config/server_reactions.yaml`.

## Решение 4: три поля `FirewallRequest` — все реально используются
**Q:** нет ли мёртвых полей во входной модели (как `error_code` в результате)?
**A:** нет. Проверено потребление: `ip` → ip_blocklist/rate_limiter/anomaly; `timestamp` → `rate_limiter.check(ip, timestamp)` и `anomaly_detector.py:89`; `method` → `anomaly_detector.py:95,98` (различает `tools/call`); `params` → injection + anomaly. Все 4 живы (grep-доказано).
**Alt:** передавать сырой `req_data: dict` — отброшено (правила зависели бы от формы JSON-RPC; типизированный DTO развязывает).
**Регрессия:** новое правило, которому нужен 5-й атрибут (напр. session/token для фикса [D14](../AUDIT.md#-d14)) → расширять `FirewallRequest` здесь; забыть прокинуть на краю (`server.py:367`) → поле молча дефолтное.
**Связь:** сборка запроса — `server.py:367-372`; фикс IP-гранулярности [D14](../AUDIT.md#-d14) приземлится как новое поле сессии/токена в этой же модели.

## Открытые вопросы файла
- **⚪ D20 (error_code мёртв):** либо пробросить `error_code` в ответ/`server_reactions`, либо удалить поле как несущее ложное обещание. Проверено запуском. См. [../AUDIT.md#-d20](../AUDIT.md#-d20).
- **🟡 D21 (RATE_LIMIT ≡ BLOCK на проводе):** потребитель `server.py:382` не различает мягкий лимит и жёсткий блок — Claude не может делать умный backoff на rate_limit. Обесценивает работу D6. См. [../AUDIT.md#-d21](../AUDIT.md#-d21).
- **Валидация края (не дефект, наблюдение):** `@dataclass` без валидации оправдан, пока `server.py` гардит типы; при доработке края это единственная точка защиты входной модели. Связка с [D10](../AUDIT.md#-d10) (fail-closed уже стоит).

## Что улучшить (регрессия-тесты, линза test-master)
- Тест-контракт: `FirewallResult(FirewallDecision.ALLOW)` → `reason==""`, `error_code==""`; каждая ветвь `firewall.check()` выставляет ожидаемый `error_code` (зафиксировать инвариант даже если поле пока не читается — чтобы фикс D20 не сломал коды).
- Тест на D21: замоканный `RATE_LIMIT`-результат на краю должен давать ответ, отличимый от `BLOCK` (после фикса) — набор `tests/bot_army`/`tests/rate_limit`.
- Тест «`contracts` — лист графа»: `import core.firewall.contracts` не тянет `firewall`/`rules` (страж от возврата цикла).
