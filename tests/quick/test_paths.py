#!/usr/bin/env python3
"""tests/quick/test_paths.py — запись слоя файлов: замена, создание без затирания, остаток с владельцем.

Под настоящим именем лежит либо старый файл, либо новый целиком; права — с рождения, а не после записи; создание не
затирает чужой файл и в гонке процессов даёт ровно одного победителя; остаток записи узнаётся обходом и не выдаётся за
целый файл. Отказы — через путь доставки (`Engine.refusal`).
"""
import json
import multiprocessing
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from types import SimpleNamespace

import psutil

from core import owner_only
from core.auth import ensure_digest
from core.contracts import ContractError
from core.declaration import Declaration
from core.engine import Engine
from core.ids import LinkRegistry
from core.integrity import InstanceIdentity
from core.paths import REMNANT, Files
from core.reactions import Reactions
from core.runner import RunnerSupervisor
from core.secrets import ChannelSecrets
import core.write_policy as write_policy_module
from core.write_policy import WritePolicy
from tests.harness.access import mode

_checks = 0
_fails: list[str] = []
_UMASK = os.umask(0)
os.umask(_UMASK)


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


def posix_check(label, cond, detail=""):
    """Проверка битов группы и прочих: на Windows их нет (режим — только флаг «только чтение»), там она не судится."""
    if os.name == "nt":
        print(f"  ── не судится на Windows: {label} — битов группы и прочих там нет")
    else:
        check(label, cond, detail)


def leftovers(folder: Path) -> list[str]:
    """Остатки записи в каталоге — по признаку слоя."""
    return sorted(n for n in os.listdir(folder) if n.endswith(f".{REMNANT}"))


class FirstByte:
    """Файл записи слоя: права на диске в момент первого байта — ключ не лежит под чужими правами ни мгновения."""

    def __init__(self, f, modes: list, path: Path | None = None):
        self.f, self.modes, self.path, self.seen = f, modes, path, False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self.f.__exit__(*exc)

    def write(self, data):
        if not self.seen:
            self.seen = True
            # POSIX — по дескриптору; на Windows доступ — DACL, оракул читает его по пути.
            on_path = os.name == "nt" and self.path is not None
            self.modes.append(mode(self.path) if on_path else stat.S_IMODE(os.fstat(self.f.fileno()).st_mode))
        return self.f.write(data)

    def __getattr__(self, name):
        return getattr(self.f, name)


def first_bytes(call) -> list[int]:
    """Права каждого файла, который записал вызов, в момент его первого байта."""
    modes: list[int] = []
    born: dict[int, Path] = {}
    real_fdopen, real_create = os.fdopen, owner_only.create

    def creating(path, mode_):
        fd = real_create(path, mode_)
        born[fd] = Path(path)
        return fd

    os.fdopen = lambda fd, *a, **kw: FirstByte(real_fdopen(fd, *a, **kw), modes, born.get(fd))
    owner_only.create = creating
    try:
        call()
    finally:
        os.fdopen, owner_only.create = real_fdopen, real_create
    return modes


def _race(ws: str, data: str, barrier, results) -> None:
    barrier.wait()
    results.put((data, Files(Path(ws)).create("гонка.key", data, mode=0o600)))


def _register_many(ws: str, prefix: str, n: int, barrier) -> None:
    """Процесс-писатель реестра: n регистраций подряд, старт одновременно с соседом."""
    reg = LinkRegistry(Path(ws))
    barrier.wait()
    for i in range(n):
        reg.register({"id": f"{prefix}{i}", "type": "niche", "name": f"{prefix}{i}", "path": f"niches/{prefix}{i}"})


def _set_many(ws: str, home: str, prefix: str, n: int, barrier) -> None:
    """Процесс-писатель ключей канала: n ключей подряд в один файл канала, старт одновременно с соседом."""
    box = ChannelSecrets(Path(ws), Path(home))
    barrier.wait()
    for i in range(n):
        box.set("ch", f"{prefix}{i}", f"sk-{prefix}{i}")


