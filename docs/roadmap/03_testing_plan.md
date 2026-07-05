# 03 — План тестирования (под специфику проекта)

> Опирается на скил `test-master` и особенности сервера: контракт `ToolResult`/`ErrorDetail`,
> коды `server_reactions`, файрвол-симуляции, MCP-сюрфейс. Исполнение — воркстрим `I7` (сквозной).

## Слои тестов

| Слой | Что покрывает | Инструмент | Статус |
|---|---|---|---|
| **Unit (in-process)** | контракты, engine, reactions, ids, tables/excel логика | pytest / скрипты | ✅ частично (`tests/quick/*`) но **tests/ в .gitignore** (F12) |
| **Contract** | каждый инструмент возвращает `ToolResult`; ошибка = `ErrorDetail` с кодом из реестра; parity-gap G14/D30 (facts/status не теряются на MCP-границе) | pytest + asserts | 🟠 частично |
| **Симуляции (adversarial)** | `virus_injection`, `cache_injection`, `cache_overflow`, `bot_army` против `core/firewall` | скрипты-симуляторы | 🟠 замысел в скиле, покрытие не подтверждено (F18) |
| **Регрессия D#** | каждый закрытый дефект = тест, который краснеет при откате (D1,D2,D4,D5,D6,D8,D9,D13 — есть в `test_audit_fixes`) | pytest | ✅ есть, расширять |
| **Reactions/errors** | все 5 классов / 13 кодов реакций мапятся; recovery присутствует; D27/D4 закрыты | pytest | 🔴 gap (F5) |
| **Security (inbound)** | path-traversal (D1/D29), injection, rate-limit-bypass, fail-open | симуляции + bandit | 🟠 D29 открыт (F15) |
| **Security (outbound)** | сервер-как-атака: prompt-injection через вывод инструмента, tool-poisoning, rug-pull, weaponized fs_delete/write/move | сценарные тесты | 🔴 gap |
| **Capability-aware / honest-stub** | стаб обязан КРИЧАТЬ (`NotImplementedError`), не фейкать success; xfail spec-тесты для P#-инструментов | pytest xfail | 🟠 провайдеры честны, spec-тестов нет |
| **Property / metamorphic** | инварианты по семействам (fs_*, table_*): напр. read∘write=identity | hypothesis | ⚪ будущее |
| **E2E (video pipeline)** | сквозной сценарий сборки видео | по мере P5–P7 | 🔴 продукта нет |
| **CI-gate** | всё выше на каждом PR + coverage-порог | GitHub Actions | 🔴 нет (F13) |

## Baseline (Сессия 1, 2026-07-05)

`tests/quick/` как скрипты: audit **30/30** · search **24/24** · structure **35/35** · tables **33/33** ·
firewall **1/4** (env: нужен живой сервер :8080) · tunnel **19/20** (env: cloudflared quick). Держать при рефакторах.

## Порядок построения (в рамках I7)

1. **I1 сначала** — разгитигнорить `tests/`, иначе CI не увидит тесты.
2. Ввести pytest как канонический раннер (сейчас часть тестов = запускаемые скрипты) + `conftest`.
3. Coverage-замер, порог в CI (стартовый, поднимать).
4. Закрыть reactions/outbound-security gaps (F5, outbound-слой) — самые опасные пустоты.
5. Каждый продукт-воркстрим (P#) приходит со своими unit+contract+honest-stub-тестами.

## Особенности, которые нельзя потерять

- **Симуляции — часть безопасности, не «e2e для галочки»**: они проверяют, что файрвол реально блокирует, а не «театр» (память: injection-театр уже снимали).
- **Reactions-as-contract**: коды реакций — публичный контракт для Claude; тест на каждый код + recovery.
- **Outbound = сервер атакует клиента**: уникальная для MCP поверхность; вывод инструмента как вектор prompt-injection обязателен в покрытии.
