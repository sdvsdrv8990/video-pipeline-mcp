#!/usr/bin/env python3
"""scripts/guards/_studio_surface.py — источник улики о поверхности студии (React/TS).

Отдаёт наблюдение, ничего не судит: кто объявлен компонентом, каким маркером он адресуется,
какие токены читает, какие пропсы объявил, какие теги нарисовал и откуда их взял. Судьёй работает
`acceptance_studio.py`; здесь только разбор текста дерева.

Разбор ТЕКСТОВЫЙ, и это объявленный предел: парсера TSX в питоне нет, а node в гейте не участвует.
Поэтому улику даём только там, где она ПАРНАЯ — тег ↔ его импорт, литерал ↔ токен того же рода,
проп ↔ его читатель. Одиночный литерал уликой не считается (`code-quality`: наивный счётчик
литералов на этой базе даёт шум, а не находки).
"""
import json
import posixpath
import re
from pathlib import Path

# Свойства CSS, значение которых обязано приходить из токена. Список — словарь РОДА значения:
# литерал становится уликой не потому, что он литерал, а потому, что у него есть объявленный дом.
STYLE_PROPS = ("padding", "margin", "gap", "font", "fontSize", "fontFamily", "fontWeight",
               "color", "background", "backgroundColor", "border", "borderRadius", "borderColor",
               "width", "height", "minWidth", "minHeight", "maxWidth", "maxHeight",
               "top", "left", "right", "bottom", "transitionDuration", "animationDuration",
               "lineHeight", "letterSpacing", "boxShadow")
STYLE_LITERAL = re.compile(
    r"\b(" + "|".join(STYLE_PROPS) + r")\s*:\s*(?P<quote>[\"'`])(?P<value>[^\"'`]*)(?P=quote)")
COMPONENT = re.compile(r"^export\s+(?:default\s+)?(?:function\s+(?P<fn>[A-Z]\w*)|"
                       r"const\s+(?P<const>[A-Z]\w*)\s*[:=])", re.M)
MARKER = re.compile(r'data-component=(?:"([^"]+)"|\{`([^`]+)`\})')
IMPORT_NAMES = re.compile(r"import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+[\"']([^\"']+)[\"']")
IMPORT_DEFAULT = re.compile(r"import\s+(?:type\s+)?([A-Za-z_]\w*)\s*(?:,|\s+from)")
# Тег, а не дженерик: `useState<Niche[]>` — то же начало, но прилеплено к имени, а после
# имени у дженерика идёт `[`/`>`, а у тега — пробел, `/` или `>` после атрибутов.
JSX_TAG = re.compile(r"(?<![\w\]])<([A-Z]\w*)(?=[\s/>])")
TOKEN_USE = re.compile(r"\btokens\.([\w.]+)")
EXPORTED = re.compile(r"^export\s+(?:default\s+)?(?:async\s+)?"
                      r"(?:function|const|let|var|type|interface|class|enum)\s+(\w+)", re.M)
PROPS = re.compile(r"^export\s+(?:default\s+)?(?:function\s+[A-Z]\w*|const\s+[A-Z]\w*\s*[:=][^(]*)"
                   r"\s*\(\s*\{(?P<props>[^}]*)\}", re.M)
INTERPOLATION = re.compile(r"\$\{[^}]*\}")
CODE_SUFFIX = (".tsx", ".jsx", ".ts", ".js")


def declared_tokens(text: str) -> dict[str, str]:
    """Пути объявленных токенов (`space.md`) → значение. Разбор по вложенности фигурных скобок."""
    out: dict[str, str] = {}
    path: list[str] = []
    for chunk in re.finditer(r"(\w+)\s*:\s*\{|\}|(\w+)\s*:\s*[\"']([^\"']*)[\"']", text):
        opened, leaf, value = chunk.group(1), chunk.group(2), chunk.group(3)
        if opened:
            path.append(opened)
        elif leaf:
            out[".".join(path + [leaf])] = value
        elif path:
            path.pop()
    return out


def _props_of(text: str) -> list[str]:
    found = PROPS.search(text)
    if not found:
        return []
    return [name.split(":")[0].split("=")[0].strip().lstrip(".")
            for name in found.group("props").split(",") if name.strip()]


def read(root: Path) -> dict:
    """Поверхность дерева `root`: компоненты, токены, чтение токенов, теги, пропсы, литералы."""
    files = sorted(p for p in root.rglob("*") if p.suffix in CODE_SUFFIX and p.is_file())
    tokens: dict[str, str] = {}
    for path in files:
        if path.stem == "tokens":
            tokens.update(declared_tokens(path.read_text(encoding="utf-8")))
    components: dict[str, dict] = {}
    modules: dict[str, dict] = {}
    token_use: dict[str, list[str]] = {}
    literals: list[dict] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(root))
        imported = {name.split(" as ")[-1].strip().removeprefix("type ").strip()
                    for group, _ in IMPORT_NAMES.findall(text)
                    for name in group.split(",") if name.strip()}
        imported |= set(IMPORT_DEFAULT.findall(text))
        modules[relative] = {
            "exports": sorted(set(EXPORTED.findall(text))),
            "imports": [{"names": [name.split(" as ")[0].strip().removeprefix("type ").strip()
                                   for name in group.split(",") if name.strip()],
                         "from": где} for group, где in IMPORT_NAMES.findall(text)],
        }
        for used in TOKEN_USE.findall(text):
            token_use.setdefault(used, []).append(relative)
        markers = [name or braced for name, braced in MARKER.findall(text)]
        for found in COMPONENT.finditer(text):
            name = found.group("fn") or found.group("const")
            body = text[found.start():]
            components[name] = {
                "file": relative,
                "line": text[:found.start()].count("\n") + 1,
                "markers": markers,
                "props": _props_of(body),
                "tags": sorted(set(JSX_TAG.findall(body)) - {name}),
                "imported": sorted(imported),
                "tokens": sorted(set(TOKEN_USE.findall(body))),
                "body": body,
            }
        if path.stem == "tokens":
            continue
        for found in STYLE_LITERAL.finditer(text):
            # Шаблонная строка из одних подстановок токена (`${tokens.space.xs} ${...}`) — это
            # чтение декларации, а не литерал: уликой остаётся ТОЛЬКО то, что осталось от неё
            # после выброса подстановок.
            остаток = INTERPOLATION.sub(" ", found.group("value")).strip(" /,;")
            if found.group("quote") == "`" and not остаток:
                continue
            literals.append({"file": relative, "line": text[:found.start()].count("\n") + 1,
                             "prop": found.group(1), "value": found.group("value")})
    return {"root": str(root), "files": [str(p.relative_to(root)) for p in files],
            "components": components, "modules": modules, "tokens": tokens,
            "token_use": token_use, "style_literals": literals}


def resolve(source: str, module: str) -> str | None:
    """Относительный импорт → путь внутри дерева. Внешний пакет (`react`) разрешению не подлежит."""
    if not module.startswith("."):
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(source), module))


def surface_json(root: Path) -> str:
    """Снимок ФОРМЫ, годный для храповика: тело компонентов в него не входит."""
    got = read(root)
    shot = {name: {"file": item["file"], "markers": sorted(set(item["markers"])),
                   "props": sorted(item["props"]), "tokens": item["tokens"]}
            for name, item in sorted(got["components"].items())}
    return json.dumps({"tokens": got["tokens"], "components": shot},
                      ensure_ascii=False, indent=2, sort_keys=True) + "\n"
