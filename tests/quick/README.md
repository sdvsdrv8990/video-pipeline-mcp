# Быстрые тесты (quick/)

Это временныe тесты для быстрой проверки.

## Правила

1. **Создаём** когда нужно быстро проверить что-то
2. **Запускаем** и смотрим результат
3. **Удаляем** после проверки (не оставляем "на потом")

## Структура

```
tests/quick/
├── README.md            — этот файл
├── test_firewall.py     — файрвол (integration, нужен живой сервер)
├── test_audit_fixes.py  — регрессия по фиксам аудита D1–D13 (in-process)
├── test_tunnel.py       — туннель Cloudflare (offline: команды, готовность, супервизор)
└── ...
```

`test_audit_fixes.py` и `test_tunnel.py` — постоянная регрессия по исправлениям
аудита (`docs/dev/audit/`), не удалять. Запуск из корня проекта, exit code 0 = все прошли:

```bash
python3 tests/quick/test_audit_fixes.py   # 20/20
python3 tests/quick/test_tunnel.py        # 20/20 (без сети/домена)
```

## Как работает

```bash
# 1. Создаём тест
echo 'from core.firewall import Firewall; print("OK")' > tests/quick/test_import.py

# 2. Запускаем
python3 tests/quick/test_import.py

# 3. Удаляем
rm tests/quick/test_import.py
```

## Не путать с

- `tests/firewall/` — постоянный тест (живёт в проекте)
- `tests/quick/` — временный тест (удаляется после проверки)

## История

Создано: 2026-07-01
Цель: место для быстрых проверок без загромождения основных тестов
