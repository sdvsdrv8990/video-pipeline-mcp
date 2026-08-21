#!/usr/bin/env python3
"""
scripts/config_to_schema.py — YAML-конфиг вида «секция → список записей» в листы книги.

Такой конфиг уже ЯВЛЯЕТСЯ книгой: секция = лист, ключи записи = столбцы, записи = строки,
поэтому конвертация механическая — обход структуры, а не разбор прозы.
Приёмка: обратная сборка из ТЕКСТА полученных листов обязана дать исходные секции
(`--verify`). Не сошлось → конвертация теряет данные, переносить нечего.

Использование:
    python3 scripts/config_to_schema.py --config <src.yaml> --verify
    python3 scripts/config_to_schema.py --config <src.yaml>                    # листы в stdout
    python3 scripts/config_to_schema.py --config <src.yaml> --merge <book.schema.yaml>
"""

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# Флаг столбца выводим из имени: ключ записи → `id`, остальное правит человек (`W`).
# Список ключей объявлен здесь, а не выведен догадкой по данным.
ID_KEYS = {"sequence_id", "schedule_id", "rule_id", "resource_type", "metadata_type",
           "fragment_type", "param"}
# Булевы столбцы: тип объявляем явно, чтобы Excel не показал их строками.
BOOL_KEYS = {"enabled", "sync_mode", "requires_human_approval", "signal_on_reuse"}
# Значения enum берём ИЗ ФАКТИЧЕСКИХ данных конфига, не выдумываем. Список ключей — ровно
# закрытый: расширять его опасно. `day_of_week` с двумя встреченными днями
# дал бы дропдаун, запрещающий вторник — конфиг такого ограничения не накладывал.
ENUM_KEYS = {"severity", "status", "action"}


def col_type(key: str, values: list) -> str:
    """Тип столбца по фактическим значениям, а не по имени."""
    if key in BOOL_KEYS:
        return "boolean"
    if any(isinstance(v, list) for v in values):
        return "list"
    if key in ENUM_KEYS:
        return "enum"
    non_null = [v for v in values if v is not None and v != ""]
    if non_null and all(isinstance(v, bool) for v in non_null):
        return "boolean"
    if non_null and all(isinstance(v, int) and not isinstance(v, bool) for v in non_null):
        return "integer"
    if non_null and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
        return "float"
    return "string"


def build_sheets(config: dict) -> list[dict]:
    """Секция → лист. Порядок столбцов — порядок первого появления ключа в записях."""
    sheets = []
    for section, records in config.items():
        if not isinstance(records, list) or not records:
            continue                            # не таблица — переносить нечем
        order: list[str] = []
        for rec in records:
            for key in rec:
                if key not in order:
                    order.append(key)           # записи секции имеют РАЗНЫЕ наборы ключей
        columns = []
        for key in order:
            values = [rec.get(key) for rec in records]
            ctype = col_type(key, values)
            col = {"name": key, "type": ctype, "flag": "id" if key in ID_KEYS else "W"}
            if ctype == "enum":
                seen = []
                for v in values:
                    if v not in (None, "") and v not in seen:
                        seen.append(v)
                col["enum"] = seen
            columns.append(col)
        sheets.append({"name": section.upper(), "columns": columns,
                       "rows": [dict(rec) for rec in records]})
    return sheets


def rebuild_config(sheets: list[dict]) -> dict:
    """Обратная сборка: листы → исходный конфиг. Основа приёмки round-trip."""
    return {sheet["name"].lower(): [dict(row) for row in sheet["rows"]] for sheet in sheets}


def verify(config: Path) -> int:
    """Round-trip через СЕРИАЛИЗАЦИЮ: сравнение структур в памяти ничего не доказало бы —
    строки там те же объекты. Потеря возможна именно в тексте схемы, поэтому идём
    конфиг → листы → YAML-текст → разбор → конфиг."""
    src = yaml.safe_load(config.read_text(encoding="utf-8"))
    tables = {k: v for k, v in src.items() if isinstance(v, list) and v}
    text = "sheets:\n" + render(build_sheets(src))
    reparsed = yaml.safe_load(text)["sheets"]
    back = rebuild_config(reparsed)
    if back != tables:
        print("❌ ROUND-TRIP НЕ СОШЁЛСЯ — конвертация теряет данные:")
        for key in sorted(set(tables) | set(back)):
            if tables.get(key) != back.get(key):
                print(f"  - секция {key}: было {tables.get(key)!r}\n    стало {back.get(key)!r}")
        return 1
    skipped = [k for k in src if k not in tables]
    rows = sum(len(v) for v in tables.values())
    sheets = build_sheets(src)
    cols = sum(len(s["columns"]) for s in sheets)
    print(f"✓ round-trip сошёлся: {len(tables)} секций → {len(tables)} листов, "
          f"{cols} столбцов, {rows} строк-дефолтов")
    if skipped:
        print(f"  не таблицы, не переносятся: {skipped}")
    # Дропдаун из одного значения запрещает всё остальное, хотя конфиг этого не запрещал.
    for sh in sheets:
        for c in sh["columns"]:
            if c.get("enum") and len(c["enum"]) < 2:
                print(f"  ⚠ {sh['name']}.{c['name']}: enum из одного значения {c['enum']} — "
                      "дропдаун запретит остальные, дополнить руками")
    return 0


