"""
tests/quick/test_install_cost.py — счёт установки: цена считается, а неизвестное называется.

Сеть здесь не нужна и не зовётся: индекс колёс — подставной транспорт двери сервера (`Fetcher`), его журнал —
истина о том, какие запросы ушли, куда (IP), под каким именем, с каким сроком и представлением клиента. Судится
РАЗБОР ответа — что делает счёт, когда pip не отработал, когда индекс не сообщил размер, отказал, увёл во
внутреннюю сеть или не достигнут вовсе, и когда места на диске меньше требуемого или его не прочитать. Живой прогон
с сетью делает сам установщик, и его цифры меняются с каждым релизом колёс — тест на них не опирается.
"""
import contextlib
import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import httpx
import yaml

import configure     # меню настройки: зовёт цену перед наборами расчёта и печатает отказ
import install_cost  # счёт установки: его же читает configure.py перед наборами расчёта
import core.packages
from core.declaration import Declared
from core.declaration_schemas import PackagesDecl
from core.packages import PackageError
from core.providers.download import Unreached

_checks = 0
_fails = []


def ok(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


ИМЕНА = {"files.example": ["151.101.1.1"], "cdn.example": ["104.16.1.1"], "inner.example": ["10.0.0.5"],
         "down.example": ["151.101.9.9"]}


class _Индекс:
    """Подставной индекс колёс: разрешение имён, журнал запросов, ответ по пути адреса; down.example — перегружен."""

    def __init__(self, перегрузка: int = 0):
        self.журнал: list[dict] = []
        self.спрошены: list[str] = []
        self.перегрузка = перегрузка            # столько первых ответов /busy.whl — 503
        self.паузы: list[float] = []

    def resolve(self, host, port, *args, **kwargs):
        self.спрошены.append(host)
        if host not in ИМЕНА:
            raise socket.gaierror(f"нет имени {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ИМЕНА[host]]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.журнал.append({"ip": request.url.host, "host": request.headers.get("host"), "path": request.url.path,
                            "method": request.method, "agent": request.headers.get("user-agent"),
                            "timeout": request.extensions.get("timeout", {}).get("read")})
        path = request.url.path
        if request.headers.get("host") == "down.example" or (path == "/busy.whl" and self.перегрузка):
            self.перегрузка -= path == "/busy.whl"
            return httpx.Response(503)
        answers = {"/light.whl": (200, {"content-length": "2000000"}), "/silent.whl": (200, {}),
                   "/moved.whl": (302, {"location": "https://cdn.example/real.whl"}),
                   "/real.whl": (200, {"content-length": "3000000"}), "/closed.whl": (403, {}),
                   "/busy.whl": (200, {"content-length": "1000000"})}
        status, headers = answers.get(path, (404, {}))
        return httpx.Response(status, headers=headers)

    def швы(self) -> dict:
        return {"transport": httpx.MockTransport(self.handle), "resolve": self.resolve, "sleep": self.паузы.append,
                "proxies": dict}


print("== 1. Неизвестный размер называется с причиной, а не считается нулём ==")
_машина = {"свободно_мб": 100000, "озу_мб": 8000, "ядра": 4}
_с_молчащими = {"цель": ".[gpu-amd]", "пакетов": 76, "скачать_мб": 6494.0,
                "без_размера": [{"пакет": "torch", "почему": "HEAD download.pytorch.org: нет Content-Length"}],
                "ошибка": ""}
_строки, _хватает = install_cost.отчёт([_с_молчащими], _машина)
ok(any("torch — HEAD download.pytorch.org: нет Content-Length" in с for с in _строки),
   "молчащий индекс назван поимённо и с причиной — иначе его ноль сложится с остальными и соврёт в меньшую")
ok(any("больше" in с and "(torch)" in с for с in _строки),
   "сказано, что итог БОЛЬШЕ посчитанного, и чей размер не известен: неполный замер обязан быть слышен")

