# Q&A: паттерны атак на MCP-серверы

> **Роль:** справочник угроз для security-аудита. Классификация по правам ИИ, механизмы обхода, источники.
> **Сквозное:** [G3](../global.md#g3-firewall-перед-ядром), [G12](../global.md#g12-эфемерное-in-process-состояние-файрвола), [G17](../global.md#g17-containment-workspace--единая-точка-а-не-проверка-в-каждом-хендлере), [G18](../global.md#g18-за-туннелем-клиент-один--гранулярность-по-ip-бессмысленна-секрет-уязвим).
> **Статус:** справочник актуален; security-дефекты отражены в AUDIT.md (D3 закрыт, D14/D29/D31).
> **Навигация:** `threat_landscape.md` → `audit/v2/AUDIT.md` §security → `audit/v2/files/core_firewall_*.md`.

## Решение 1: Классификация по правам доступа ИИ
**Q:** какие уровни прав и риски?
**A:**
| Уровень | Права | Риски |
|---|---|---|
| **L0** | Только генерация текста | Prompt injection, утечка промпта |
| **L1** | Чтение данных | Утечка данных, конфигов |
| **L2** | Запись данных | Модификация, бэкдоры |
| **L3** | Выполнение кода | Полная компрометация |
| **L4** | Системный доступ | Неограниченный ущерб |
**Наш сервер:** L1-L2 (чтение/запись данных, НЕ выполнение кода).
**Связь:** SESSIONS.md §threat_landscape.

## Решение 2: 4 категории атак
**Q:** какие вектора актуальны?
**A:**
**Категория 1: Без ИИ (чистый сервер)**
- Сетевые: DDoS, Slowloris, Port scanning, MITM
- Протокольные: JSON injection, Tool spoofing, Request forgery
- Транспорт: Tunnel hijacking, Session fixation, Replay attack

**Категория 2: С ИИ, без файловой системы**
- Prompt injection: direct, role hijacking, context manipulation, indirect
- Обход модерации: Base64, multi-turn, hypothetical framing, translation bypass
- Утечка информации: system prompt extraction, tool enumeration

**Категория 3: С ИИ + файловая система**
- Утечка данных: data exfiltration, config theft, log extraction
- Утечка секретов: credential theft, API key extraction, token extraction
- Скачивание: file download, archive creation, symlink abuse
- Вирусы: malicious file creation, remote code execution
- Модификация: data tampering, ransomware, code injection

**Категория 4: Продвинутые**
- Цепочки: recon→exploit, data staging→exfiltration
- Обход защит: multi-vector, slow and low, legitimate abuse
**Связь:** SESSIONS.md §threat_landscape.

## Решение 3: Механизмы обхода защит ИИ
**Q:** как обходят наши защиты?
**A:**
| Механизм | Как обходят | Пример |
|---|---|---|
| Content filter | Обфускация текста | Base64, Unicode, языки |
| Instruction hierarchy | Multi-turn manipulation | Расщепление на несколько сообщений |
| Path validation | Path traversal | "../../../etc/passwd" |
| IP-based limiting | Rotating proxies | 1000 IP по 1 запросу |
**Связь:** SESSIONS.md §threat_landscape, [D14](../AUDIT.md#-d14), [D29](../AUDIT.md#-d29).

## Решение 4: Источники для улучшения
**Q:** откуда брать новые вектора?
**A:**
| Источник | Что искать |
|---|---|
| GitHub Topics | mcp-security, ai-agent-security |
| OWASP | LLM Top 10, AI Security Guide |
| NIST | AI Risk Management Framework |
| Academic papers | Prompt injection, adversarial ML |
| CVE databases | AI/ML vulnerabilities |
| Security blogs | Real-world attacks, case studies |
**Связь:** SESSIONS.md §threat_landscape.

## Открытые вопросы файла
- **D14 (🟡):** IP-гранулярность бесполезна за туннелем.
- **D29 (🟠):** Path traversal через state_manager.
- **D31 (🟡):** Секрет туннеля в argv + yaml.
- **F7 (🔶):** Penetration testing — не проведён.

## Что улучшить
- Провести penetration testing (DDoS, injection, обход rate limiting).
- Добавить нормализацию текста в injection_detector (Base64, Unicode).
- Актуализировать whitelist паттернов по результатам тестов.
