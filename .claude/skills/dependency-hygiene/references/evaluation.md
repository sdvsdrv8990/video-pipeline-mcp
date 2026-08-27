# Оценка пакета — детальный чек

> Разворачивает раздел «Оценка пакета» из `SKILL.md`. Источник структуры и API-примеров —
> `github.com/andrew/managing-dependencies` (CC0-1.0), сокращено и переведено под наш стек (pip).

---

## Метаданные одним запросом (любой реестр)

```bash
curl -s "https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages/<pkg>" | jq '{
  dependent_repos: .dependent_repos_count,
  dependent_packages: .dependent_packages_count,
  latest: .latest_release_number,
  latest_release: .latest_release_published_at,
  created: .first_release_published_at,
  maintainers: (.maintainers | length),
  repo: .repository_url,
  license: .normalized_licenses,
  advisories: (.advisories | length),
  archived: .repo_metadata.archived
}'
```

Локально, без сети наружу от нас: `.venv/bin/python -m pip index versions <pkg>` (существование +
доступные версии), `.venv/bin/python -m pip show -f <pkg>` (что уже стоит и чем тянется).

## Практики проекта (OpenSSF Scorecard)

```bash
scorecard --repo=github.com/owner/repo    # или securityscorecards.dev
```
Меньше 5 — повод посмотреть внимательнее; у маленьких и зрелых проектов низкий балл сам по себе
не приговор. `Maintained` или `Dangerous-Workflow` ниже 3 — реальный риск.

## Типосквоттинг: на что смотреть в имени

| Приём | Пример |
|---|---|
| Замена символа | `requets` vs `requests`, `djang0` vs `django` |
| Пропуск символа | `loadsh` vs `lodash` |
| Гомоглифы | `pyp1` (единица) vs `pypi`; кириллическая `а` vs латинская `a` |
| Разделители | `cross-env` / `crossenv` / `cross_env` |
| Комбосквоттинг | `lodash-js`, `axios-api`, `requests-utils` |

Отсюда правило из `SKILL.md`: имя копируется из официальной документации, а не набирается по памяти
и не берётся из ответа ИИ без проверки.

## Slopsquatting (выдуманные ИИ имена)

ИИ регулярно выдумывает имена пакетов, причём **повторяемо** — одни и те же выдумки от сессии к
сессии. Это делает их предсказуемой мишенью: злоумышленник регистрирует такое имя заранее.

Перед установкой пакета, который предложил ИИ (в том числе я):
1. `.venv/bin/python -m pip index versions <pkg>` — существует ли вообще;
2. возраст + число зависимых: очень новый и почти никем не используемый — под подозрением;
3. упомянут ли он в официальной документации того фреймворка, ради которого ставится;
4. прогнать имя по таблице типосквоттинга выше.

## Provenance

Аттестация происхождения (сборка из известного репозитория известным пайплайном) — хороший сигнал,
её отсутствие само по себе не приговор для старых пакетов. Для новых — повод не спешить.