HOLDER = ("import sys, time; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from core.paths import Files\n"
          "with Files(Path(sys.argv[2])).lock(sys.argv[3]):\n"
          "    print('held', flush=True)\n"
          "    sys.stdin.readline() if sys.argv[4] == 'release' else time.sleep(3600)\n")


def main():
    ws = Path(tempfile.mkdtemp(prefix="vpm_paths_"))
    files = Files(ws)
    engine = Engine(reactions=Reactions(ROOT / "config" / "server_reactions.yaml"))

    print("== замена: новый файл, права как у open(), остатка нет ==")
    files.replace("a/new.txt", "один")
    check("содержимое записано", (ws / "a" / "new.txt").read_text(encoding="utf-8") == "один")
    check("права нового — 0o666 без маски процесса", mode(ws / "a" / "new.txt") == 0o666 & ~_UMASK,
          oct(mode(ws / "a" / "new.txt")))
    check("остатка после замены нет", leftovers(ws / "a") == [], leftovers(ws / "a"))

    print("== замена: права заменяемого сохраняются, имя появляется с готовым содержимым и правами ==")
    (ws / "a" / "new.txt").chmod(0o640)
    seen: dict = {}
    real_replace = os.replace

    def watching_replace(src, dst, *a, **kw):
        seen.update(mode=mode(Path(src)), size=Path(src).stat().st_size, src=Path(src).name)
        return real_replace(src, dst, *a, **kw)

    os.replace = watching_replace
    try:
        files.replace("a/new.txt", "второе содержимое")
    finally:
        os.replace = real_replace
    posix_check("в момент появления имени — итоговые права", seen.get("mode") == 0o640, seen)
    check("в момент появления имени — всё содержимое", seen.get("size") == len("второе содержимое".encode()), seen)
    check("временный — остаток слоя рядом (`<файл>.<владелец>.part`)",
          seen.get("src", "").startswith("new.txt.") and seen["src"].endswith(f".{REMNANT}"), seen)
    posix_check("права после замены сохранены", mode(ws / "a" / "new.txt") == 0o640, oct(mode(ws / "a" / "new.txt")))

    print("== замена: процесс умер до os.replace — старый файл цел, остаток помечен обходом ==")
    (ws / "b").mkdir()
    (ws / "b" / "data.json").write_text("СТАРОЕ", encoding="utf-8")
    died = subprocess.run([sys.executable, "-c", (
        "import os, sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from core.paths import Files\n"
        "os.replace = lambda *a, **k: os._exit(9)\n"
        "Files(Path(sys.argv[2])).replace('b/data.json', 'НОВОЕ')"), str(ROOT), str(ws)])
    check("процесс записи оборвался", died.returncode == 9, died.returncode)
    check("под настоящим именем — старый файл целиком", (ws / "b" / "data.json").read_text(encoding="utf-8") == "СТАРОЕ")
    kinds = {e.path: e.kind for e in files.walk("b")}
    rest = [p for p, k in kinds.items() if k == "remnant"]
    check("обход называет остаток остатком, а не файлом", len(rest) == 1 and kinds["b/data.json"] == "file", kinds)
    check("в остатке — недоставленное содержимое", rest and (ws / rest[0]).read_text(encoding="utf-8") == "НОВОЕ")

    print("== замена: сбой записи — исключение наружу, старый файл цел, мусора нет ==")
    (ws / "c").mkdir()
    (ws / "c" / "x.txt").write_text("ЦЕЛОЕ", encoding="utf-8")
    real_fsync = os.fsync

    def failing_fsync(fd):
        raise OSError(28, "No space left on device")

    os.fsync = failing_fsync
    try:
        files.replace("c/x.txt", "НЕ ДОЛЖНО ЛЕЧЬ")
        raised = False
    except OSError:
        raised = True
    finally:
        os.fsync = real_fsync
    check("сбой не проглочен", raised)
    check("файл не тронут", (ws / "c" / "x.txt").read_text(encoding="utf-8") == "ЦЕЛОЕ")
    check("остаток своего сбоя убран", leftovers(ws / "c") == [], leftovers(ws / "c"))

    print("== создание: права с рождения, имя появляется готовым ==")
    seen.clear()
    real_link = os.link
    made = []

    def watching_link(src, dst, *a, **kw):
        seen.update(mode=mode(Path(src)), size=Path(src).stat().st_size)
        return real_link(src, dst, *a, **kw)

    os.link = watching_link
    try:
        born = first_bytes(lambda: made.append(files.create("k/instance.key", "КЛЮЧ", mode=0o600)))
    finally:
        os.link = real_link
    check("создан", made == [True] and (ws / "k" / "instance.key").read_text(encoding="utf-8") == "КЛЮЧ")
    check("в момент первого байта — уже 0o600", born == [0o600], born)
    check("в момент появления имени — 0o600 и всё содержимое",
          (seen.get("mode"), seen.get("size")) == (0o600, len("КЛЮЧ".encode())), seen)
    check("после — 0o600", mode(ws / "k" / "instance.key") == 0o600, oct(mode(ws / "k" / "instance.key")))
    check("остатка нет", leftovers(ws / "k") == [], leftovers(ws / "k"))

    print("== создание поверх существующего — не затирает ==")
    check("второй раз — False", files.create("k/instance.key", "ДРУГОЙ", mode=0o600) is False)
    check("содержимое первого цело", (ws / "k" / "instance.key").read_text(encoding="utf-8") == "КЛЮЧ")
    check("остатка нет и после отказа", leftovers(ws / "k") == [], leftovers(ws / "k"))

    print("== создание: гонка восьми процессов — ровно один победитель, его содержимое ==")
    ctx = multiprocessing.get_context("spawn")
    barrier, results = ctx.Barrier(8), ctx.Queue()
    procs = [ctx.Process(target=_race, args=(str(ws), f"процесс-{i}", barrier, results)) for i in range(8)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    outcome = [results.get() for _ in procs]
    winners = [d for d, won in outcome if won]
    check("ровно один создал", len(winners) == 1, outcome)
    check("на диске — содержимое победителя", winners and (ws / "гонка.key").read_text(encoding="utf-8") == winners[0])
    check("остатков гонки нет", leftovers(ws) == [], leftovers(ws))

    print("== остаток с владельцем: имя, дозапись, публикация ==")
    part = files.remnant("m/model.onnx", "inst42")
    check("имя — <файл>.<владелец>.part рядом", part == (ws / "m" / f"model.onnx.inst42.{REMNANT}").resolve(), part)
    part.parent.mkdir(parents=True)
    part.write_bytes(b"0123")
    with open(part, "ab") as f:
        f.write(b"4567")
    check("обход помечает остаток", {e.path: e.kind for e in files.walk("m")} == {f"m/model.onnx.inst42.{REMNANT}": "remnant"})
    files.publish("m/model.onnx", "inst42")
    check("публикация: файл целиком под настоящим именем", (ws / "m" / "model.onnx").read_bytes() == b"01234567")
    check("публикация: остатка нет", leftovers(ws / "m") == [], leftovers(ws / "m"))
    try:
        files.remnant("m/model.onnx", "a.b")
        check("владелец с точкой отвергнут", False)
    except ValueError:
        check("владелец с точкой отвергнут (иначе обход не узнал бы остаток)", True)
    (ws / "m" / "заметки.part").write_text("x", encoding="utf-8")
    check("файл с одной точкой перед part — не остаток",
          {e.path: e.kind for e in files.walk("m")}.get("m/заметки.part") == "file")

    print("== заметка остатка: валидатор докачки рядом, снимается парой ==")
    files.note("n/w.bin", "job7", {"etag": '"abc"', "host": "cdn.example"})
    check("заметка читается тем же словарём", files.noted("n/w.bin", "job7") == {"etag": '"abc"', "host": "cdn.example"})
    check("имя заметки — <файл>.<владелец>.part.json",
          (ws / "n" / f"w.bin.job7.{REMNANT}.json").is_file(), sorted(os.listdir(ws / "n")))
    files.remnant("n/w.bin", "job7").write_bytes(b"12")
    files.remnant("n/w.bin", "other").write_bytes("чужое".encode())
    kinds = {e.path: e.kind for e in files.walk("n")}
    check("обход помечает и остаток, и заметку остатком",
          kinds == {f"n/w.bin.job7.{REMNANT}": "remnant", f"n/w.bin.job7.{REMNANT}.json": "remnant",
                    f"n/w.bin.other.{REMNANT}": "remnant"}, kinds)
    died = subprocess.run([sys.executable, "-c", (
        "import os, sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from core.paths import Files\n"
        "os.replace = lambda *a, **k: os._exit(9)\n"
        "Files(Path(sys.argv[2])).note('n/w.bin', 'job7', {'etag': 'NEW'})"), str(ROOT), str(ws)])
    check("процесс, убитый посреди записи заметки, оставил прежнюю целой",
          died.returncode == 9 and files.noted("n/w.bin", "job7") == {"etag": '"abc"', "host": "cdn.example"},
          (died.returncode, files.noted("n/w.bin", "job7")))
    for _orphan in [n for n in os.listdir(ws / "n") if n.startswith(f"w.bin.job7.{REMNANT}.json.")]:
        (ws / "n" / _orphan).unlink()  # остаток убитой записи заметки — его снимет уборщик, здесь мешает сверке
    (ws / "n" / f"w.bin.job7.{REMNANT}.json").write_text("{битый", encoding="utf-8")
    try:
        broken = files.noted("n/w.bin", "job7")
    except Exception as e:  # исключение наружу — тоже провал этой проверки, а не падение набора
        broken = e
    check("битая заметка — None, решение за вызывающим", broken is None, repr(broken))
    check("нет заметки — None", files.noted("n/w.bin", "never") is None)
    files.note("n/w.bin", "job7", {"etag": "E2"})
    files.publish("n/w.bin", "job7")
    check("publish: файл на месте, остаток и заметка сняты, чужой остаток цел",
          (ws / "n" / "w.bin").read_bytes() == b"12"
          and sorted(os.listdir(ws / "n")) == sorted(["w.bin", f"w.bin.other.{REMNANT}"]),
          sorted(os.listdir(ws / "n")))
    files.note("n/w.bin", "other", {"etag": "O"})
    files.drop("n/w.bin", "other")
    files.drop("n/w.bin", "other")
    check("drop снимает свой остаток с заметкой и идемпотентен", sorted(os.listdir(ws / "n")) == ["w.bin"],
          sorted(os.listdir(ws / "n")))
    (ws / "n" / "жертва.txt").write_text("ЧУЖИЕ ДАННЫЕ", encoding="utf-8")
    (ws / "n" / f"w.bin.evil.{REMNANT}").symlink_to(ws / "n" / "жертва.txt")
    files.drop("n/w.bin", "evil")
    check("ссылка на месте остатка: drop снял ссылку, цель цела",
          not (ws / "n" / f"w.bin.evil.{REMNANT}").is_symlink()
          and (ws / "n" / "жертва.txt").read_text(encoding="utf-8") == "ЧУЖИЕ ДАННЫЕ")

    print("== закрытый каталог: запись — только с secrets=True, отказ доходит кодом ==")
    try:
        files.replace("ch/.secrets/keys.enc", "x")
        said = None
    except ContractError as e:
        said = engine.refusal(e).error
    check("без secrets — SECRET_ACCESS_DENIED с путём от корня",
          said is not None and said.code == "SECRET_ACCESS_DENIED" and said.facts == {"path": "ch/.secrets/keys.enc"},
          said)
    check("отказ ничего не оставил", not (ws / "ch").exists())
    files.replace("ch/.secrets/keys.enc", "x", mode=0o600, secrets=True)
    check("с secrets=True — записано с правами владельца", mode(ws / "ch" / ".secrets" / "keys.enc") == 0o600)

    print("== места ключей: каждый файл владельца — 0o600 уже в момент первого байта ==")
    home = ws / "home"
    home.mkdir()
    sites = {
        "ключ шифра (secrets._box)": (lambda: ChannelSecrets(ws, home)._box(), home / "instance_secret.key"),
        "файл ключей канала (secrets._write)": (lambda: ChannelSecrets(ws, home).set("ch2", "Prov", "sk-1"),
                                               ws / "ch2" / ".secrets" / "provider_keys.enc"),
        "ключ инстанса (integrity.ensure_key)": (lambda: InstanceIdentity(home, ws).ensure_key(),
                                                 home / "instance_key.pem"),
        "токен раннера (supervisor._write)": (lambda: RunnerSupervisor(SimpleNamespace(
            config={}, need=lambda *path, kind=None: {"state_file": "vendor/runner/state.json"}[path[-1]]), home)._write(
            {"token": "t"}), home / "vendor" / "runner" / "state.json"),
        "отпечаток ключа доступа (auth)": (lambda: ensure_digest(home / ".env", env={}), home / ".env"),
    }
    for what, (call, made_file) in sites.items():
        try:
            born = first_bytes(call)
        except Exception as e:  # отказ места — провал его проверки, а не падение набора
            born = [repr(e)]
        check(f"{what}: 0o600 с первого байта и после", born and set(born) == {0o600} and mode(made_file) == 0o600,
              f"первые байты {[oct(b) if isinstance(b, int) else b for b in born]}, "
              f"после {oct(mode(made_file)) if made_file.exists() else 'нет файла'}")
    key = (home / "instance_key.pem").read_bytes()
    InstanceIdentity(home, ws).ensure_key()
    check("ключ инстанса не перевыпускается вторым стартом", (home / "instance_key.pem").read_bytes() == key)
    check("остатков у мест ключей нет", leftovers(home) == [] and leftovers(home / "vendor" / "runner") == [],
          leftovers(home))

    print("== замок между процессами (FS-09): держит процесс, а не поток; умерший держатель не держит ==")
    lws = ws / "locks"
    lws.mkdir()
    reg = LinkRegistry(lws)
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(ROOT), str(lws), "_id_registry.json", "release"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    check("подпроцесс взял замок реестра", holder.stdout.readline() == "held\n")
    writer = threading.Thread(target=lambda: reg.register({"id": "A1", "type": "niche", "name": "a", "path": "niches/a"}))
    writer.start()
    writer.join(1.0)
    waited = writer.is_alive()
    holder.stdin.write("go\n")
    holder.stdin.flush()
    holder.wait(30)
    writer.join(30)
    check("запись реестра ждёт, пока замок держит другой процесс, и проходит после", waited and reg.get("A1") is not None,
          f"ждала={waited}, записано={reg.get('A1') is not None}")
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(ROOT), str(lws), "_id_registry.json", "die"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    holder.stdout.readline()
    holder.kill()
    holder.wait(30)
    late = Files(lws).lock("_id_registry.json")
    try:
        late.acquire(timeout=10)
        freed = late.is_locked
        late.release()
    except Exception as e:
        freed = repr(e)
    check("держатель убит SIGKILL — замок свободен сразу, вечного замка нет", freed is True, freed)

    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    procs = [ctx.Process(target=_register_many, args=(str(lws), p, 20, barrier)) for p in ("P", "Q")]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    ids = {e["id"] for e in LinkRegistry(lws).all()}
    lost = sorted({f"{p}{i}" for p in "PQ" for i in range(20)} - ids)
    check("два процесса пишут реестр одновременно — ни одна запись не потеряна", not lost, f"потеряны: {lost}")
    check("замок — отдельный файл `<реестр>.lock`, данные — целый JSON (их меняет os.replace)",
          (lws / "_id_registry.json.lock").is_file()
          and isinstance(json.loads((lws / "_id_registry.json").read_text(encoding="utf-8") or "null"), dict),
          sorted(os.listdir(lws)))

    barrier = ctx.Barrier(2)
    procs = [ctx.Process(target=_set_many, args=(str(lws), str(home), p, 10, barrier)) for p in ("K", "L")]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    try:
        keys = {s["provider"] for s in ChannelSecrets(lws, home).status("ch")}
    except Exception as e:  # нечитаемый файл ключей — провал этой проверки, а не падение набора
        keys = {repr(e)}
    lost = sorted({f"{p}{i}" for p in "KL" for i in range(10)} - keys)
    check("два процесса кладут ключи канала одновременно — ни один не потерян", not lost, f"потеряны: {lost}")
    check("каталог секретов канала — 0700", mode(lws / "ch" / ".secrets") == 0o700, oct(mode(lws / "ch" / ".secrets")))

    print("== место на диске: запись по пути клиента судится до первого байта (FS-12) ==")
    policy = WritePolicy(ROOT / "config")
    (ws / "место").mkdir()
    (ws / "место" / "старое.md").write_text("СТАРОЕ", encoding="utf-8")
    usage, said = shutil.disk_usage(ws), []
    write_policy_module.shutil = SimpleNamespace(disk_usage=lambda p: usage._replace(free=5))  # на диске 5 байт
    try:
        for write in (lambda: files.replace("место/старое.md", "НОВОЕ ДЛИННЕЕ", policy=policy),
                      lambda: files.create("место/новое.md", "НОВОЕ ДЛИННЕЕ", policy=policy)):
            try:
                write()
                said.append(None)
            except ContractError as e:
                said.append(engine.refusal(e).error)
        files.replace("место/малое.md", "ок", policy=policy)
    finally:
        write_policy_module.shutil = shutil
    size = len("НОВОЕ ДЛИННЕЕ".encode("utf-8"))
    check("не помещается — NO_DISK_SPACE через путь доставки: путь от корня, размер, свободно",
          [(s.code, s.facts) if s else None for s in said]
          == [("NO_DISK_SPACE", {"path": "место/старое.md", "size": size, "free": 5}),
              ("NO_DISK_SPACE", {"path": "место/новое.md", "size": size, "free": 5})], said)
    check("отказ до первого байта: прежний файл цел, нового нет, остатков нет",
          (ws / "место" / "старое.md").read_text(encoding="utf-8") == "СТАРОЕ"
          and not (ws / "место" / "новое.md").exists() and leftovers(ws / "место") == [], sorted(os.listdir(ws / "место")))
    check("помещается — пишется", (ws / "место" / "малое.md").read_text(encoding="utf-8") == "ок")

    print("== объявление: тип значения судится у источника (Declaration.need), а не сырым ValueError дальше ==")

    def refused(call) -> object:
        """Что увидит клиент: отказ через путь доставки, имя чужого исключения — или None, если отказа не было."""
        try:
            call()
        except Exception as e:  # проверка называет и чужое исключение, а не падает на нём
            out = engine.refusal(e)
            return out.error if out else type(e).__name__
        return None

    decl_dir = Path(tempfile.mkdtemp(prefix="vpm_decl_"))
    (decl_dir / "d.yaml").write_text("a:\n  n: 5\n  f: 2\n  s: abc\n  b: true\n", encoding="utf-8")
    decl = Declaration(decl_dir / "d.yaml", ContractError, "пробы")
    check("тип совпал — значение как есть; int годится там, где читают float; без типа — как раньше",
          decl.need("a", "n", kind=int) == 5 and decl.need("a", "f", kind=float) == 2 and decl.need("a", "s") == "abc")
    for key, kind, rule in (("s", int, "int_type"), ("b", int, "int_type"), ("n", str, "string_type")):
        said = refused(lambda: decl.need("a", key, kind=kind))
        check(f"`a.{key}` как {kind.__name__} — DECLARATION_INVALID с путём и правилом схемы ({rule})",
              getattr(said, "code", None) == "DECLARATION_INVALID" and f"a.{key}" in said.message
              and [(x["field"], x["rule"]) for x in said.errors] == [(f"a.{key}", rule)], said)
    (decl_dir / "firewall.yaml").write_text("write_allowlist:\n  enabled: true\n  extensions: ['.md']\n"
                                            "file_limits:\n  read_bytes: 10\n  write_bytes: много\n", encoding="utf-8")
    said = refused(lambda: files.replace("тип.md", "x", policy=WritePolicy(decl_dir)))
    check("предел записи строкой в объявлении — DECLARATION_INVALID через путь доставки, файла нет",
          getattr(said, "code", None) == "DECLARATION_INVALID" and "write_bytes" in said.message
          and not (ws / "тип.md").exists(), said)

    print("== остаток записи на платформе без chmod по дескриптору (Windows до 3.13) и отказ посреди записи ==")

    def open_fds() -> int:
        # Через psutil: /proc/self/fd есть только в Linux; num_fds — POSIX, num_handles — Windows.
        proc = psutil.Process()
        return proc.num_fds() if hasattr(proc, "num_fds") else proc.num_handles()

    real_supports, real_fsync, real_fchmod = os.supports_fd, os.fsync, getattr(os, "fchmod", None)
    os.supports_fd = real_supports - {os.chmod}
    if real_fchmod is not None:
        del os.fchmod  # факты Windows до 3.13: ни fchmod, ни chmod по дескриптору
    fds_before, modes = open_fds(), []
    try:
        said = refused(lambda: modes.extend(first_bytes(lambda: files.create("win/ключ.key", "секрет", mode=0o600))))
    finally:
        os.supports_fd = real_supports
        if real_fchmod is not None:
            os.fchmod = real_fchmod
    check("без chmod по дескриптору права ставятся по пути — 0o600 уже в момент первого байта",
          said is None and modes == [0o600], (said, modes))
    check("и дескриптор не теряется", open_fds() == fds_before, (fds_before, open_fds()))

    def _fsync_fails(fd):
        raise OSError(5, "сбой диска в пробе")

    fds_before = open_fds()
    os.fsync = _fsync_fails
    try:
        said = refused(lambda: files.create("win/сбой.key", "x", mode=0o600))
    finally:
        os.fsync = real_fsync
    check("отказ посреди записи: остатка нет, имени нет — удаление после закрытия файла (Windows не удаляет открытый)",
          leftovers(ws / "win") == [] and not (ws / "win" / "сбой.key").exists(), leftovers(ws / "win"))
    check("отказ посреди записи не теряет дескриптор", open_fds() == fds_before, (fds_before, open_fds(), said))

    had_binary, real_open, opened = hasattr(os, "O_BINARY"), os.open, []
    if not had_binary:
        os.O_BINARY = 0x8000  # факт Windows: флаг двоичного режима CRT существует
    os.open = lambda path, flags, *a: (opened.append(flags), real_open(path, flags & ~os.O_BINARY, *a))[1]
    try:
        files.create("win/двоичный.key", "a\nb", mode=0o600)
    finally:
        os.open = real_open
        if not had_binary:
            del os.O_BINARY
    written = (ws / "win" / "двоичный.key").read_bytes()
    # На Windows закрытый файл рождается CreateFileW, а не os.open: там судят байты; флаг — подменой os.open на POSIX.
    flagged = os.name == "nt" or (any(f & os.O_CREAT for f in opened)
                                  and all(f & 0x8000 for f in opened if f & os.O_CREAT))
    check("где есть O_BINARY, остаток открыт в двоичном режиме — запись не переводит \\n в \\r\\n",
          written == b"a\nb" and flagged,
          (written, [hex(f) for f in opened]))  # открытие каталога для сброса на диск (O_RDONLY) — не запись, флага не требует
    shutil.rmtree(ws, ignore_errors=True)

    print(f"\n{'=' * 50}")
    print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
    if _fails:
        print("ПРОВАЛЫ:")
        for f in _fails:
            print(f"  - {f}")
        sys.exit(1)
    print("ВСЁ ЗЕЛЁНОЕ ✅")


if __name__ == "__main__":
    main()
