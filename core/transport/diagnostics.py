"""
core/transport/diagnostics.py — приёмка туннеля: что именно работает, а что нет.

## Назначение
Гоняет объявленный в `config/tunnel_diagnostics.yaml` набор и отвечает ТОЧКОЙ отказа со следующим
шагом. Состояние спрашивается у обеих сторон — у транспорта (`status`) и у сети (`probe`): лог
туннеля это свидетельство заинтересованной стороны.

## Границы
- протокол, формат пакетов и криптослой не задеваются: диагностика читает, а не меняет;
- живёт ровно на время прогона — постоянного захвата и крючков после себя не оставляет;
- в потоке и в отчёте не бывает пользовательских данных, ключей, токенов и паролей;
- нечем проверить — исход `нечем` с ИМЕНЕМ недостающего: выдать это за `прошло` значило бы
  отчитаться о работе, которой не было.
"""

import hashlib
import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from secrets import token_hex
from tempfile import mkdtemp
from typing import Any

import httpx

from core.contracts import ContractError
from core.declaration import Declaration

ROOT = Path(__file__).resolve().parents[2]
КАТАЛОГ = ROOT / "config" / "tunnel_diagnostics.yaml"

ПРОШЛО, ОТКАЗ, НЕЧЕМ = "прошло", "отказ", "нечем"
НЕПОЛНО = "неполно"


@dataclass(frozen=True)
class Проверка:
    """Строка каталога проверок."""

    id: str
    что: str
    доказывает: str
    режим: str


@dataclass(frozen=True)
class Итог:
    """Исход одной проверки. `дальше` пуст только когда идти некуда — проверка прошла."""

    проверка: Проверка
    исход: str
    причина: str
    дальше: str = ""


@dataclass
class Отчёт:
    """Свод прогона. `путь` называет, каким путём реально шёл тестовый поток."""

    режим: str
    итоги: list[Итог] = field(default_factory=list)
    путь: str = ""

    @property
    def вердикт(self) -> str:
        """Непроверенное не считается пройденным: `нечем` даёт НЕПОЛНО, а не зелёный итог."""
        исходы = {и.исход for и in self.итоги}
        if ОТКАЗ in исходы:
            return ОТКАЗ
        return НЕПОЛНО if НЕЧЕМ in исходы else ПРОШЛО

    def строки(self) -> list[str]:
        """Отчёт для человека: знак, что проверяли, и причина."""
        знак = {ПРОШЛО: "✓", ОТКАЗ: "✗", НЕЧЕМ: "—"}
        строки = [f"  {знак[и.исход]} {и.проверка.что:<32} {и.причина}" for и in self.итоги]
        if self.путь:
            строки.append(f"  путь потока: {self.путь}")
        строки.append(f"  ИТОГ: {self.вердикт.upper()}")
        строки += [f"  дальше — {и.проверка.что.lower()}: {и.дальше}"
                   for и in self.итоги if и.дальше]
        return строки


