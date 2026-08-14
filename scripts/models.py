"""
scripts/models.py — какие модели бывают, какие стоят, и установка выбранной одной командой.

## Зачем скрипт, а не поход в документацию
Список моделей меняется чаще, чем любая документация про них. Поэтому здесь он берётся ЖИВЫМ:
онлайн — из реестра шлюза (режим вызова, провайдер, цена), локально — из индекса голосов piper и
каталога Hugging Face по задаче. Что считать лишним, объявлено в `config/providers.yaml →
local.sources.*.filters`, поэтому отсев правится данными, а не этим файлом.

## Ключ нужен для ВЫЗОВА, а не для списка
Онлайн-список виден без ключей: реестр шлюза лежит офлайн. Ключ понадобится, когда дело дойдёт
до самого вызова — вносится он отдельно и вручную (`scripts/set_provider_key.py`).

## Установка проверяет, ЧТО кладёт на диск
`.bin/.ckpt/.pt` — pickle: его загрузка выполняет код из файла. Ставим только форматы, которые
кодом не являются, из объявленных источников и не больше объявленного размера. Поставленное
попадает в опись рядом с весами (`vendor/models/installed.yaml`) — рукописный конфиг машина не
переписывает, иначе первая же запись стёрла бы объяснения из него.

Прогон:
  python scripts/models.py online --kind image|tts [--provider openai] [--limit N]
  python scripts/models.py local  --kind image|tts [--language ru_RU] [--limit N]
  python scripts/models.py installed
  python scripts/models.py install <id> --kind image|tts
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.providers.catalog import ModelCatalog, OnlineCatalog    # noqa: E402
from core.providers.resolver import ProviderError                 # noqa: E402

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
    rows = _catalog().available(args.kind, limit=args.limit, language=args.language)
    if not rows:
        print("Каталог пуст после отсева — ослабь фильтры в config/providers.yaml → local.sources.")
        return 1
    cat = _catalog()
    scored, last = cat.with_fit(rows), {}
    for r in scored:
        size = f"{r['mb']} МБ" if r.get("mb") else f"↓{r.get('downloads', 0)}"
        params = f"{r['params_total'] / 1e9:.1f}B" if r.get("params_total") else "—"
        active = f"/{r['params_active'] / 1e9:.1f}B акт." if r.get("params_active") else ""
        fit = last = r.get("fit") or {}
        gpu = f"  (на карте: {fit['on_gpu']['verdict']})" if fit.get("on_gpu") else ""
        print(f"  {r['id']:<44}{size:<12}{params + active:<12}"
              f"{fit.get('verdict', '?'):<7}{fit.get('need_mb', 0)} МБ{gpu}")
    if last.get("device"):
        print(f"\nВердикт считался по: {last['device']} ({last.get('available_mb', 0)} МБ).")
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
    print(f"  диск:          {hw['disk_free_mb']} МБ свободно под веса")
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
    print(f"{'модель':<40}{'вид':<8}{'на диске':<12}путь")
    for r in rows:
        print(f"  {str(r['id']):<38}{str(r['kind'] or ''):<8}"
              f"{('да, ' + str(r['mb']) + ' МБ') if r['present'] else 'НЕТ':<12}{r['path']}")
    return 0


def cmd_pull(args) -> int:
    """Восстановить веса по описи: сама опись переезжает с машиной, гигабайты — нет."""
    cat = _catalog()
    declared = [e for e in cat.inventory() if not args.id or e.get("id") in args.id]
    if not declared:
        print("Опись пуста — ставить нечего. Посмотри доступное: "
              "python scripts/models.py local --kind tts")
        return 1
    for entry in declared:
        state = next((r for r in cat.installed() if r["id"] == entry["id"]), {})
        if state.get("present") and not args.force:
            print(f"  = {entry['id']} уже на диске ({state['mb']} МБ)")
            continue
        print(f"→ {entry['id']} ({entry.get('repo')})")
        got = cat.install_declared(entry, progress=lambda m: print(f"  {m}"))
        print(f"  ✓ {got['path']} — {round(got['mb'])} МБ")
    return 0


def cmd_install(args) -> int:
    cat = _catalog()
    entry = cat.install(args.id, args.kind, progress=lambda m: print(f"  {m}"))
    print(f"✓ {entry['id']} — {round(entry['mb'])} МБ, {entry['path']}")
    for skip in entry.get("refused") or []:
        print(f"  ⚠ пропущено: {skip['file']} — {skip['reason']}")
    print(f"  Записано в опись {cat.inventory_file.relative_to(ROOT)}. "
          "Чтобы канал этим исполнял — поставь имя модели в столбец model листа провайдеров.")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Каталог моделей: онлайн, локальные, установка")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("online", help="какие модели умеет вызвать шлюз (ключ не нужен)")
    p.add_argument("--kind", default="image", help="image | tts | stt")
    p.add_argument("--provider", default="", help="только один провайдер")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_online)

    p = sub.add_parser("local", help="какие локальные модели доступны к установке")
    p.add_argument("--kind", default="tts", help="tts | image")
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
    p.add_argument("--kind", required=True, help="tts | image")
    p.set_defaults(func=cmd_install)

    args = ap.parse_args(argv[1:])
    try:
        return args.func(args)
    except ProviderError as e:
        print(f"✗ {e.code}: {e.message}\n  {e.reason}")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
