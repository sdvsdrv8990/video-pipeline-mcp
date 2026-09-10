"""scripts/install_cost.py — цена установки СЧИТАЕТСЯ, а не вписывается числом.

## Назначение
Ответ на «сколько это займёт и хватит ли машины» ДО установки: состав берёт `pip --dry-run
--report`, размер каждого колеса — индекс, свободное место и ОЗУ — `core.providers.hardware`.
Записанных чисел здесь нет: изменился манифест или версия колеса — изменился и ответ.

## Границы
- считаются наборы, нужные СЕРВЕРУ (боевой периметр и локальный расчёт); гейт, тесты и скрипты
  серверу не нужны и в цену установки не входят;
- размер, которого индекс не сообщает, называется вслух: «не сообщает» — не «ноль»;
- сети или pip нет — ответ «не посчитано», а не «влезет»;
- сравнивается размер СКАЧИВАНИЯ: распакованное занимает больше, поэтому «не хватает» — это
  твёрдый отказ, а «хватает» — необходимое условие, не достаточное.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Индекс PyTorch отвечает 403 на запрос без представления клиента: размер торча иначе не узнать.
ЗАГОЛОВКИ = {"User-Agent": "pip/26.2"}
ТАЙМАУТ_СЕТИ = 60


def состав(цель: str, индекс: str = "", python: str = sys.executable,
           таймаут: int = 900) -> tuple[list[dict], str]:
    """Что поставит pip. Вторым значением — причина, если состав узнать не удалось."""
    отчёт_файл = Path(tempfile.mkdtemp()) / "report.json"
    команда = [python, "-m", "pip", "install", "--dry-run", "--quiet", "--ignore-installed",
               "--report", str(отчёт_файл), цель]
    if индекс:
        команда += ["--extra-index-url", индекс]
    try:
        готово = subprocess.run(команда, cwd=ROOT, capture_output=True, text=True, timeout=таймаут)
    except (OSError, subprocess.SubprocessError) as беда:
        return [], f"pip не отработал: {type(беда).__name__}"
    if готово.returncode != 0 or not отчёт_файл.exists():
        причина = (готово.stderr or готово.stdout).strip().splitlines()
        return [], причина[-1][:200] if причина else f"pip вернул {готово.returncode}"
    return json.loads(отчёт_файл.read_text(encoding="utf-8")).get("install", []), ""


def размер(url: str) -> int:
    """Байты колеса по заголовку. Ноль — индекс не сообщил, и это не «пусто»."""
    try:
        запрос = urllib.request.Request(url, method="HEAD", headers=ЗАГОЛОВКИ)
        with urllib.request.urlopen(запрос, timeout=ТАЙМАУТ_СЕТИ) as ответ:
            return int(ответ.headers.get("Content-Length") or 0)
    except Exception:                    # noqa: BLE001 — сеть, код ответа, редирект: исход один
        return 0


def цена(цель: str, индекс: str = "") -> dict:
    """Сколько скачается для `цель`. Неизвестное перечислено поимённо, а не спрятано в ноль."""
    пакеты, ошибка = состав(цель, индекс)
    if ошибка:
        return {"цель": цель, "пакетов": 0, "скачать_мб": 0.0, "без_размера": [], "ошибка": ошибка}
    байт, молчат = 0, []
    for пакет in пакеты:
        url = (пакет.get("download_info") or {}).get("url", "")
        if not url.startswith("http"):
            continue                     # своё дерево ставится из каталога, а не качается
        сколько = размер(url)
        байт += сколько
        if not сколько:
            молчат.append(пакет["metadata"]["name"])
    return {"цель": цель, "пакетов": len(пакеты), "скачать_мб": байт / 1e6,
            "без_размера": молчат, "ошибка": ""}


def машина() -> dict:
    """Свободное место и память — тем же путём, каким их видит сервер."""
    sys.path.insert(0, str(ROOT))
    from core.providers.hardware import probe
    снимок = probe(ROOT)
    return {"свободно_мб": снимок["disk_free_mb"], "озу_мб": снимок["ram_available_mb"],
            "ядра": снимок["cpu_count"]}


def отчёт(цены: list[dict], факты: dict) -> tuple[list[str], bool]:
    """Строки для человека и вердикт. Каждая строка называет числа, а не намерения."""
    строки: list[str] = []
    нужно = 0.0
    неизвестно: list[str] = []
    for ц in цены:
        if ц["ошибка"]:
            строки.append(f"  {ц['цель']}: не посчитано — {ц['ошибка']}")
            неизвестно.append(ц["цель"])
            continue
        хвост = f" · размер не сообщили: {', '.join(ц['без_размера'])}" if ц["без_размера"] else ""
        строки.append(f"  {ц['цель']}: пакетов {ц['пакетов']}, скачать {ц['скачать_мб']:.0f} МБ{хвост}")
        нужно += ц["скачать_мб"]
        неизвестно += ц["без_размера"]
    строки.append(f"  свободно на диске: {факты['свободно_мб']} МБ · ОЗУ доступно: "
                  f"{факты['озу_мб']} МБ · ядер: {факты['ядра']}")
    хватает = факты["свободно_мб"] >= нужно
    if not хватает:
        строки.append(f"  НЕ ХВАТАЕТ МЕСТА: нужно не меньше {нужно:.0f} МБ, свободно "
                      f"{факты['свободно_мб']} МБ")
    elif неизвестно:
        строки.append(f"  размер известен не весь ({', '.join(sorted(set(неизвестно)))}) — "
                      f"итог будет больше {нужно:.0f} МБ")
    return строки, хватает


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--цель", action="append", default=[],
                    help="что ставим: `.` (сервер), `.[local]`, `.[gpu-amd]`; можно несколько")
    ap.add_argument("--индекс", default="", help="--extra-index-url для последней цели")
    a = ap.parse_args(argv)
    цели = getattr(a, "цель") or ["."]
    индекс = getattr(a, "индекс")
    цены = [цена(ц, индекс if ц == цели[-1] else "") for ц in цели]
    строки, хватает = отчёт(цены, машина())
    print("Требуется:")
    for строка in строки:
        print(строка)
    return 0 if хватает else 1


if __name__ == "__main__":
    sys.exit(main())
