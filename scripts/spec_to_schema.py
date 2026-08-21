#!/usr/bin/env python3
"""
scripts/spec_to_schema.py — мост `spec/schemas/*.schema.md` → `config/templates/tables/*.schema.yaml`.

Спеки книг владелец вёл прозой с размеченными markdown-таблицами; целевой формат — YAML
(`docs/roadmap/spec/TABLE_SCHEMA_FORMAT.md`). Разбор механический, но НЕ безошибочный:
результат — черновик под вычитку, а не истина. Приёмка конвертера — регенерация уже
собранной руками `network_config` и сравнение с ней (`--verify`).

Использование:
    python3 scripts/spec_to_schema.py --verify                 # приёмка на эталоне
    python3 scripts/spec_to_schema.py --book channel_data      # черновик одной книги в stdout
    python3 scripts/spec_to_schema.py --all --write            # записать все недостающие
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = ROOT / "docs" / "roadmap" / "spec" / "schemas"
OUT_DIR = ROOT / "config" / "templates" / "tables"

# Уровень книги выводим из шаблонов структуры: кто объявил её как table_template.
TPL_DIR = ROOT / "config" / "templates" / "workspace"

# Заголовки листов в спеках разнородны: «Лист 3: `X`», «Лист 13: `X` (пояснение)»,
# «Листы 4–10: `A … B` (7 идентичных)». Имя берём из первых обратных кавычек, хвост — в описание.
# `[^\S\n]*` вместо `\s*`: иначе хвост заголовка перешагивает пустую строку и в описание
# листа затягивается первая строка тела («| Столбец | Тип | Флаг |»).
SHEET_RE = re.compile(r"^##\s*Лист[ыа]?\s*[\d–\-—, ]*:\s*`([^`]+)`[^\S\n]*(.*)$", re.M)
# Диапазон («A … B») и составные имена («VISUAL_/SCRIPT_/AUDIO_X») = несколько листов одной
# структуры. Разворачиваем ТОЛЬКО через блок «Дельты», где спека называет листы поимённо;
# без него имена середины диапазона нам взять неоткуда → предупреждение, а не догадка.
# Многоточие ищем во ВСЁМ заголовке: `A` … `B` (две пары кавычек) — тот же диапазон, что
# и `A … B`, и раньше он проходил немо, схлопываясь в один лист.
RANGE_RE = re.compile(r"[…]|\.\.\.")
# Блок поимённого перечисления листов группы: «**Дельты:**» / «**Дельты по листам:**».
DELTA_HEAD_RE = re.compile(r"\*\*Дельт[аы][^*]*\*\*")
DELTA_ITEM_RE = re.compile(r"^`([A-Z][A-Z0-9_]*)`\s*\(\d+\)\s*:\s*\+?\s*(.*)$")
# Имя столбца в спеках — snake_case строчными. Всё прочее («pct=a/b*100», проза) — не имя.
COL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Лист целиком вычисляемый: «весь Read-Only», «всё `F`» в шапке дашбордов.
SHEET_ALL_F_RE = re.compile(r"(?:вес[ьи]|вся|всё|все)\s+`F`|Read-Only", re.I)
# Одиночный столбец вне списка: «`type` = `F` (фикс.)» — имя, знак, флаг сразу за ним.
SOLO_COL_RE = re.compile(r"^\s*[=—–-]\s*`(?:id|W|F|fk)`")
# Ограничения Excel на имя листа — нарушение здесь ронял бы материализацию книги.
EXCEL_BAD_CHARS = set(r"\/*?:[]")
EXCEL_NAME_LIMIT = 31
ROW_RE = re.compile(r"^\|\s*(`[^|]+`)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*(.*?)\s*\|?\s*$")
NAME_RE = re.compile(r"`([^`]+)`")
TOKEN_RE = re.compile(r"\b([A-Z][A-Z0-9_]{1,})\b")
FORMULA_RE = re.compile(r"`(=[^`]+)`")

# Тип в спеке бывает союзным («integer/str») — берём строку как самый широкий.
TYPE_MAP = {"integer/str": "string", "int": "integer", "str": "string",
            "bool": "boolean", "float": "float", "date": "date", "datetime": "datetime"}
KNOWN_FLAGS = {"id", "W", "F", "fk"}


def yq(value: str) -> str:
    """Безопасный YAML-скаляр для flow-стиля: одинарные кавычки с удвоением внутренних.

    Формулы содержат двойные кавычки (`=IF(x>=4,"OK","ROTATE")`), поэтому обернуть в них нельзя.
    """
    return "'" + str(value).replace("'", "''") + "'"


def extract_enum(note: str) -> list[str] | None:
    """Значения enum из «Прим.»: части через `/`, в каждой — ВЕРХНИЙ_РЕГИСТР-токен.

    Пояснения в скобках допустимы: `HARD (сервер блокирует) / SOFT (рекомендация ИИ)`.
    Каждая часть обязана дать токен — иначе это не список значений (напр. формула `=likes/views`).
    """
    parts = [p for p in (note or "").split("/")]
    if len(parts) < 2:
        return None
    vals = []
    for part in parts:
        m = TOKEN_RE.search(part)
        if not m:
            return None
        vals.append(m.group(1))
    return vals


def book_levels() -> dict:
    """book → уровень сущности, по объявлениям `table_template` в шаблонах структуры."""
    import yaml
    levels = {}
    for tpl in sorted(TPL_DIR.glob("*.tpl.yaml")):
        node = tpl.name.replace(".tpl.yaml", "")
        body = (yaml.safe_load(tpl.read_text(encoding="utf-8")) or {}).get(node) or {}
        for fr in body.get("files", []):
            if fr.get("kind") == "table" and fr.get("table_template"):
                levels[fr["table_template"]] = node
    return levels


def norm_type(raw: str, note: str) -> str:
    t = (raw or "").strip().strip("`").lower()
    t = TYPE_MAP.get(t, t)
    if not t:
        t = "string"
    # «enum» иногда стоит во «Флаг», а тип пуст — распознаём по списку значений в примечании.
    if t not in {"string", "integer", "float", "boolean", "date", "datetime", "enum"}:
        t = "enum" if extract_enum(note) else "string"
    return t


def parse_row(line: str) -> list[dict] | None:
    """Строку таблицы → список колонок (в одной строке спеки бывает несколько имён)."""
    m = ROW_RE.match(line)
    if not m:
        return None
    names_cell, type_cell, flag_cell, note = m.groups()
    names = NAME_RE.findall(names_cell)
    if not names:
        return None
    flag = (flag_cell or "").strip().strip("`")
    if flag not in KNOWN_FLAGS:
        flag = "W"                      # не объявлено — консервативно «правит человек»
    ctype = norm_type(type_cell, note)
    cols = []
    for name in names:
        col: dict = {"name": name, "type": ctype, "flag": flag}
        if ctype == "enum":
            vals = extract_enum(note)
            if vals:
                col["enum"] = vals
        if flag == "F":
            fm = FORMULA_RE.search(note or "")
            if fm:
                col["formula"] = fm.group(1)
        if note and note.strip():
            col["_note"] = re.sub(r"\s+", " ", note.strip())[:120]
        cols.append(col)
    return cols


def split_items(body: str) -> list[tuple[str, str]]:
    """Список столбцов из прозы → пары (имя, аннотация). Запятые внутри скобок не разделяют.

    Скобки бывают вложенными: `pace_wpm (F: word_count/(duration/60))`.
    """
    items, depth, cur = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    out = []
    for raw in items:
        raw = re.sub(r"\s+", " ", raw.strip().strip("`*")).strip()
        if not raw:
            continue
        m = re.match(r"^([^\s(]+)\s*(?:\((.*)\))?$", raw)
        if not m:
            out.append((raw, ""))
            continue
        out.append((m.group(1), (m.group(2) or "").strip()))
    return out


def col_from_prose(name: str, ann: str, default_flag: str) -> dict | None:
    """Столбец из прозы: флаг и enum берём из аннотации в скобках, тип спекой не объявлен."""
    if not COL_NAME_RE.match(name):
        return None
    flag, note = default_flag, ann
    ctype = "string"
    if ann == "id":
        flag, note = "id", ""
    elif ann.startswith("fk"):
        flag, note = "fk", ann[2:].strip()
    elif ann == "enum":
        ctype, note = "enum", ""
    elif re.match(r"^F\b", ann):
        flag, note = "F", ann[1:].lstrip(": ").strip()
    else:
        vals = extract_enum(ann)
        if vals:
            ctype, note = "enum", ""
            col = {"name": name, "type": ctype, "flag": flag, "enum": vals}
            return col
    col: dict = {"name": name, "type": ctype, "flag": flag}
    if note:
        col["_note"] = note[:120]
    return col


def clause_flag(tail: str) -> str | None:
    """Флаг, объявленный ПОСЛЕ списка: «— `W`;», «— всё `W`.», «— кроме помеченных `F`, всё `W`».

    Берём ПОСЛЕДНИЙ флаг в предложении: в «кроме помеченных `F`, всё `W`» правило — `W`,
    а `F` — оговорка про уже помеченные столбцы.
    """
    clause = re.split(r"[;\n]", tail, maxsplit=1)[0]
    flags = [f for f in NAME_RE.findall(clause) if f in KNOWN_FLAGS]
    return flags[-1] if flags else None


def parse_prose(text: str, solo_ok: bool = False, flag: str | None = None) -> tuple[list[dict], list[str]]:
    """Столбцы из прозаической формы спеки → (столбцы, сколько токенов отброшено как не-имена).

    Прозаическая форма: список имён в обратных кавычках через запятую, флаг — после списка
    или один на весь лист («дашборд, весь Read-Only»). Тип спекой в этой форме НЕ объявлен.
    Отброшенные токены возвращаем поимённо: это адрес для вычитки, а не счётчик.
    `solo_ok` — контекст, где имя без запятой заведомо столбец (блок «Дельты»: `+ `visual_solution`.`).
    """
    default_flag = flag or ("F" if SHEET_ALL_F_RE.search(text) else "W")
    cols, dropped = [], []
    for m in re.finditer(r"`([^`]+)`", text):
        body = m.group(1)
        if body.lstrip().startswith("="):
            continue                                     # формула, не столбец
        tail = text[m.end():m.end() + 200]
        if "," not in body and not solo_ok:
            # Одиночное имя берём, только если сразу за ним объявлен флаг: «`type` = `F`».
            # Без этого условия любое слово в кавычках внутри прозы стало бы столбцом.
            if not SOLO_COL_RE.match(tail):
                continue
        col_flag = clause_flag(tail) or default_flag
        for name, ann in split_items(body):
            col = col_from_prose(name, ann, col_flag)
            if col is None:
                dropped.append(name)
            else:
                cols.append(col)
    return cols, dropped


def parse_deltas(block: str) -> list[tuple[str, str]]:
    """Блок «Дельты» → [(имя листа, текст с добавочными столбцами)]. Пункты бывают многострочными."""
    bullets, cur = [], None
    for line in block.splitlines():
        if re.match(r"^\s*[-*]\s+`[A-Z]", line):
            if cur:
                bullets.append(cur)
            cur = re.sub(r"^\s*[-*]\s+", "", line).strip()
        elif cur is not None and line.strip() and not line.startswith("#"):
            cur += " " + line.strip()
        elif cur is not None:
            bullets.append(cur)
            cur = None
    if cur:
        bullets.append(cur)
    out = []
    for b in bullets:
        m = DELTA_ITEM_RE.match(b)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def dedup(cols: list[dict]) -> tuple[list[dict], list[str]]:
    """Одно имя столбца на лист: секции дашбордов повторяют `trend`/`rotation_health`."""
    seen, out, dups = set(), [], []
    for c in cols:
        if c["name"] in seen:
            dups.append(c["name"])
            continue
        seen.add(c["name"])
        out.append(c)
    return out, dups


def parse_spec(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    book = path.name.replace(".schema.md", "")
    bounds = [(m.start(), m.group(1), (m.group(2) or "").strip().lstrip("—").strip())
              for m in SHEET_RE.finditer(text)]
    sheets: list[dict] = []
    warnings: list[str] = []
    for i, (start, sheet_name, desc) in enumerate(bounds):
        end = bounds[i + 1][0] if i + 1 < len(bounds) else len(text)
        section = text[start:end]
        # Группа листов: разделяем «общую часть» и поимённые дельты.
        dm = DELTA_HEAD_RE.search(section)
        head = section[:dm.start()] if dm else section
        deltas = parse_deltas(section[dm.end():]) if dm else []

        cols: list[dict] = []
        for line in head.splitlines():
            if line.startswith("| Столбец") or set(line.strip()) <= set("|- "):
                continue
            parsed = parse_row(line)
            if parsed:
                cols.extend(parsed)
        from_prose = False
        dropped: list[str] = []
        if not cols:                      # табличной формы нет — пробуем прозаическую
            cols, dropped = parse_prose(head)
            from_prose = bool(cols)

        # Диапазон ищем во всём заголовке: «`A` … `B`» — тот же диапазон, что «`A … B`».
        is_group = bool(RANGE_RE.search(sheet_name + " " + desc)
                        or EXCEL_BAD_CHARS & set(sheet_name))
        warn = ""
        if is_group and not deltas:
            warn = ("группа листов, спека не называет их поимённо (нет блока «Дельты») — "
                    "имена может дать только владелец")
        elif not cols:
            warn = ("спека называет только секции дашборда, состав столбцов не объявлен"
                    if "Секци" in head else "столбцы не распознаны")
        elif not is_group and len(sheet_name) > EXCEL_NAME_LIMIT:
            warn = f"имя длиннее {EXCEL_NAME_LIMIT} символов — Excel не примет, сократить руками"
        if warn:
            warnings.append(f"{book}: лист «{sheet_name}» — {warn}")
            continue
        if dropped:
            warnings.append(f"{book}: лист «{sheet_name}» — не приняты за имена столбцов: "
                            f"{dropped} (составные через `/` разводятся руками)")
        if from_prose and "Секци" in head:
            # Дашборды спека описывает секциями, и часть имён стоит ВНЕ кавычек — значит
            # разобранный набор заведомо неполон. Молчать здесь нельзя: это тот же немой
            # промах, что схлопывал диапазон листов в один.
            warnings.append(f"{book}: лист «{sheet_name}» — собран из секций: {len(cols)} столбцов, "
                            "проверить полноту (часть имён в спеке стоит вне кавычек)")

        # Один лист или группа: у каждого листа группы общие столбцы + собственные дельты.
        group_flag = "F" if SHEET_ALL_F_RE.search(head) else "W"
        members = deltas if deltas else [(sheet_name, "")]
        for name, extra in members:
            own = list(cols)
            if extra:
                # В «Дельтах» контекст однозначен: перечисляются столбцы, флаг — общий по группе.
                extra_cols, _ = parse_prose(extra, solo_ok=True, flag=group_flag)
                own += extra_cols
            own, dups = dedup(own)
            if dups:
                warnings.append(f"{book}: лист «{name}» — повторы имён объединены: {sorted(set(dups))}")
            if len(name) > EXCEL_NAME_LIMIT:
                warnings.append(f"{book}: лист «{name}» — имя длиннее {EXCEL_NAME_LIMIT} символов, "
                                "Excel не примет")
                continue
            sheets.append({"name": name, "desc": desc if not deltas else "",
                           "columns": own, "prose": from_prose})
    return {"book": book, "level": book_levels().get(book, ""), "source": path.name,
            "sheets": sheets, "headers_found": len(bounds), "warnings": warnings}


def render(schema: dict) -> str:
    """YAML пишем вручную: нужен компактный flow-стиль колонок, как в собранной руками схеме."""
    out = [f"# {schema['book']}.xlsx — черновик из спеки (scripts/spec_to_schema.py). ТРЕБУЕТ ВЫЧИТКИ.",
           f"# Источник: docs/roadmap/spec/schemas/{schema['source']}. Формат: spec/TABLE_SCHEMA_FORMAT.md.",
           f"book: {schema['book']}"]
    if schema["level"]:
        out.append(f"level: {schema['level']}")
    out += [f"source: {schema['source']}", "sheets:"]
    for sh in schema["sheets"]:
        head = f"  - name: {sh['name']}"
        if sh.get("desc"):
            head += f"   # {sh['desc']}"
        out.append(head)
        if sh.get("prose"):
            # Прозаическая форма даёт имена и флаги, но НЕ типы — принятый string это допущение.
            out.append("    # столбцы из прозаической формы спеки: тип не объявлен, принят string")
        out.append("    columns:")
        for c in sh["columns"]:
            item = f"{{ name: {c['name']}, type: {c['type']}, flag: {c['flag']}"
            if "enum" in c:
                # Кавычки обязательны: без них YAML прочитает TRUE/FALSE/NO как булевы,
                # и set_validation получит не строку (падение на реальной книге).
                item += ", enum: [" + ", ".join(yq(v) for v in c["enum"]) + "]"
            if "formula" in c:
                item += f", formula: {yq(c['formula'])}"
            item += " }"
            line = f"      - {item}"
            if c.get("_note"):
                line += f"   # {c['_note']}"
            out.append(line)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def verify() -> int:
    """Приёмка: сходится ли разбор с эталоном, собранным руками (имена/типы/флаги/enum)."""
    import yaml
    ref_path = OUT_DIR / "network_config.schema.yaml"
    if not ref_path.exists():
        print("нет эталона network_config.schema.yaml — приёмку провести не на чем", file=sys.stderr)
        return 2
    ref = yaml.safe_load(ref_path.read_text(encoding="utf-8"))
    got = yaml.safe_load(render(parse_spec(SPEC_DIR / "network_config.schema.md")))
    problems = []
    if [s["name"] for s in ref["sheets"]] != [s["name"] for s in got["sheets"]]:
        problems.append(f"листы: эталон {[s['name'] for s in ref['sheets']]} vs разбор {[s['name'] for s in got['sheets']]}")
    # Расхождение числа листов уже названо выше; режем по короткому, чтобы сверить общее.
    for rs, gs in zip(ref["sheets"], got["sheets"], strict=False):
        for key in ("name", "type", "flag", "enum"):
            r = [c.get(key) for c in rs["columns"]]
            g = [c.get(key) for c in gs["columns"]]
            if r != g:
                problems.append(f"{rs['name']}.{key}: эталон {r} vs разбор {g}")
    if problems:
        print("❌ РАЗБОР НЕ СХОДИТСЯ С ЭТАЛОНОМ:")
        for p in problems:
            print("  -", p)
        return 1
    print(f"✓ разбор сходится с эталоном: {len(ref['sheets'])} листов, "
          f"{sum(len(s['columns']) for s in ref['sheets'])} столбцов")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true", help="приёмка конвертера на собранной руками схеме")
    ap.add_argument("--book", help="одна книга по имени (без .schema.md)")
    ap.add_argument("--all", action="store_true", help="все книги, объявленные шаблонами структуры")
    ap.add_argument("--write", action="store_true", help="записать в config/templates/tables/ (иначе stdout)")
    args = ap.parse_args()

    if args.verify:
        return verify()

    if args.book:
        books = [args.book]
    elif args.all:
        books = sorted(book_levels())          # только объявленные — не плодим схемы без потребителя
    else:
        ap.print_help()
        return 2

    for book in books:
        spec = SPEC_DIR / f"{book}.schema.md"
        if not spec.exists():
            print(f"пропуск {book}: нет спеки {spec.name}", file=sys.stderr)
            continue
        schema = parse_spec(spec)
        text = render(schema)
        cols = sum(len(s["columns"]) for s in schema["sheets"])
        for w in schema["warnings"]:
            print(f"  ⚠ {w}", file=sys.stderr)
        if args.write:
            dst = OUT_DIR / f"{book}.schema.yaml"
            if dst.exists() and "черновик" not in dst.read_text(encoding="utf-8")[:200]:
                print(f"{book}: пропуск — {dst.name} собран РУКАМИ, черновиком не затираем", file=sys.stderr)
                continue
            dst.write_text(text, encoding="utf-8")
            print(f"{book}: {len(schema['sheets'])}/{schema['headers_found']} листов, "
                  f"{cols} столбцов → записано")
        else:
            print(text)
            print(f"# {book}: {len(schema['sheets'])}/{schema['headers_found']} листов, "
                  f"{cols} столбцов", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
