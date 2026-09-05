#!/usr/bin/env bash
# vpm-skill-bundle.sh — SessionStart hook (project-gated), video_pipeline_mcp.
#
# Выкладывает в контекст сессии ЖИВОЙ список установленных скилов (читается с диска, поэтому
# не может назвать несуществующий) + адреса двух хабов: связки задач и ось «меньше своего кода».
# Не роутер: выбор скила остаётся нативным по description — хук лишь гарантирует, что роспись
# связок и точный роспись имён приезжают с первого хода, а не вспоминаются.
#
# Молча ничего не делает вне проекта.

# Корень — от места самого хука, а не зашит: зашитый путь молчит у всех, кроме автора.
PROJ_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Отметка в общем следе сторожей: без неё шелловый хук выглядит пылящимся у сторожа дисциплины,
# который судит по следу, а не по существованию файла. Ошибка отметки работы хука не касается.
python3 -c "
import sys
sys.path.insert(0, '$PROJ_ROOT/.claude/hooks')
try:
    import _trace; _trace.mark('vpm-skill-bundle.sh')
except Exception:
    pass
" 2>/dev/null || true
SKILLS_DIR="$PROJ_ROOT/.claude/skills"

case "$PWD" in
  "$PROJ_ROOT"|"$PROJ_ROOT"/*) : ;;
  *) exit 0 ;;
esac

[ -d "$SKILLS_DIR" ] || exit 0

python3 - "$SKILLS_DIR" "$PROJ_ROOT" <<'PY'
import json, re, sys
from pathlib import Path

skills_dir, proj = Path(sys.argv[1]), Path(sys.argv[2])

def head(text: str, limit: int = 95) -> str:
    """Первая мысль описания: до тире/точки, иначе обрезка. Полное description и так у модели."""
    first = re.split(r"\s+[—–]\s+|\.\s", text.strip(), maxsplit=1)[0]
    return (first[:limit] + "…") if len(first) > limit else first

rows = []
for skill in sorted(skills_dir.glob("*/SKILL.md")):
    fm = skill.read_text(encoding="utf-8", errors="replace").split("---")
    block = fm[1] if len(fm) > 2 else ""
    name = re.search(r"^name:\s*(.+)$", block, re.M)
    desc = re.search(r"^description:\s*(.+)$", block, re.M)
    if name:
        rows.append((name.group(1).strip(),
                     head(desc.group(1)) if desc else "⚠ БЕЗ description — нативно не сработает"))
    else:
        rows.append((f"⚠ {skill.parent.name}", "битый frontmatter: нет `name:` — скилл не подхватится"))

if not rows:
    sys.exit(0)

# Где сейчас работа: живой журнал, а не замороженная копия. Записи в файле лежат НЕ по порядку
# (блоки дописывались с разных концов), поэтому берём максимальный номер сессии, а не первую
# строку: иначе хук уверенно показывает позапрошлую работу — так и было с прежним vpm-хуком.
resume = ""
journal = proj / "docs/roadmap/_sessions.md"
if journal.exists():
    best = (-1, -1)
    for line in journal.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"###\s+Сессия\s+(\d+)(?:\s*\(доп\.\s*(\d+)\))?", line)
        if m:
            rank = (int(m.group(1)), int(m.group(2) or 0))
            if rank > best:
                best, resume = rank, line.lstrip("# ").strip()

# Точка возобновления ПРОГРАММЫ: первый незакрытый шаг журнала прохода. Журнал сессий говорит,
# что было; журнал прохода — что брать следующим, и именно его теряли при обрыве.
step = ""
passfile = proj / "docs/roadmap/18_full_pass.md"
if passfile.exists():
    for line in passfile.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("|") and ("⬜" in line or "🔨" in line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 3 and cells[0] and cells[0][0].isdigit():
                step = f"{cells[0]} — {cells[1]}"
                break

listing = "\n".join(f"- `{n}` — {d}" for n, d in rows)
ctx = f"""## Скилы проекта: что установлено ПРЯМО СЕЙЧАС ({len(rows)})

{listing}

Список прочитан с диска в этой сессии — имена точные. Скилл, которого здесь нет, звать нельзя.

**Связки по типам задач** — в скиле `engineering-questions`, раздел «Связки: какие скилы стакать».
Это единственная копия таблицы; в `CLAUDE.md` её больше нет (дубль расходился с реальностью).
Нетривиальная задача начинается с `/engineering-questions`: он даёт и вопросы, и связку.

**Директива владельца 2026-08-14 (цель работы):** поднять качество и УМЕНЬШИТЬ количество кода.
Механизм берём библиотечный, себе оставляем политику — ось 1a в `code-quality`. Перед тем как
писать обход под частный случай, проверь сигнатуру чужого API вызовом, а не памятью.

**Гейт — это СЕМЬ джоб `ci.yml`, а не один `pytest`.** Прогонять из `.venv` и смотреть exit-код каждой:
`lint` (`ruff check .` + `mypy`) · `test` (`pytest`) · `conformance` (`python tests/conformance/test_conformance.py`) ·
`security` (`bandit -r core server.py -ll` + `pip-audit`) · `comment-guard` (`python scripts/guards/comment_guard.py --check`) ·
`gitleaks`. Считать по `python3 -c "import yaml;print(list(yaml.safe_load(open('.github/workflows/ci.yml'))['jobs']))"`, а не по этой строке и не грепом (`push:` — триггер, а не джоба). Локально зелёное ещё не
зелёное в CI: там другая версия Python и установка `-e ".[dev]"` (лок не участвует), а весов моделей нет
вовсе — тест, которому они нужны, обязан пропускаться с причиной, а не падать.

**Три правила, которые стоили дороже всего (S24) — держать при ЛЮБОЙ задаче по коду:**
1. **Мутация выжила = тест не добивает до механизма**, а не «фикс лишний». Откат/компенсацию
   проверять падением на ВТОРОЙ операции: первая обязана успеть сделать то, что придётся отменять.
2. **Два источника одной правды работают ровно наполовину.** У декларации ищи ЧИТАТЕЛЯ; ноль
   вызывающих = мёртвая половина, а ревьюер читает именно её. Два списка, обязанных совпадать, —
   инвариант вешается тестом, а не дисциплиной.
3. **Утверждение документа проверяется ЗАПУСКОМ.** Доки говорили «12 групп» при восьми и «стабы»
   при мёртвом коде. Прочитал → `ls`/`grep`/прогон → и только потом опираешься.

Отказ (красный тест, `INTERNAL_ERROR`, «в CI иначе») начинается со `systematic-debugging`, поиск
по незнакомому месту — с `code-navigation`. Оба стоят в связках `engineering-questions`.

**Последняя запись журнала:** {resume or "(журнал пуст)"} — `docs/roadmap/_sessions.md`.
**Следующий шаг прохода:** {step or "(журнал прохода закрыт или пуст)"} — `docs/roadmap/18_full_pass.md`."""

print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": ctx,
}}))
PY