print("\n== 1б. Сбор цены дверью сервера: длина, переход, молчание, отказ, внутренняя сеть, перегрузка ==")
# Подменяется состав (pip) и сеть (транспорт и имена двери); суд адреса, повтор и разбор длины — настоящие.
_прежний_состав = install_cost.состав
_пакеты = [("лёгкий", "https://files.example/light.whl"), ("молчащий", "https://files.example/silent.whl"),
           ("за-переходом", "https://files.example/moved.whl"), ("закрытый", "https://files.example/closed.whl"),
           ("внутренний", "https://inner.example/x.whl"), ("занятый", "https://files.example/busy.whl"),
           ("своё-дерево", "file:///дерево")]
install_cost.состав = lambda цель, индекс="", **_: (
    [{"metadata": {"name": имя}, "download_info": {"url": url}} for имя, url in _пакеты], "")
_индекс = _Индекс(перегрузка=1)
install_cost.швы = _индекс.швы()
_ц = install_cost.цена(".[проба]")
_политика = install_cost.ИНДЕКС.model.index.fetch
_почему = {м["пакет"]: м["почему"] for м in _ц["без_размера"]}
ok(_ц["скачать_мб"] == 6.0 and _ц["пакетов"] == 7,
   f"сложены только названные длины: 2 + 3 (за переходом) + 1 (после повтора) МБ; пакеты — все 7, включая своё "
   f"дерево ({_ц['скачать_мб']} МБ, {_ц['пакетов']})")
ok(list(_почему) == ["молчащий", "закрытый", "внутренний"]
   and _почему["молчащий"] == "HEAD files.example: нет Content-Length"
   and "PACKAGE_INDEX_FAILED" in _почему["закрытый"] and "403" in _почему["закрытый"]
   and "DOWNLOAD_FORBIDDEN" in _почему["внутренний"],
   f"без размера — ровно молчащий, отказавший 403 и внутренний, каждый со своей причиной, а не нулём ({_почему})")
ok(not [x for x in _индекс.журнал if x["ip"] == "10.0.0.5"] and "inner.example" in _индекс.спрошены,
   "имя, ведущее во внутреннюю сеть, судится разрешением — туда ни запроса")
ok([x["ip"] for x in _индекс.журнал if x["path"] == "/real.whl"] == ["104.16.1.1"],
   "переход на CDN судится и разрешается заново; длина — конечного ответа")
ok(_индекс.журнал and all(x["method"] == "HEAD" and x["timeout"] == _политика.timeout_sec for x in _индекс.журнал)
   and {x["agent"] for x in _индекс.журнал if x["host"] == "files.example"} == {install_cost.ЗАГОЛОВКИ["User-Agent"]},
   f"каждый запрос — HEAD со сроком из packages.yaml; индексу клиент представляется как pip "
   f"({sorted({(x['host'], x['agent'], x['timeout']) for x in _индекс.журнал})})")
ok(_индекс.паузы == [_политика.backoff_sec] and [x["path"] for x in _индекс.журнал].count("/busy.whl") == 2,
   f"перегрузка (503) повторена общим циклом двери с паузой политики, вторая попытка дала длину ({_индекс.паузы})")
ok(not any(isinstance(п, Unreached) for п in _почему.values()),
   "хост, ответивший без длины, 403 или судом адреса, — достигнут: его следующие пакеты спрошены, а не сняты разом")

print("\n== 1в. Хост, не достигнутый после всех попыток, — одно решение на прогон: остальным его пакетам та же причина ==")
_индекс = _Индекс()
install_cost.швы = _индекс.швы()
_пакеты = [("лежащий-1", "https://down.example/a.whl"), ("лежащий-2", "https://down.example/b.whl"),
           ("лёгкий", "https://files.example/light.whl")]
_ц = install_cost.цена(".[проба]")
_почему = {м["пакет"]: м["почему"] for м in _ц["без_размера"]}
_ждём_паузы = [min(_политика.backoff_sec * 2 ** n, _политика.backoff_max_sec) for n in range(_политика.attempts - 1)]
_к_лежащему = [x["path"] for x in _индекс.журнал if x["host"] == "down.example"]
ok(_к_лежащему == ["/a.whl"] * _политика.attempts and _индекс.паузы == _ждём_паузы,
   f"первый пакет лежащего хоста — {_политика.attempts} попыток с паузами политики, второй — ни одного запроса "
   f"({_к_лежащему}, {_индекс.паузы})")
