"""
scripts/fetch_local_models.py — тянет веса локальных моделей в каталог зависимостей.

## Зачем отдельный шаг
Веса — зависимость, а не исходник: гигабайты в git не кладут. Поэтому каталог моделей
(`config/providers.yaml → local.models_dir`) в `.gitignore`, а его наполнение — этот скрипт.
Клон репозитория без прогона скрипта даёт сервер без локальных моделей — и он об этом честно
скажет кодом `LOCAL_MODEL_MISSING`, а не притворится, что генерирует.

## Что откуда
Список моделей объявлен в `config/providers.yaml → local.catalog`: репозиторий, файлы, куда
положить. Добавить модель = строка в декларации, а не правка этого файла.

Прогон:  python scripts/fetch_local_models.py [id ...]
"""

import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def catalog() -> tuple[Path, list[dict]]:
    cfg = yaml.safe_load((ROOT / "config" / "providers.yaml").read_text(encoding="utf-8")) or {}
    local = cfg.get("local") or {}
    models_dir = ROOT / str(local.get("models_dir", "vendor/models"))
    return models_dir, list(local.get("catalog") or [])


def fetch(entry: dict, models_dir: Path) -> Path:
    from huggingface_hub import hf_hub_download, snapshot_download

    dest = models_dir / str(entry["dest"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    if entry.get("snapshot"):
        snapshot_download(repo_id=entry["repo"], local_dir=str(dest),
                          allow_patterns=entry.get("allow") or None)
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    for remote in entry.get("files") or []:
        got = hf_hub_download(repo_id=entry["repo"], filename=remote)
        # Плоско по имени: piper ждёт .onnx и .onnx.json рядом, без вложенности репозитория.
        shutil.copyfile(got, dest / Path(remote).name)
    return dest


def main(argv: list[str]) -> int:
    models_dir, entries = catalog()
    wanted = set(argv[1:])
    picked = [e for e in entries if not wanted or str(e.get("id")) in wanted]
    if not picked:
        print(f"Нечего тянуть: в декларации нет {sorted(wanted) or 'ни одной модели'}")
        return 1
    for entry in picked:
        print(f"→ {entry['id']} ({entry['repo']})")
        path = fetch(entry, models_dir)
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        print(f"  ✓ {path.relative_to(ROOT)} — {round(size / 1024 / 1024)} МБ")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
