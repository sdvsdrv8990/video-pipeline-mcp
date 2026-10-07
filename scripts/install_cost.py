"""scripts/install_cost.py — цена установки СЧИТАЕТСЯ, а не вписывается числом.

Отвечает на «сколько займёт и хватит ли машины» ДО установки набора. Записанных чисел здесь нет:
изменился манифест или версия колеса — изменился и ответ. Считаются наборы, нужные СЕРВЕРУ; гейт,
тесты и скрипты серверу не нужны и в цену установки не входят. Размер, которого индекс не
сообщает, называется вслух с причиной: «не сообщает» — не «ноль». Сети или pip нет — ответ «не посчитано», а
не «влезет». Сравнивается размер СКАЧИВАНИЯ, а распакованное занимает больше: поэтому «не
хватает» — твёрдый отказ, а «хватает» — необходимое условие, не достаточное. Индекс спрашивается дверью сервера
(`Fetcher.length`) по политике `packages.yaml`, поэтому скрипт исполняется в среде сервера (.venv).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.contracts import ContractError                    # noqa: E402
from core.declaration import Declared, declarations_root   # noqa: E402
from core.declaration_schemas import PACKAGES, RequestPolicy  # noqa: E402
from core.packages import PackageError                     # noqa: E402
from core.providers.download import Fetcher, Unreached     # noqa: E402
from core.providers.hardware import probe                  # noqa: E402
from core.reactions import cli_text                        # noqa: E402

# Клиент представляется как pip, который колесо скачает: CDN индекса PyTorch отвечает 403 на «Python-urllib».
ЗАГОЛОВКИ = {"User-Agent": "pip/26.2"}
# Коды выхода сверх 0 — свои: 1 CPython отдаёт на необработанное исключение, 2 — argparse на ошибку вызова.
НЕ_ХВАТАЕТ, НЕ_СУДИМО = 3, 4
ИНДЕКС = Declared(declarations_root() / PACKAGES.file, PackageError, "индекса пакетов",
                  f"Заведи config/{PACKAGES.file} — как спрашивать индекс пакетов, объявлено там.", schema=PACKAGES.schema)
РЕЕСТР = declarations_root() / "server_reactions.yaml"
швы: dict = {}      # транспорт, разрешение имён и сон загрузчика (MF-16): подставляет тест


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


def размер(url: str, политика: RequestPolicy) -> tuple[int | None, str]:
    """Байты колеса по HEAD через дверь сервера — (байты или None, откуда длина или почему её нет).

    Повторимое повторяется по политике; отказ индекса (4xx, чужая схема, адрес во внутренней сети) — кодом реестра.
    """
    return Fetcher(политика, **швы).length(url, ЗАГОЛОВКИ)


def цена(цель: str, индекс: str = "") -> dict:
    """Сколько скачается для `цель`. Неизвестное перечислено поимённо с причиной, а не спрятано в ноль.

    Политика читается до pip: битая декларация — отказ двери кодом сразу, а не после прогона состава. Хост, который
    дверь не достигла и после всех попыток (`Unreached`), остальным пакетам прогона отвечает той же причиной без
    новых запросов: одно решение на хост за прогон, иначе лежащий хост колёс держит счёт часами.
    """
    политика = ИНДЕКС.model.index.fetch
    пакеты, ошибка = состав(цель, индекс)
    if ошибка:
        return {"цель": цель, "пакетов": 0, "скачать_мб": 0.0, "без_размера": [], "ошибка": ошибка}
    байт, молчат = 0, []
    недостижимы: dict[str | None, str] = {}
    for пакет in пакеты:
        url = (пакет.get("download_info") or {}).get("url", "")
        if not url.startswith("http"):
            continue                     # своё дерево ставится из каталога, а не качается
        хост = urlsplit(url).hostname
        if хост in недостижимы:
            сколько, почему = None, недостижимы[хост]
        else:
            try:
                сколько, почему = размер(url, политика)
            except ContractError as отказ:
                сколько, почему = None, cli_text(отказ, РЕЕСТР).splitlines()[0]
            if isinstance(почему, Unreached):
                недостижимы[хост] = почему
        if сколько is None:
            молчат.append({"пакет": пакет["metadata"]["name"], "почему": почему})
        else:
            байт += сколько
    return {"цель": цель, "пакетов": len(пакеты), "скачать_мб": байт / 1e6,
            "без_размера": молчат, "ошибка": ""}


def машина() -> dict:
    """Свободное место и память — тем же путём, каким их видит сервер."""
    снимок = probe(ROOT)
    return {"свободно_мб": снимок["disk_free_mb"], "озу_мб": снимок["ram_available_mb"],
            "ядра": снимок["cpu_count"]}


def отчёт(цены: list[dict], факты: dict) -> tuple[list[str], bool | None]:
    """Строки для человека и вердикт: True — хватает, False — не хватает, None — место не прочитано, судить нечем.

    Каждая строка называет числа, а не намерения; причина отрицательного вердикта — своей строкой.
    """
    строки: list[str] = []
    нужно = 0.0
    неизвестно: list[str] = []
    for ц in цены:
        if ц["ошибка"]:
            строки.append(f"  {ц['цель']}: не посчитано — {ц['ошибка']}")
            неизвестно.append(ц["цель"])
            continue
        молчащие = "; ".join(f"{м['пакет']} — {м['почему']}" for м in ц["без_размера"])
        хвост = f" · размер не сообщили: {молчащие}" if молчащие else ""
        строки.append(f"  {ц['цель']}: пакетов {ц['пакетов']}, скачать {ц['скачать_мб']:.0f} МБ{хвост}")
        нужно += ц["скачать_мб"]
        неизвестно += [м["пакет"] for м in ц["без_размера"]]
    свободно = "не прочитано" if факты["свободно_мб"] is None else f"{факты['свободно_мб']} МБ"
    строки.append(f"  свободно на диске: {свободно} · ОЗУ доступно: "
                  f"{факты['озу_мб']} МБ · ядер: {факты['ядра']}")
    if факты["свободно_мб"] is None:
        строки.append(f"  свободное место не прочитано — хватит ли {нужно:.0f} МБ, судить нечем")
        return строки, None
    хватает = факты["свободно_мб"] >= нужно
    if not хватает:
        строки.append(f"  НЕ ХВАТАЕТ МЕСТА: нужно не меньше {нужно:.0f} МБ, свободно "
                      f"{факты['свободно_мб']} МБ")
    elif неизвестно:
        строки.append(f"  размер известен не весь ({', '.join(sorted(set(неизвестно)))}) — "
                      f"итог будет больше {нужно:.0f} МБ")
    return строки, хватает


def main(argv: list[str] | None = None) -> int:
    """Код выхода: 0 — хватает, `НЕ_ХВАТАЕТ` — места меньше нужного, `НЕ_СУДИМО` — место не прочитано; падение —
    код CPython."""
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
    if хватает is None:
        return НЕ_СУДИМО
    return 0 if хватает else НЕ_ХВАТАЕТ


if __name__ == "__main__":
    sys.exit(main())