ok(isinstance(_почему.get("лежащий-1"), Unreached) and _почему.get("лежащий-2") is _почему.get("лежащий-1"),
   f"у второго — тот же объект причины, что вернула дверь первому (метка Unreached дошла без пересборки) "
   f"({ {к: (type(п).__name__, п) for к, п in _почему.items()} })")
ok(_ц["скачать_мб"] == 2.0 and "/light.whl" in [x["path"] for x in _индекс.журнал],
   f"другой хост того же прогона спрашивается как обычно ({_ц['скачать_мб']} МБ)")

print("\n== 2. Отказ pip — это «не посчитано», а не «влезет» ==")
_сломанный = {"цель": ".[нет-такой-группы]", "пакетов": 0, "скачать_мб": 0.0,
              "без_размера": [], "ошибка": "pip вернул 1"}
_строки, _хватает = install_cost.отчёт([_сломанный], _машина)
ok(any("не посчитано" in с and "pip вернул 1" in с for с in _строки),
   "причина названа дословно — «не посчитано» без причины неотличимо от «ноль»")

print("\n== 3. Нехватка места — твёрдый отказ с обоими числами; непрочитанное место — не нехватка ==")
_тесно = {"свободно_мб": 500, "озу_мб": 8000, "ядра": 4}
_дорого = {"цель": ".[gpu-amd]", "пакетов": 76, "скачать_мб": 6494.0,
           "без_размера": [], "ошибка": ""}
_строки, _хватает = install_cost.отчёт([_дорого], _тесно)
ok(_хватает is False, "вердикт «не хватает»: свободного меньше, чем размер скачивания")
ok(any("НЕ ХВАТАЕТ МЕСТА" in с and "6494" in с and "500" in с for с in _строки),
   "в отказе оба числа — нужное и свободное; без них он не проверяем")
_слепо = {"свободно_мб": None, "озу_мб": 8000, "ядра": 4}
_строки, _хватает = install_cost.отчёт([_дорого], _слепо)
ok(_хватает is None and _строки[-1] == "  свободное место не прочитано — хватит ли 6494 МБ, судить нечем"
   and not any("НЕ ХВАТАЕТ МЕСТА" in с for с in _строки),
   f"место не прочитано — вердикт «судить нечем» (None), причина — последней строкой, а не «не хватает» ({_хватает})")

print("\n== 4. Цена складывается по целям, а свободное место берётся у машины ==")
_дёшево = {"цель": ".", "пакетов": 66, "скачать_мб": 115.0, "без_размера": [], "ошибка": ""}
_строки, _хватает = install_cost.отчёт([_дёшево, _дорого], _тесно)
ok(not _хватает and any("6609" in с for с in _строки),
   "цели складываются: 115 + 6494 — иначе каждая по отдельности «влезет», а вместе нет")
ok(any("свободно на диске" in с and "ОЗУ" in с for с in _строки),
   "машина в отчёте названа фактами probe, а не пересказом")

print("\n== 5. Состав спрашивается у pip, и его отказ доезжает причиной ==")
_пакеты_pip, _ошибка = _прежний_состав(".[этой-группы-нет]", python=sys.executable, таймаут=120)
ok(_пакеты_pip == [] and bool(_ошибка),
   f"несуществующая группа даёт причину, а не пустой успех ({_ошибка[:60]})")

print("\n== 6. Недоступный адрес — «длина не узнана» с причиной после всех попыток политики, а не ноль ==")
# Смена поведения B1: прежде недоступный адрес давал 0 и ходил в живой DNS; теперь имена — подставные.
_индекс = _Индекс()
install_cost.швы = _индекс.швы()
_длина = install_cost.размер("https://absent.example/x.whl", _политика)
ok(_длина == (None, "HEAD absent.example: gaierror") and isinstance(_длина[1], Unreached)
   and _индекс.паузы == _ждём_паузы and not _индекс.журнал,
   f"имя не разрешилось — None с родом обрыва и меткой Unreached после {_политика.attempts} попыток и пауз "
   f"{_ждём_паузы}, а не 0; запроса нет ({_длина}, {_индекс.паузы})")