class Диагностика:
    """Прогон объявленных проверок по живому туннелю (или по его отсутствию — тоже улика)."""

    def __init__(self, tunnel: Any = None, конец: str = "client",
                 каталог: str | Path = КАТАЛОГ, корень: Path = ROOT):
        self._объявление = Declaration(
            каталог, ContractError, "проверок туннеля",
            "Заведи config/tunnel_diagnostics.yaml — набор проверок объявлен там.").data
        self.tunnel = tunnel
        self.конец = конец
        self.корень = корень
        self._временные: list[Path] = []
        self._поток: dict[str, str] = {}
        self._приёмник: HTTPServer | None = None
        self._путь = ""

    def проверки(self, режим: str) -> list[Проверка]:
        """Полный режим включает быстрый: он его продолжение, а не замена."""
        нужны = {"быстрая"} if режим == "быстрая" else {"быстрая", "полная"}
        return [Проверка(**строка) for строка in self._объявление["проверки"]
                if строка["режим"] in нужны]

    # ── Проверки ────────────────────────────────────────────────────────────

    def _конфигурация(self) -> tuple[str, str, str]:
        if self.tunnel is None:
            return НЕЧЕМ, "объект туннеля не передан", "поднять туннель и повторить"
        режим, порт = getattr(self.tunnel, "mode", ""), getattr(self.tunnel, "port", 0)
        if not режим or not порт:
            return ОТКАЗ, "режим или порт не объявлены", "заполнить config/tunnel.yaml"
        return ПРОШЛО, f"режим {режим}, порт {порт}", ""

    def _компоненты(self) -> tuple[str, str, str]:
        нужны = self._объявление["концы"].get(self.конец, [])
        if not нужны:
            return НЕЧЕМ, f"для конца `{self.конец}` бинарь не объявлен", ""
        версии = []
        for имя in нужны:
            путь = self._бинарь(имя)
            if путь is None:
                return (ОТКАЗ, f"{имя} не найден",
                        f"установить {имя} — ./install.sh ставит его для объявленного конца")
            версии.append(f"{имя} {self._версия(путь, имя)}")
        return ПРОШЛО, ", ".join(версии), ""

    def _служба(self) -> tuple[str, str, str]:
        if self.tunnel is None:
            return НЕЧЕМ, "туннель в этом прогоне не поднят", "./run.sh поднимет сервер и туннель"
        снимок = self.tunnel.status()
        if not снимок.get("running"):
            причина = снимок.get("last_error") or "процесс не запущен"
            return ОТКАЗ, f"служба не работает: {причина}", "перезапустить ./run.sh"
        if not снимок.get("connected"):
            return (ОТКАЗ, "процесс жив, но соединений нет",
                    "путь до края закрыт — проверить VPN и файрвол")
        return ПРОШЛО, f"рёбер зарегистрировано: {снимок.get('connections')}", ""

    def _соединение(self) -> tuple[str, str, str]:
        if self.tunnel is None:
            return НЕЧЕМ, "спрашивать нечего: туннель не поднят", "./run.sh поднимет туннель"
        улика = self.tunnel.probe()
        if улика.get("дошло"):
            return ПРОШЛО, улика.get("текст", "доехало до нашего процесса"), ""
        return (ОТКАЗ, улика.get("текст", "до нашего процесса не доходит"),
                "снаружи отвечает не наш процесс — проверить маршрут публичного адреса "
                "на локальный порт")

    def _поток_до(self) -> tuple[str, str, str]:
        объявлен = self._объявление["поток"]
        поток = {"метка": объявлен["метка"], "id": token_hex(8),
                 "время": datetime.now(UTC).isoformat(timespec="seconds")}
        поток["сумма"] = self._сумма(поток)
        self._поток = поток
        файл = Path(mkdtemp(prefix="vpm-диагностика-")) / "поток.json"
        файл.write_text(json.dumps(поток, ensure_ascii=False), encoding="utf-8")
        self._временные.append(файл)
        return ПРОШЛО, f"метка {поток['метка']}, id {поток['id']}, сумма снята", ""

    def _поток_после(self) -> tuple[str, str, str]:
        if not self._поток:
            return НЕЧЕМ, "поток не собран", "проверка «до» обязана пройти первой"
        принято: list[bytes] = []
        сервер = self._поднять_приёмник(принято)
        self._путь = ("петля 127.0.0.1 — путь через туннель проверен отдельно, проверкой "
                      "«Соединение»; второй конец на другой машине этим прогоном не задействован")
        try:
            httpx.post(f"http://127.0.0.1:{сервер.server_port}/",
                       content=json.dumps(self._поток, ensure_ascii=False).encode(), timeout=5.0)
        except httpx.HTTPError as beda:
            return ОТКАЗ, f"поток не дошёл до приёмника: {beda}", "проверить локальный сетевой стек"
        for _ in range(50):
            if принято:
                break
            time.sleep(0.02)
        if not принято:
            return ОТКАЗ, "приёмник не увидел потока", "проверить локальный сетевой стек"
        пришло = json.loads(принято[0].decode("utf-8"))
        if пришло.get("метка") != self._поток["метка"]:
            return ОТКАЗ, "метка потока не та", "поток подменён по дороге"
        if not (self._сумма(пришло) == self._поток["сумма"] == пришло.get("сумма")):
            return ОТКАЗ, "сумма не сошлась — поток изменился по дороге", "сверить обработку"
        return ПРОШЛО, f"сумма сошлась, id {пришло['id']}", ""

    def _уборка(self) -> tuple[str, str, str]:
        if self._приёмник is not None:
            self._приёмник.shutdown()
            self._приёмник.server_close()
            self._приёмник = None
        осталось = []
        for файл in self._временные:
            shutil.rmtree(файл.parent, ignore_errors=True)
            if файл.exists():
                осталось.append(str(файл))
        self._временные.clear()
        self._поток.clear()
        if осталось:
            return ОТКАЗ, f"не снесено: {', '.join(осталось)}", "удалить вручную"
        return ПРОШЛО, "временные снесены, диагностика выключена", ""

    # ── Прогон ──────────────────────────────────────────────────────────────

    def _таблица(self) -> dict[str, Callable[[], tuple[str, str, str]]]:
        """Таблица, а не ветвление: незнакомый id виден сразу."""
        return {"конфигурация": self._конфигурация, "компоненты": self._компоненты,
                "служба": self._служба, "соединение": self._соединение,
                "поток_до": self._поток_до, "поток_после": self._поток_после,
                "уборка": self._уборка}

    def прогнать(self, режим: str = "быстрая", только: set[str] | None = None) -> Отчёт:
        """Прогон режима. Уборка идёт ВСЕГДА, даже когда проверка до неё отказала.

        `только` сужает набор до названных id: отдельные пункты меню («Состояние», «Соединение»)
        это входы в ЭТОТ механизм, а не его копии.
        """
        таблица = self._таблица()
        отчёт = Отчёт(режим=режим)
        проверки = [п for п in self.проверки(режим) if только is None or п.id in только]
        try:
            for проверка in проверки:
                if проверка.id == "уборка":
                    continue
                исполнитель = таблица.get(проверка.id)
                if исполнитель is None:
                    raise ContractError(
                        "SCHEMA_INVALID", f"Проверка `{проверка.id}` объявлена, а исполнителя нет.",
                        reason="Каталог и таблица исполнителей обязаны совпадать.")
                исход, причина, дальше = исполнитель()
                отчёт.итоги.append(Итог(проверка, исход, причина, дальше))
        finally:
            for проверка in [п for п in проверки if п.id == "уборка"]:
                исход, причина, дальше = self._уборка()
                отчёт.итоги.append(Итог(проверка, исход, причина, дальше))
            отчёт.путь = self._путь
        return отчёт

    # ── Мелочь ──────────────────────────────────────────────────────────────

    def _бинарь(self, имя: str) -> str | None:
        объявлен = self._объявление["бинари"].get(имя, {})
        в_дереве = Path(объявлен.get("в_дереве", ""))
        путь = в_дереве if в_дереве.is_absolute() else self.корень / в_дереве
        return str(путь) if путь.exists() else shutil.which(имя)

    def _версия(self, путь: str, имя: str) -> str:
        флаги = self._объявление["бинари"].get(имя, {}).get("версия", ["--version"])
        try:
            done = subprocess.run([путь, *флаги], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return "версия не отвечает"
        строки = (done.stdout or done.stderr).strip().splitlines()
        return строки[0] if строки else "версия не названа"

    def _поднять_приёмник(self, принято: list[bytes]) -> HTTPServer:
        """Точка наблюдения на приёме: слушает только петлю и живёт до уборки."""
        class Ручка(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 — имя задано базовым классом
                длина = int(self.headers.get("Content-Length") or 0)
                принято.append(self.rfile.read(длина))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *_: Any) -> None:
                """Пусто намеренно: вывод диагностики — отчёт, а не поток строк в stderr."""

        сервер = HTTPServer(("127.0.0.1", 0), Ручка)
        threading.Thread(target=сервер.serve_forever, daemon=True).start()
        self._приёмник = сервер
        return сервер

    @staticmethod
    def _сумма(поток: dict[str, str]) -> str:
        """Считается по полям, которые есть у обеих сторон, — сама себя в счёт не берёт."""
        основа = "|".join(str(поток[к]) for к in ("метка", "id", "время"))
        return hashlib.sha256(основа.encode("utf-8")).hexdigest()[:32]


def быстрая(tunnel: Any = None, конец: str = "client") -> Отчёт:
    """Соединение, компоненты, служба, конфигурация — без лишнего трафика."""
    return Диагностика(tunnel, конец).прогнать("быстрая")


def полная(tunnel: Any = None, конец: str = "client") -> Отчёт:
    """Быстрая плюс тестовый поток с двумя точками наблюдения и уборкой."""
    return Диагностика(tunnel, конец).прогнать("полная")
