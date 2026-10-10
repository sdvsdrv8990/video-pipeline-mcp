"""scripts/models.py — какие модели бывают, какие стоят, и установка выбранной одной командой.

Зовут: configure и test_install_cost.

Список берётся ЖИВЫМ из чужих каталогов, а что считать лишним — объявлено, поэтому отсев правится
данными. Онлайн-список виден без ключей: ключ нужен для вызова. Ставим только форматы, которые не
являются кодом: pickle-веса исполняют код при загрузке. Опись поставленного ДОПОЛНЯЕТСЯ, но не
перезаписывается — это рукописный конфиг, и первая же машинная запись стёрла бы объяснения
владельца.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.contracts import ContractError                          # noqa: E402
from core.providers.catalog import ModelCatalog, OnlineCatalog    # noqa: E402
from core.reactions import cli_text                               # noqa: E402

CFG = ROOT / "config" / "providers.yaml"


def _catalog() -> ModelCatalog:
    import yaml
    local = (yaml.safe_load(CFG.read_text(encoding="utf-8")) or {}).get("local") or {}
    return ModelCatalog(CFG, ROOT / str(local.get("models_dir", "vendor/models")))


def cmd_online(args) -> int:
    rows = OnlineCatalog().available(args.kind, args.provider, args.limit)
    if not rows:
        print("Ничего не нашлось. Виды: image, tts, stt.")
        return 1
    print(f"{'модель':<44}{'провайдер':<16}{'эндпоинт':<24}цена")
    for r in rows:
        cost = (f"{r['cost_per_image']}/картинка" if r.get("cost_per_image") else
                f"{r['cost_per_character']}/символ" if r.get("cost_per_character") else "—")
        print(f"  {r['id']:<42}{r['provider']:<16}{(r['endpoints'][0] if r['endpoints'] else '—'):<24}{cost}")
    print(f"\nВсего {len(rows)}. Ключ нужен для ВЫЗОВА, а не для списка: "
          "python scripts/set_provider_key.py --channel <канал> --provider <имя>")
    return 0


def cmd_local(args) -> int:
    rows = _catalog().available(args.kind, limit=args.limit, language=args.language, describe=True)
    if not rows:
        print("Каталог пуст после отсева — ослабь фильтры в config/providers.yaml → local.sources.")
        return 1
    cat = _catalog()
    scored, last = cat.with_fit(rows), {}  # type: ignore[var-annotated]
    for r in scored:
        size = (f"{r['mb']} МБ" if r.get("mb") is not None
                else "размер не назван" if r.get("size_unknown") else f"↓{r.get('downloads', 0)}")
        params = f"{r['params_total'] / 1e9:.1f}B" if r.get("params_total") else "—"
        active = f"/{r['params_active'] / 1e9:.1f}B акт." if r.get("params_active") else ""
        fit = last = r.get("fit") or {}
        gpu = f"  (на карте: {fit['on_gpu']['verdict']})" if fit.get("on_gpu") else ""
        print(f"  {r['id']:<44} {size:<12}{params + active:<12}"
              f"{fit.get('verdict', '?'):<7}{_мб(fit.get('need_mb'))}{gpu}")
        if r.get("description"):
            # Числа не отвечают, ЧТО модель делает: без этого выбор идёт вслепую.
            print(f"      {r['description'][:150]}")
        if r.get("license"):
            print(f"      лицензия: {r['license']}"
                  + (f" · файл: {r['file']}" if r.get("file") else ""))
    if last.get("device"):
        print(f"\nВердикт считался по: {last['device']} ({_мб(last.get('available_mb'))}).")
    if last.get("on_gpu"):
        print(f"Память карты в вердикт не вошла: {last['on_gpu']['blocked_by']}")
    print(f"\nВсего {len(rows)}. Поставить: python scripts/models.py install <id> --kind {args.kind}")
    return 0


def cmd_hardware(args) -> int:
    """Что за железо под сервером — без прав root."""
    from core.providers.hardware import probe

    cat = _catalog()
    hw = probe(cat.models_dir, cat.local.get("gpu"))
    print(f"  память:        {hw['ram_available_mb']} МБ доступно из {hw['ram_total_mb']} МБ")
    if hw["container_limit_mb"]:
        print(f"  лимит контейнера: {hw['container_limit_mb']} МБ")
    print(f"  ядра:          {hw['cpu_count']}")
    free = "не прочитано (см. НЕ ЗНАЕМ)" if hw["disk_free_mb"] is None else f"{hw['disk_free_mb']} МБ свободно под веса"
    print(f"  диск:          {free}")
    for card in hw["gpu"]:
        vram = (f"{card['vram_free_mb']} МБ свободно из {card['vram_total_mb']}"
                if card["vram_total_mb"] else "объём памяти драйвер не сообщает")
        print(f"  видеокарта:    {card['name']} [{card['driver']}] — {vram}")
        print(f"    расчёт:      {'ДОСТУПНА' if card['usable'] else 'НЕДОСТУПНА'} — {card['why']}")
    for gap in hw["unknown"]:
        print(f"  НЕ ЗНАЕМ:      {gap}")
    print(f"  {hw['note']}")
    return 0


def cmd_installed(args) -> int:
    rows = _catalog().installed()
    if not rows:
        print("Ничего не поставлено. Посмотреть доступное: python scripts/models.py local --kind tts")
        return 0
    print(f"{'модель':<40}{'вид':<8}{'на диске':<14}путь")
    for r in rows:
        disk = r["disk"]
        state = f"да, {r['mb']} МБ" if disk["verdict"] == "ready" else disk["verdict"].upper()
        print(f"  {str(r['id']):<38}{str(r['kind'] or ''):<8}{state:<14}{r['path']}")
        print(f"      {disk['why']}")
    return 0


def cmd_pull(args) -> int:
    """Восстановить веса по описи планом разницы: сама опись переезжает с машиной, гигабайты — нет."""
    cat = _catalog()
    declared = [e for e in cat.inventory() if not args.id or e.get("id") in args.id]
    if not declared:
        print("Опись пуста — ставить нечего. Посмотри доступное: "
              "python scripts/models.py local --kind tts")
        return 1
    failed = 0
    for entry in declared:
        print(f"→ {entry['id']} ({entry.get('repo')})")
        try:
            got = cat.restore(entry, force=args.force, progress=lambda m: print(f"  {m}"))
        except ContractError as e:                  # отказ каталога, загрузчика или двери — у каждого свой код
            failed += 1
            print("  " + cli_text(e, CFG.parent / "server_reactions.yaml").replace("\n", "\n  "))
            continue
        done = f"скачано частей: {len(got['fetched'])}" if got["fetched"] else "на месте"
        print(f"  ✓ {got['path']} — {done}; {got['disk']['why']}")
    return 1 if failed else 0


def cmd_install(args) -> int:
    cat = _catalog()
    already = None if args.force else cat.installed_as(args.id, args.kind)
    if already:
        print(f"= {args.id} уже стоит: ревизия {already['revision']}, {already['checked']} — "
              f"{already['path']}. Перекачать: --force.")
        return 0
    место = cat.место_под(args.id, args.kind, force=args.force)
    _предупредить_о_расчёте(cat, место)
    entry = cat.install_planned(место, progress=lambda m: print(f"  {m}"))
    print(f"✓ {entry['id']} — {round(entry['mb'])} МБ, {entry['path']}")
    for skip in entry.get("refused") or []:
        print(f"  ⚠ пропущено: {skip['file']} — {skip['reason']}")
    inventory = cat.inventory_file
    print(f"  Записано в опись {inventory.relative_to(ROOT) if inventory.is_relative_to(ROOT) else inventory}. "
          "Чтобы канал этим исполнял — поставь имя модели в столбец model листа провайдеров.")
    return 0


def _предупредить_о_расчёте(cat, место: dict) -> None:
    """Веса влезут (нехватку места уже отказал `место_под`), а расчёт — может и нет: это предупреждение, а не отказ,
    диск и память — разные вопросы. Судится по снимку `место_под`, тому же, по которому модель потом ставят."""
    вердикт = (cat.with_fit([место["entry"]])[0].get("fit") or {})
    if вердикт.get("verdict") == "no":
        print(f"  веса влезут ({место['нужно_мб']:.0f} МБ при {место['свободно_мб']:.0f} МБ), но расчёт — нет: "
              f"нужно {_мб(вердикт.get('need_mb'))} на {вердикт.get('device', '?')}, "
              f"доступно {_мб(вердикт.get('available_mb'))}")


def _мб(value) -> str:
    """Объём из вердикта расчёта: число — «N МБ», неизвестный (None) — «не известно», а не 0."""
    return "не известно" if value is None else f"{value} МБ"


def _kinds() -> str:
    """Виды моделей — из декларации, а не списком здесь: копия разошлась бы (и разошлась)."""
    import yaml
    local = (yaml.safe_load(CFG.read_text(encoding="utf-8")) or {}).get("local") or {}
    return " | ".join(local.get("sources") or {}) or "нет объявленных источников"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Каталог моделей: онлайн, локальные, установка")
    kinds = _kinds()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("online", help="какие модели умеет вызвать шлюз (ключ не нужен)")
    p.add_argument("--kind", default="image", help="image | tts | stt")
    p.add_argument("--provider", default="", help="только один провайдер")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_online)

    p = sub.add_parser("local", help="какие локальные модели доступны к установке")
    p.add_argument("--kind", default="tts", help=kinds)
    p.add_argument("--language", default="", help="язык (для голосов)")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_local)

    p = sub.add_parser("hardware", help="что за железо под сервером (без root)")
    p.set_defaults(func=cmd_hardware)

    p = sub.add_parser("installed", help="что уже стоит на этой машине")
    p.set_defaults(func=cmd_installed)

    p = sub.add_parser("pull", help="восстановить веса по описи (после переезда на другую машину)")
    p.add_argument("id", nargs="*", help="только эти (пусто → все объявленные)")
    p.add_argument("--force", action="store_true", help="перекачать, даже если веса уже на диске")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("install", help="поставить модель по имени")
    p.add_argument("id")
    p.add_argument("--kind", required=True, help=kinds)
    p.add_argument("--force", action="store_true",
                   help="перекачать все части, даже если модель уже стоит той же ревизией (починка весов)")
    p.set_defaults(func=cmd_install)

    args = ap.parse_args(argv[1:])
    try:
        return args.func(args)
    except ContractError as e:
        print(cli_text(e, CFG.parent / "server_reactions.yaml"))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