print("\n== 7. Установка модели отказывает по МЕСТУ, а не только предупреждает в списке ==")
import models  # тот же скрипт, что зовут меню настройки и CLI


from core.providers import ProviderError


class _Каталог:
    """Каталог ровно в той части, которую спрашивает CLI: снимок места (`место_под`) и вердикт влезания."""

    def __init__(self, мб, вердикт="yes", отказ=None):
        self.models_dir = ROOT
        self._мб, self._вердикт, self._отказ = мб, вердикт, отказ

    def installed_as(self, model_id, kind):
        return None                             # здесь судится место, а не «уже стоит»

    def место_под(self, model_id, kind, force=False):
        if self._отказ:
            raise self._отказ                   # нехватка места и прочие отказы — у каталога (`место_под`)
        return {"нужно_мб": self._мб, "свободно_мб": 100000.0, "entry": {"id": model_id, "mb": self._мб}, "plan": []}

    def with_fit(self, rows, hardware=None):
        return [{**rows[0], "fit": {"verdict": self._вердикт, "need_mb": 99999,
                                    "device": "процессор", "available_mb": 1000}}]


class _Ставит(_Каталог):
    """Каталог, который помнит, дошли ли до установки: без этого проверка живёт, но не подключена."""

    ставили = False
    # Опись нужна печати ПОСЛЕ установки: без неё снятая проверка роняет набор трейсом, и
    # мутация ловится падением вместо названного провала — улика хуже, чем могла бы быть.
    inventory_file = ROOT / "vendor" / "models" / "installed.yaml"

    def install_planned(self, место, progress=None):
        _Ставит.ставили = True
        return {**место["entry"], "path": "x"}


for _вердикт, _ждём, _что in (
        ("yes", False, "веса и расчёт влезают — предупреждения нет, установка идёт молча"),
        ("no", True, "веса влезли, а расчёт нет — это предупреждение, а не отказ: диск и память разные вопросы")):
    _к = _Каталог(1.0, вердикт=_вердикт)
    _печать = io.StringIO()
    with contextlib.redirect_stdout(_печать):
        models._предупредить_о_расчёте(_к, _к.место_под("проба", "tts"))
    ok(("расчёт — нет" in _печать.getvalue()) is _ждём, f"{_что} ({_печать.getvalue().strip() or 'тишина'})")


class _Список(_Каталог):
    """Каталог списка `local`: одна строка и вердикт расчёта с объёмами `need_mb`/`available_mb` из события."""

    def __init__(self, need_mb, available_mb):
        super().__init__(1.0)
        self._need, self._free = need_mb, available_mb

    def available(self, kind, limit=0, language="", describe=False):
        return [{"id": "проба", "mb": 2.5}]

    def with_fit(self, rows, hardware=None):
        return [{**rows[0], "fit": {"verdict": "unknown" if self._need is None else "yes", "need_mb": self._need,
                                    "device": "видеокарта", "available_mb": self._free}}]


_прежний_список = models._catalog
for _need, _free, _ждём_строки, _что in (
        (None, None, ("unknownне известно", "Вердикт считался по: видеокарта (не известно)"),
         "объёмы неизвестны (ZU-2: null, а не 0) — печать «не известно», ни «None МБ», ни «0 МБ»"),
        (0, 512, ("yes    0 МБ", "Вердикт считался по: видеокарта (512 МБ)"),
         "законный: известный 0 и известный объём печатаются числами")):
    models._catalog = lambda: _Список(_need, _free)
    _печать = io.StringIO()
    with contextlib.redirect_stdout(_печать):
        _код = models.main(["models.py", "local", "--kind", "tts"])
    _текст = _печать.getvalue()
    ok(_код == 0 and all(s in _текст for s in _ждём_строки) and "None" not in _текст
       and " 0 МБ" not in _текст.replace("yes    0 МБ", ""), f"{_что} ({_текст.strip()})")
