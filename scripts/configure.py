"""
scripts/configure.py — меню настройки ПОСЛЕ установки: локальные модели и онлайн-провайдеры.

## Назначение
Второй этап установки, без набора команд руками. Запускается в конце install.sh (local/both)
и отдельно, повторяемо. venv уже есть — поэтому Python-меню, а не голый bash.

## Границы
- моделей часть работает сейчас (models.py под капотом); онлайн-ключ вводится под КАНАЛ позже
  (getpass в терминале или студия), через ИИ ключ не проходит — здесь только объяснение;
- флаг режима не пишем: ручка без читателя запрещена, выбор здесь = ДЕЙСТВИЕ, не запись.
"""

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "scripts" / "models.py"
PROVIDERS = ROOT / "config" / "providers.yaml"


def _kinds() -> list[str]:
    data = yaml.safe_load(PROVIDERS.read_text(encoding="utf-8")) or {}
    return list(((data.get("local") or {}).get("sources") or {}).keys())


def _pick(prompt: str, options: list[str]) -> int | None:
    """Нумерованное меню: индекс выбора или None на «назад»/пусто/EOF."""
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    print("  0) назад")
    try:
        raw = input(f"{prompt} ").strip()
    except EOFError:
        return None
    if not raw or raw == "0":
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return int(raw) - 1
    print("Нет такого пункта.")
    return _pick(prompt, options)


def _models() -> None:
    kinds = _kinds()
    if not kinds:
        print("Виды моделей не объявлены в config/providers.yaml → local.sources.")
        return
    idx = _pick("Вид модели:", kinds)
    if idx is None:
        return
    kind = kinds[idx]
    print(f"\nДоступные локальные модели ({kind}):")
    subprocess.run([sys.executable, str(MODELS), "local", "--kind", kind], check=False)
    try:
        mid = input("\nID модели для установки (пусто — назад): ").strip()
    except EOFError:
        return
    if mid:
        subprocess.run([sys.executable, str(MODELS), "install", mid, "--kind", kind], check=False)


def _online() -> None:
    print("\nОнлайн-провайдер: ключ вводится СКРЫТО под конкретный канал — не сейчас.")
    print("  • Канал создаётся через работающий сервер (студия/инструменты), там же вводится ключ.")
    print("  • Ключ идёт через ТВОЙ терминал (getpass) или студию — не через Claude AI Web.")
    print("  • Если инструмент попросит ключ, сервер подскажет ИИ, что и куда вставить.")
    try:
        input("Понятно — Enter, чтобы вернуться. ")
    except EOFError:
        pass


def main() -> int:
    actions = [
        ("Локальная модель — поставить", _models),
        ("Онлайн-провайдер — как подключить ключ", _online),
    ]
    print("=== Настройка: модели и провайдеры ===")
    while True:
        idx = _pick("Что настроить?", [a[0] for a in actions])
        if idx is None:
            print("Готово.")
            return 0
        actions[idx][1]()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nПрервано.")
        sys.exit(0)
