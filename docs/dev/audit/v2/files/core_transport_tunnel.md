# Q&A: core/transport/tunnel.py

> **Роль:** управление cloudflared-туннелем (D11) — публичный HTTPS-доступ облачного Claude к локальному серверу, одной командой; событийная готовность, супервизор с backoff.
> **Сквозное:** [G11](../global.md#g11-поставщик-туннеля--cloudflare-tunnel-cloudflared) (осн. — cloudflared quick/named); [G7](../global.md#g7-транспорт--json-rpc-20-на-aiohttp) (форвардит на `127.0.0.1:port`); корень [G12]/[D14] (один IP туннеля).
> **Статус кода:** реализован, **самый зрелый файл аудита** (событийная готовность, супервизор, backoff+jitter, дренаж stdout). **D11 закрыт**. Гигиена секретов — [D31]. Архитектурный корень [D14]; усиливает [D3].
> **Навигация (знать не читая):** `core/transport/tunnel.py`. Поверхность: `CloudflaredTunnel(port,config_path)` — `start()->url`, `status()`, `stop()`, `public_url`; приватные `_build_command`/`_await_ready`/`_classify_line`/`_start_supervisor`/`_backoff_delay`. Секрет: `MCP_TUNNEL_TOKEN` (env) ∨ `cfg.tunnel_token`. Вызывается `server.py:411` (`--tunnel`).
> **Аудит-линзы:** security-reviewer (осн. — секреты/экспозиция), mcp-developer (проводка/изоляция). Находки доказаны запуском на `.venv`.

## Решение 1: cloudflared (quick+named), готовность по СОБЫТИЮ соединения (D11)
**Q:** как дать облачному Claude доступ к серверу за NAT одной командой?
**A:** дочерний `cloudflared`; `quick` (эфемерный `*.trycloudflare.com`, без аккаунта) / `named` (постоянный hostname). Готовность = событие `Registered tunnel connection` (для quick ещё и пойманный URL), НЕ таймер; провал = `fatal`-строка/выход процесса → `TunnelError` с реальным текстом cloudflared ([G5]).
**Alt:** ngrok (нестабильный URL) / frp (нужен VPS+TLS) — отброшены ([G11]); таймаут-готовность (v1.0) — отброшена (маскирует причину).
**Регрессия:** ядро о туннеле не знает (изоляция) — смена поставщика меняет только этот слой ([G11]).
**Связь:** [G11](../global.md#g11-поставщик-туннеля--cloudflare-tunnel-cloudflared), [D11 закрыт](../AUDIT.md); история v1.0/1.1.

## Решение 2: named — token ИЛИ credentials; env-приоритет, НО секрет-footgun (D31)
**Q:** как идентифицировать named-туннель и не закоммитить секрет?
**A:** два пути — connector-`--token` (ID+секрет) ИЛИ `tunnel_id/name`+`credentials_file`. Токен: `os.environ["MCP_TUNNEL_TOKEN"]` в приоритете над `cfg.tunnel_token` (комментарий: «чтобы не держать в коммитимом tunnel.yaml»).
**Регрессия / находка (доказано запуском + grep):**
- **argv-экспозиция:** `_build_command` → `[…,"run","--token","<TOKEN>"]` — токен в argv → виден в `ps`/`/proc/<pid>/cmdline` любому локальному юзеру (доказано: `SUPER_SECRET` в argv).
- **yaml-fallback footgun:** `config/tunnel.yaml` **трекается git** (не в `.gitignore`; `.gitignore` покрывает `*.cloudflared.json`/`.cloudflared/`, но не сам `tunnel.yaml`) и несёт поле `tunnel_token: ""`; код читает его как fallback → положив токен в yaml, юзер его закоммитит. Env-путь смягчает, но слот-в-репо остаётся.
**Почему важно:** утёкший connector-токен = захват туннеля (маршрутизация трафика/impersonation). `credentials_file` защищён (`*.cloudflared.json` в .gitignore) — а inline-`tunnel_token` нет.
**Как чинить (blast-radius = `_build_command`+`_spawn`):** передавать токен через `env` подпроцесса (`TUNNEL_TOKEN`), НЕ через argv; убрать `tunnel_token` из `tunnel.yaml` (только env) или добавить `config/tunnel.yaml` в `.gitignore` c committed-шаблоном `tunnel.yaml.example`.
**Связь:** [D31](../AUDIT.md#-d31), [G11](../global.md#g11-поставщик-туннеля--cloudflare-tunnel-cloudflared).

## Решение 3: супервизор — рестарт по смерти процесса + backoff (качество)
**Q:** как держать туннель живым, не упираясь в лимиты Cloudflare?
**A:** супервизор дренажит stdout (иначе буфер пайпа → зависание), обновляет статус соединения; рестарт cloudflared ТОЛЬКО при смерти процесса (транзиентные обрывы лечит сам cloudflared), backoff экспоненциальный + jitter + сброс на стабильном прогоне.
**Alt:** рестарт на любой обрыв / фиксированная пауза — отброшены (лимиты/шум).
**Регрессия / позитив:** пойман и покрыт тестом баг — `Unregistered tunnel connection` содержит `registered…` → ложный «connected»; фикс: проверка `disconnected` до `connected` + lookbehind `(?<!un)`. Эталон инженерной аккуратности.
**Связь:** `status()` → потребляется `server.py` (лог нездоровья раз в 60с); история v1.2.

## Решение 4: туннель = транспорт, не аутентификация — корень D14, усиливает D3
**Q:** даёт ли туннель безопасность?
**A:** нет — только достижимость. quick-URL публичен (эфемерный, но не секрет-барьер); весь трафик Claude приходит с ОДНОГО edge-IP на `127.0.0.1` → **корень [D14]** (IP-firewall/rate-по-IP бесполезны, [G12]) и почему нужна app-аутентификация ([D3], которой нет).
**Регрессия:** переход на quick вместо named → нестабильный URL (переобновлять коннектор); bind `127.0.0.1` ([D12]) корректно прячет сервер за туннель.
**Связь:** [D14](../AUDIT.md#-d14), [G12](../global.md#g12-эфемерное-in-process-состояние-файрвола), [D3](../AUDIT.md#-d3), [D12](../AUDIT.md#-d12).

## Открытые вопросы файла
- **🟡 D14 (корень single-IP):** этот файл — источник; общий фикс — гранулярность по сессии/токену ([G12]).
- **⚪ named не тест-прогнан вживую:** нет домена — history-гипотеза (совместимость с реальным Claude «Connect») ждёт проверки.
- **⚪ D31 (частично):** `--token` в argv (виден в `ps`) — ограничение cloudflared, не нашего кода. Env-приоритет + .gitignore закрывают основные риски.

## Что улучшить (тесты — security-reviewer / test-master)
- Тест D31 (после фикса): `_build_command(named,token)` НЕ содержит токен в argv; токен уходит в `env` подпроцесса; grep-страж отсутствия `tunnel_token` в трекаемых файлах.
- Тест парсера (страж v1.2-бага): `Unregistered…` → `disconnected`, не `connected`; `fatal`-маркеры → `TunnelError` с текстом.
- Тест backoff: стабильный прогон → attempts=1; флап → рост паузы до `retry_max`.