models._catalog = _прежний_список

_прежний = models._catalog
models._catalog = lambda: _Ставит(10 ** 9, отказ=ProviderError(
    "MODEL_NO_DISK_SPACE", facts={"model": "проба", "need_mb": 1000000000.0, "free_mb": 100000}))
_Ставит.ставили = False
_вывод = io.StringIO()
with contextlib.redirect_stdout(_вывод):
    _код = models.main(["models.py", "install", "проба", "--kind", "tts"])
ok(_код == 2 and not _Ставит.ставили and "MODEL_NO_DISK_SPACE" in _вывод.getvalue()
   and "1000000000" in _вывод.getvalue() and "100000 МБ" in _вывод.getvalue(),
   f"проверка ПОДКЛЮЧЕНА: места меньше весов — отказ каталога кодом с обоими числами, до скачивания не дошло "
   f"(код {_код}, установка {'была' if _Ставит.ставили else 'не начата'})")

models._catalog = lambda: _Ставит(1.0, отказ=ProviderError(
    "MODEL_SIZE_UNKNOWN", facts={"model": "проба", "parts": "unet/w.safetensors"}))
_вывод = io.StringIO()
with contextlib.redirect_stdout(_вывод):
    _код = models.main(["models.py", "install", "проба", "--kind", "tts"])
models._catalog = _прежний
ok(_код == 2 and not _Ставит.ставили,
   "размер не назван источником — отказ кодом, а не «не проверено, ставим»: незнание не выдаётся за «хватает»")
ok("MODEL_SIZE_UNKNOWN" in _вывод.getvalue() and "unet/w.safetensors" in _вывод.getvalue()
   and "'проба'" in _вывод.getvalue(),
   f"CLI печатает текст реестра с подставленными фактами, а не пустое «✗ КОД: » ({_вывод.getvalue().strip()})")

print("\n== 8. Декларация индекса — схема двери: битый ключ — отказ кодом с полем до pip ==")
ok(isinstance(install_cost.ИНДЕКС.model, PackagesDecl), "боевой packages.yaml проходит схему целиком")
_папка = Path(tempfile.mkdtemp())
_звали_pip: list[str] = []
install_cost.состав = lambda цель, индекс="", **_: (_звали_pip.append(цель), ([], ""))[1]
_прежний_индекс = install_cost.ИНДЕКС
for _что, _правка, _ждём in (
        ("нет ключа", lambda f: f.pop("attempts"), "index.fetch.attempts (missing)"),
        ("ключ тела у HEAD", lambda f: f.update(max_bytes=0), "index.fetch.max_bytes (extra_forbidden)"),
        ("нет кода отказа", lambda f: f.update(codes={}), "index.fetch.codes.failed (missing)"),
        ("код тела у HEAD", lambda f: f["codes"].update(corrupt="X"), "index.fetch.codes.corrupt (extra_forbidden)"),
        ("пауза выше потолка", lambda f: f.update(backoff_sec=f["backoff_max_sec"] + 1),
         "index.fetch.backoff_sec (backoff_over_ceiling)")):
    _звали_pip.clear()
    _живая = yaml.safe_load((ROOT / "config" / "packages.yaml").read_text(encoding="utf-8"))
    _правка(_живая["index"]["fetch"])
    (_папка / "packages.yaml").write_text(yaml.safe_dump(_живая, allow_unicode=True), encoding="utf-8")
    install_cost.ИНДЕКС = Declared(_папка / "packages.yaml", PackageError, "индекса пакетов", schema=PackagesDecl)
    try:
        install_cost.цена(".")
        _отказ = None
    except PackageError as e:
        _отказ = e
    ok(_отказ is not None and _отказ.code == "DECLARATION_INVALID" and _ждём in _отказ.facts.get("details", "")
       and not _звали_pip,
       f"{_что}: {_ждём} — DECLARATION_INVALID с полем и правилом, pip не звался "
       f"({_отказ and (_отказ.code, _отказ.facts)}, pip {_звали_pip})")
install_cost.ИНДЕКС, install_cost.состав = _прежний_индекс, _прежний_состав
shutil.rmtree(_папка, ignore_errors=True)

