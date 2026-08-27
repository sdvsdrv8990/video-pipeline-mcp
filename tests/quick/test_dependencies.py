"""
tests/quick/test_dependencies.py — объявленные зависимости и настоящие импорты обязаны сходиться.

Standalone-прогон:  python tests/quick/test_dependencies.py
Проверяет обе стороны: импорт мимо `pyproject.toml` (свежая установка не соберётся) и объявление,
которое не читает никто. Читателем считается не только `import`: `ruff`/`bandit` зовут КОМАНДОЙ из
`ci.yml`, а `setuptools` — строкой `[build-system]`.
"""
import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_PARTS = {".venv", "vendor", "__pycache__", "node_modules", ".git"}
RUNNERS = (".github/workflows/*.yml", "*.sh", "scripts/*.sh", ".pre-commit-config.yaml", "docker/*")

# Задники, которых не зовут ни импортом, ни командой: их тянет за собой другой пакет. Имя с
# причиной — это объявление; голое число было бы снимком, который молча стареет.
TRANSITIVE = {
    "accelerate": "diffusers/transformers при локальной генерации",
    "triton": "бэкенд torch на ROCm",
}

_checks = 0
_fails = []


def ok(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


def normal(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def bare(spec: str) -> str:
    for sep in (">=", "==", "<=", "~=", ">", "<", "[", ";", " "):
        spec = spec.split(sep)[0]
    return normal(spec)


def sources() -> list[Path]:
    return [p for p in ROOT.rglob("*.py") if not (SKIP_PARTS & set(p.parts))]


def own_names() -> set[str]:
    """Наши модули: сторожа подключаются по `sys.path`, каталогом верхнего уровня их не поймать."""
    return {p.stem for p in sources()} | {p.name for p in ROOT.iterdir() if p.is_dir()}


def imported() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for src in sources():
        try:
            tree = ast.parse(src.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    out.setdefault(alias.name.split(".")[0], set()).add(str(src.relative_to(ROOT)))
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                out.setdefault(node.module.split(".")[0], set()).add(str(src.relative_to(ROOT)))
    return out


def runner_text() -> str:
    text = ""
    for pattern in RUNNERS:
        for path in ROOT.glob(pattern):
            if path.is_file():
                text += path.read_text(encoding="utf-8", errors="ignore") + "\n"
    return text


def main() -> int:
    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = manifest["project"]
    declared = {bare(s) for s in project.get("dependencies", [])}
    declared |= {bare(s) for deps in (project.get("optional-dependencies") or {}).values() for s in deps}
    # Самоссылка (`имя[test,scripts]`) объединяет СВОИ же группы и никакого пакета не объявляет:
    # искать ей стороннего читателя значит обвинять механизм группировки.
    declared.discard(normal(project["name"]))
    build = {bare(s) for s in (manifest.get("build-system") or {}).get("requires", [])}

    mapping = {mod: {normal(d) for d in dists} for mod, dists in packages_distributions().items()}
    own, runners = own_names(), runner_text()
    third = {m: f for m, f in imported().items() if m not in own and m not in sys.stdlib_module_names}

    print("§1 импорт мимо объявления (свежая установка)")
    undeclared, undecided = [], []
    for module, files in sorted(third.items()):
        dists = mapping.get(module)
        if dists is None:
            # Пакета нет в этой среде: связать `import yaml` с `pyyaml` нечем. Обвинять по
            # совпадению имён нельзя — невиновным станет любой пакет, чей модуль зовётся иначе.
            if module not in declared:
                undecided.append(module)
            continue
        if not (dists & declared):
            undeclared.append(f"{module} ← {sorted(files)[0]}")
    ok(not undeclared, f"каждый сторонний импорт объявлен в pyproject; мимо: {undeclared or '—'}")
    if undecided:
        print(f"  ○ не проверено (пакета нет в этой среде): {', '.join(sorted(undecided))}")

    print("§2 объявление без читателя")
    read = {d for m in third for d in mapping.get(m, set())}
    orphan = []
    for dist in sorted(declared):
        if dist in read or dist in build or dist in TRANSITIVE:
            continue
        if re.search(rf"(?<![\w-]){re.escape(dist)}(?![\w-])", runners):
            continue
        orphan.append(dist)
    ok(not orphan, f"каждая объявленная зависимость кем-то читается; ничья: {orphan or '—'}")

    print("§3 заимствования у проекта объявлены и не устарели")
    runtime = {bare(s) for s in project.get("dependencies", [])}
    borrowed = (manifest.get("tool") or {}).get("vpm") or {}
    for zone, declared_names in (("tests", borrowed.get("borrowed_by_tests") or []),
                                 ("scripts", borrowed.get("borrowed_by_scripts") or [])):
        prefix = f"{zone}/"
        actual = {d for m, files in third.items() for d in mapping.get(m, set())
                  if d in runtime and any(f.startswith(prefix) for f in files)}
        said = {normal(n) for n in declared_names}
        ok(not (actual - said), f"[{zone}] незаявленное заимствование: {sorted(actual - said) or '—'}")
        ok(not (said - actual), f"[{zone}] объявлено, а больше не берут: {sorted(said - actual) or '—'}")

    print("§4 список задников не устарел")
    for name, why in sorted(TRANSITIVE.items()):
        ok(name in declared, f"`{name}` ({why}) всё ещё объявлен — иначе послабление стало мусором")
        ok(name not in read, f"`{name}` по-прежнему никем не импортируется — иначе послабление лишнее")

    print(f"\nПроверок: {_checks}, провалов: {len(_fails)}")
    for fail in _fails:
        print(f"  - {fail}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
