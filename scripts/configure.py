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


def _local() -> dict:
    data = yaml.safe_load(PROVIDERS.read_text(encoding="utf-8")) or {}
    return data.get("local") or {}


def _kinds() -> list[str]:
    return list((_local().get("sources") or {}).keys())


def _среда_расчёта() -> None:
    """Поставить torch под ТУ карту, что найдена на машине, а не под чужую.

    Карта определяется без прав root (`core.providers.hardware`), а чем её ставить — объявлено
    рядом с самой картой (`config/providers.yaml → local.gpu.drivers[<драйвер>].extras/index`).
    Без этого шага веса лягут, а расчёт откажет уже при вызове: колесо torch с PyPI под Linux
    собрано под CUDA и карту AMD не увидит, различает сборки только локальная версия.
    """
    sys.path.insert(0, str(ROOT))
    from core.providers.hardware import probe

    правила = _local().get("gpu") or {}
    драйверы = правила.get("drivers") or {}
    железо = probe(ROOT, правила)
    найдено = None
    for карта in железо["gpu"]:
        правило = драйверы.get(карта["driver"]) or {}
        if правило.get("extras"):
            найдено = (f"{карта['name']} [{карта['driver']}]", правило)
            break
    if найдено is None:
        найдено = ("видеокарта не найдена — расчёт на процессоре", правила.get("no_gpu") or {})
    что, правило = найдено
    print(f"\nНа этой машине: {что}")
    имена = sorted({str(п.get("extras")) for п in list(драйверы.values()) + [правила.get("no_gpu") or {}]
                    if п.get("extras")})
    idx = _pick("Ставить среду расчёта:", [f"по найденному — {правило.get('extras')}",
                                           "выбрать вручную", ])
    if idx is None:
        return
    if idx == 1:
        ручной = _pick("Какая среда:", имена)
        if ручной is None:
            return
        имя = имена[ручной]
        правило = next((п for п in list(драйверы.values()) + [правила.get("no_gpu") or {}]
                        if п.get("extras") == имя), {})
    _поставить(str(правило.get("extras") or ""), str(правило.get("index") or ""))


def _поставить(extras: str, index: str) -> None:
    """Одна команда установки, напечатанная ДО запуска: её же можно повторить руками."""
    if not extras:
        print("Чем ставить эту карту — не объявлено (config/providers.yaml → local.gpu).")
        return
    команда = [sys.executable, "-m", "pip", "install", "-e", f".[local,{extras}]"]
    if index:
        команда += ["--extra-index-url", index]
    print("  " + " ".join(команда))
    готово = subprocess.run(команда, cwd=ROOT, check=False)
    print("✓ среда расчёта поставлена" if готово.returncode == 0
          else f"✗ установка вернула {готово.returncode} — команда выше повторяема руками")


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
        ("Среда расчёта — torch под карту этой машины", _среда_расчёта),
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