print("\n== 9. Код выхода: «не хватает» и «не прочитано» — свои, падение — код CPython; их не спутать ==")
_код_падения = subprocess.run([sys.executable, "-c", "raise RuntimeError"], capture_output=True).returncode
_прежняя_цена, _прежняя_машина = install_cost.цена, install_cost.машина
install_cost.цена = lambda цель, индекс="": _дорого
_коды = {}
for _имя, _факты in (("хватает", _машина), ("не хватает", _тесно), ("не прочитано", _слепо)):
    install_cost.машина = lambda _ф=_факты: _ф
    with contextlib.redirect_stdout(io.StringIO()):
        _коды[_имя] = install_cost.main(["--цель", ".[gpu-amd]"])
install_cost.цена, install_cost.машина = _прежняя_цена, _прежняя_машина
ok(_коды == {"хватает": 0, "не хватает": install_cost.НЕ_ХВАТАЕТ, "не прочитано": install_cost.НЕ_СУДИМО}
   and len({install_cost.НЕ_ХВАТАЕТ, install_cost.НЕ_СУДИМО, _код_падения, 2}) == 4,
   f"«не хватает» и «не прочитано» отвечают своими кодами — не кодом падения ({_код_падения}), не ошибкой вызова "
   f"argparse (2) и не друг другом ({_коды})")
_пусто = tempfile.mkdtemp()
_упал = subprocess.run([sys.executable, str(ROOT / "scripts" / "install_cost.py")], capture_output=True, text=True,
                       env={**os.environ, "MCP_CONFIG": _пусто})
shutil.rmtree(_пусто, ignore_errors=True)
ok(_упал.returncode == _код_падения and "НЕ ХВАТАЕТ" not in _упал.stdout and "PackageError" in _упал.stderr,
   f"цена не посчитана (нет декларации индекса) — код падения, а не «не хватает»: читатель кода их различит "
   f"(код {_упал.returncode}, {_упал.stderr.strip().splitlines()[-1:]})")

print("\n== 10. Конфигуратор: отказ установки называет причину отчёта; «не прочитано» — не «не хватает» ==")


class _План:
    """Пакеты среды расчёта ровно в той части, которую спрашивает меню: план и установка (помнит, звали ли)."""

    поставили = False

    def __init__(self, project):
        self.project = project

    def judge(self, names):
        return {"plan": [{"requirement": "torch", "have": None}], "checked": 1}

    def install(self, names, index="", announce=None):
        _План.поставили = True
        return {"outcome": "поставлено", "checked": 1, "installed": ["torch"]}


_прежние = core.packages.Packages, install_cost.цена, install_cost.машина
core.packages.Packages = _План
install_cost.цена = lambda цель, индекс="": _дорого
_печать = {}
for _имя, _факты in (("не прочитано", _слепо), ("не хватает", _тесно)):
    install_cost.машина = lambda _ф=_факты: _ф
    _вывод = io.StringIO()
    with contextlib.redirect_stdout(_вывод):
        configure._поставить("cpu", "")
    _печать[_имя] = _вывод.getvalue()
core.packages.Packages, install_cost.цена, install_cost.машина = _прежние
ok("свободное место не прочитано" in _печать["не прочитано"] and "не хватает" not in _печать["не прочитано"].lower()
   and "отменена" in _печать["не прочитано"] and not _План.поставили,
   f"место не прочитано — меню печатает «не прочитано» и отменяет установку, слов «не хватает» нет "
   f"({_печать['не прочитано'].strip().splitlines()[-2:]})")
ok("НЕ ХВАТАЕТ МЕСТА" in _печать["не хватает"] and "отменена" in _печать["не хватает"] and not _План.поставили,
   f"законный: места меньше нужного — нехватка с обоими числами, установка отменена "
   f"({_печать['не хватает'].strip().splitlines()[-2:]})")

print(f"\nПроверок: {_checks}, провалов: {len(_fails)}")
for _f in _fails:
    print(f"  - {_f}")
sys.exit(1 if _fails else 0)