def render(sheets: list[dict]) -> str:
    """YAML-фрагмент в стиле собранных руками схем (flow-столбцы, блочные строки)."""
    out = []
    for sh in sheets:
        out.append(f"  - name: {sh['name']}")
        out.append("    columns:")
        for c in sh["columns"]:
            item = f"{{ name: {c['name']}, type: {c['type']}, flag: {c['flag']}"
            if "enum" in c:
                item += ", enum: [" + ", ".join(f"'{v}'" for v in c["enum"]) + "]"
            out.append(f"      - {item} }}")
        out.append("    rows:")
        for row in sh["rows"]:
            body = yaml.safe_dump(row, allow_unicode=True, default_flow_style=False,
                                  sort_keys=False).rstrip().splitlines()
            out.append(f"      - {body[0]}")
            out += [f"        {line}" for line in body[1:]]
        out.append("")
    return "\n".join(out)


def merge(config: Path, target: Path) -> int:
    """Вписать листы конфига в конец книги. Повторный запуск отказывает, а не дублирует листы."""
    src = yaml.safe_load(config.read_text(encoding="utf-8"))
    sheets = build_sheets(src)
    existing = yaml.safe_load(target.read_text(encoding="utf-8"))
    have = {s["name"] for s in existing["sheets"]}
    dup = [s["name"] for s in sheets if s["name"] in have]
    if dup:
        print(f"листы уже есть в {target.name}: {dup} — повторно не добавляю", file=sys.stderr)
        return 1
    text = target.read_text(encoding="utf-8").rstrip() + "\n\n"
    # Файл перестаёт быть чистым черновиком спеки: в нём появились листы из конфига.
    # Без снятия слова «черновик» следующий `spec_to_schema --all --write` затрёт их молча
    # (проверено: затирает). Защита от затирания в конвертере спек ищет именно это слово.
    draft = f"# {target.name.replace('.schema.yaml', '')}.xlsx — черновик из спеки"
    for line in text.splitlines():
        if line.startswith(draft):
            text = text.replace(line, f"# {target.name} — из спеки (scripts/spec_to_schema.py) + листы "
                                      f"конфига\n# (scripts/config_to_schema.py). ДОСОБРАН — "
                                      f"spec_to_schema его больше не перезаписывает.", 1)
            break
    text += (f"  # ═══ Листы из бывшего {config.name} (S22) ═══\n"
             "  # Перенесены механически scripts/config_to_schema.py (round-trip проверен).\n"
             "  # РЕШЕНИЯ, которые несла спека-источник:\n"
             "  #  1. WORKFLOW_SEQUENCES — allow-list разрешённых переходов + human-gate,\n"
             "  #     а НЕ жёсткий маршрут: маршрут выбирает ИИ, сервер лишь ограничивает.\n"
             "  #  2. RESOURCE_LIMITS — ЕДИНЫЙ источник провайдеров: одна строка = провайдер +\n"
             "  #     модель + параметры + лимит + fallback; адаптер читает строку целиком.\n"
             "  #  3. sync_mode отличает синхронный вызов от async (trigger → poll → download).\n"
             "  #  4. SCENE_PROFILE.enabled — «тихий столбец»: гасит ПОВЕДЕНИЕ, не структуру;\n"
             "  #     per-video сбор идёт только из включённых типов фрагментов.\n"
             "  #  5. AUTOMATION_RULES.severity=CRITICAL — жёсткий блок с вызовом человека.\n")
    text += render(sheets)
    target.write_text(text.rstrip() + "\n", encoding="utf-8")
    print(f"{target.name}: +{len(sheets)} листов "
          f"({sum(len(s['columns']) for s in sheets)} столбцов, "
          f"{sum(len(s['rows']) for s in sheets)} строк)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, type=Path, help="YAML-конфиг «секция → записи»")
    ap.add_argument("--verify", action="store_true", help="round-trip: конфиг → листы → конфиг")
    ap.add_argument("--merge", type=Path, metavar="BOOK", help="вписать листы в схему книги")
    args = ap.parse_args()
    if not args.config.exists():
        print(f"нет конфига-источника: {args.config}", file=sys.stderr)
        return 2
    if args.verify:
        return verify(args.config)
    if args.merge:
        return merge(args.config, args.merge)
    print(render(build_sheets(yaml.safe_load(args.config.read_text(encoding="utf-8")))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
