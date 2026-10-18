#!/usr/bin/env python3
"""tests/quick/test_tunnel_supervision.py — надзор туннеля из закоммиченной декларации, без умолчаний в коде.

Политика перезапуска cloudflared и первого подъёма — раздел `надзор` в `config/tunnel_diagnostics.yaml` со схемой у двери
(`TunnelSupervision`). Меряется на двери деклараций — туннель не поднимается, сеть не трогается: значения приходят из
объявления; раздела нет или он битый — отказ кодом с путём ключа, а не молчаливые числа; ключ надзора в машинном
`tunnel.yaml` — отказ с адресом переезда. Каталог деклараций — копия без машинных файлов, как на чистом клоне.
"""
import logging
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml

import core.transport.tunnel as tunnel_module
from core.contracts import ContractError
from core.transport.tunnel import CloudflaredTunnel, TunnelCancelled, TunnelError
from tests.harness.live_server import declarations_copy

_checks = 0
_fails: list[str] = []


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


def built(config: Path, machine: Path | None = None):
    """Туннель, как его строит сервер, на каталоге деклараций `config`; отказ двери — (код, ошибки ввода)."""
    held = os.environ.get("MCP_CONFIG")
    os.environ["MCP_CONFIG"] = str(config)
    try:
        return CloudflaredTunnel(port=8080, config_path=machine)
    except ContractError as e:
        return e.code, e.errors
    finally:
        if held is None:
            os.environ.pop("MCP_CONFIG", None)
        else:
            os.environ["MCP_CONFIG"] = held


def with_section(config: Path, section: dict | None) -> Path:
    """Копия каталога деклараций: раздел `надзор` заменён (`None` — снят)."""
    path = config / "tunnel_diagnostics.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if section is None:
        data.pop("надзор")
    else:
        data["надзор"] = section
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return config


declared = yaml.safe_load((ROOT / "config" / "tunnel_diagnostics.yaml").read_text(encoding="utf-8"))["надзор"]

print("== 1. Чистый клон (машинного tunnel.yaml нет): надзор — из объявления ==")
clean = declarations_copy()
check("в копии деклараций машинного tunnel.yaml нет — как на чистом клоне", not (clean / "tunnel.yaml").exists())
tunnel = built(clean)
check("туннель собран, числа надзора — из раздела объявления, а не из кода",
      isinstance(tunnel, CloudflaredTunnel)
      and (tunnel.retry_base, tunnel.retry_max, tunnel.retry_reset, tunnel.ready_timeout, tunnel.first_attempts)
      == (declared["retry_base_seconds"], declared["retry_max_seconds"], declared["retry_reset_seconds"],
          declared["ready_timeout_seconds"], declared["first_attempts"]),
      tunnel if not isinstance(tunnel, CloudflaredTunnel) else vars(tunnel).get("retry_base"))
shifted = {**declared, "retry_base_seconds": 3, "first_attempts": 5}
tunnel = built(with_section(declarations_copy(), shifted))
check("правка раздела меняет поведение: значение берётся из объявления каждый раз",
      isinstance(tunnel, CloudflaredTunnel) and (tunnel.retry_base, tunnel.first_attempts) == (3, 5),
      tunnel if not isinstance(tunnel, CloudflaredTunnel) else (tunnel.retry_base, tunnel.first_attempts))

print("== 2. Раздела нет или он битый — отказ кодом с путём ключа, а не молчаливые числа ==")
got = built(with_section(declarations_copy(), None))
check("раздела «надзор» нет — отказ декларации, туннель не собран",
      isinstance(got, tuple) and got[0] == "DECLARATION_INVALID" and any("надзор" in str(e) for e in got[1]), got)
got = built(with_section(declarations_copy(), {k: v for k, v in declared.items() if k != "first_attempts"}))
check("нет ключа first_attempts — отказ с путём ключа",
      isinstance(got, tuple) and any("first_attempts" in str(e.get("field", "")) for e in got[1]), got)
got = built(with_section(declarations_copy(), {**declared, "retry_base_seconds": 120}))
check("пауза выше потолка — отказ связи ключей с путём retry_base_seconds",
      isinstance(got, tuple) and any("retry_base_seconds" in str(e.get("field", "")) for e in got[1]), got)

print("== 3. Ключ надзора в машинном tunnel.yaml — отказ с адресом переезда ==")
config = declarations_copy()
machine = config / "tunnel.yaml"
machine.write_text(yaml.safe_dump({"mode": "quick", "local_port": 8080, "retry_max_seconds": 30}), encoding="utf-8")
got = built(config, machine)
check("старый ключ в машинном файле — отказ, ошибка ввода называет ключ и куда он переехал",
      isinstance(got, tuple) and any(e.get("field") == "retry_max_seconds" and "tunnel_diagnostics.yaml → надзор"
                                     in str(e.get("expected", "")) for e in got[1]), got)
machine.write_text(yaml.safe_dump({"mode": "quick", "local_port": 8080}), encoding="utf-8")
check("машинный файл без ключей надзора — читается, туннель собран", isinstance(built(config, machine), CloudflaredTunnel))

print("== 4. Сбой надзора — узел сервера: запись в журнал с номером события, состояние «остановлен», а не тишина ==")
heard: list[logging.LogRecord] = []
ear = logging.Handler(logging.INFO)
ear.emit = heard.append  # type: ignore[method-assign]
logging.getLogger("core.transport.tunnel").addHandler(ear)
HOST = "/host/secret/bin/cloudflared"


def halted(spawn=None, line=None) -> tuple[dict, list[logging.LogRecord]]:
    """Надзор на поддельном процессе: его stdout кончился (процесс умер) или строка разбора падает; конец потока надзора —
    событием (`join` без своего предела)."""
    tunnel = built(declarations_copy())
    assert isinstance(tunnel, CloudflaredTunnel)
    tunnel._proc = SimpleNamespace(stdout=iter([line] if line else []), poll=lambda: 1)   # type: ignore[assignment]
    tunnel._sleep_interruptible = lambda delay: None                                     # type: ignore[method-assign]
    if spawn is not None:
        tunnel._spawn = spawn                                                              # type: ignore[method-assign]
    if line is not None:
        def broken(raw):
            raise ValueError(f"строка не разбирается: {raw}")
        tunnel._apply_line = broken                                                        # type: ignore[method-assign]
    heard.clear()
    tunnel._start_supervisor()
    assert tunnel._supervisor is not None
    tunnel._supervisor.join()
    return tunnel.status(), list(heard)


def refuse_spawn():
    raise OSError(2, "No such file or directory", HOST)


for case, kwargs, reason in (("перезапуск не удался", {"spawn": refuse_spawn}, "перезапуск не удался"),
                             ("поток надзора упал", {"line": "INF вывод cloudflared"}, "поток надзора упал")):
    status, records = halted(**kwargs)
    state = status.get("supervision") or {}
    event = str(state.get("event", ""))
    check(f"{case}: состояние надзора — «остановлен» с причиной и номером события",
          state.get("state") == "остановлен" and state.get("reason") == reason and len(event) == 8, state)
    check(f"{case}: журнал сервера — запись с тем же номером события и трассировкой причины",
          any(event in r.getMessage() and r.exc_info for r in records), [r.getMessage() for r in records])
    check(f"{case}: путь хоста и текст исключения — не в состоянии туннеля, только в журнале",
          HOST not in str(status) and "No such file" not in str(status), status)


class Clock:
    """Подставные часы модуля туннеля: сон двигает время и копится — выдержанная пауза меряется без настоящего ожидания."""

    def __init__(self):
        self.now, self.slept = 1000.0, 0.0

    def time(self) -> float:
        return self.now

    monotonic = time

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.now += seconds


class Proc:
    """Подставной cloudflared: строки stdout и конец; terminate — погашен."""

    def __init__(self, lines=()):
        self.stdout, self.ended = iter(lines), False

    def poll(self):
        return 0 if self.ended else None

    def terminate(self) -> None:
        self.ended = True

    kill = terminate

    def wait(self, timeout=None) -> int:
        return 0


class Waits:
    """Подставное событие остановки: ожидание паузы двигает подставные часы и запоминается; `set` — остановка."""

    def __init__(self, clock: Clock):
        self.clock, self.calls, self.flag = clock, [], False

    def wait(self, timeout=None) -> bool:
        self.calls.append(timeout)
        if not self.flag and timeout:
            self.clock.sleep(timeout)
        return self.flag

    def set(self) -> None:
        self.flag = True

    def is_set(self) -> bool:
        return self.flag


class Half:
    """Генератор добавки: random() — ровно середина, пауза воспроизводима."""

    @staticmethod
    def random() -> float:
        return 0.5


class InPlace:
    """Поток надзора исполняется в потоке набора: его конец — событие самого сценария, ждать нечего."""

    def __init__(self, target, daemon=None):
        self.target = target

    def start(self) -> None:
        self.target()

    def join(self, timeout=None) -> None:
        return None


def lifted(outcomes: list, *, stop_before: bool = False, stop_in_spawn: bool = False) -> dict:
    """`start()` на подставных процессах: исходы готовности попыток по порядку (TunnelError или URL). Третье порождение —
    перезапуск надзора после удачного подъёма — снимает туннель: так кончается надзор."""
    tunnel = built(declarations_copy())
    assert isinstance(tunnel, CloudflaredTunnel)
    clock, procs, lines, plan = Clock(), [], [], list(outcomes)
    seen: dict = {}

    def spawn():
        if len(procs) == 1:
            seen["pause"] = clock.slept                 # выдержано до порождения повтора
        proc = Proc(["INF a", "INF b", "INF c"] if len(procs) < 2 else [])
        procs.append(proc)
        if stop_in_spawn or len(procs) > 2:
            tunnel.stop()                               # остановка пришла, пока процесс порождался
        return proc

    def ready(stream, poll_fn, deadline=None):
        outcome = plan.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    tunnel._binary_available = lambda: True             # type: ignore[method-assign]
    tunnel._spawn = spawn                               # type: ignore[method-assign]
    tunnel._await_ready = ready                         # type: ignore[method-assign]
    tunnel._apply_line = lines.append                   # type: ignore[method-assign]
    tunnel._stopped = Waits(clock)                      # type: ignore[assignment]
    tunnel.rng = Half()                                 # type: ignore[assignment]
    tunnel.clock = clock.time                           # type: ignore[misc]
    held = tunnel_module.time, tunnel_module.threading
    tunnel_module.time = clock                          # type: ignore[assignment]
    tunnel_module.threading = SimpleNamespace(Thread=InPlace, Timer=threading.Timer, Lock=threading.Lock)  # type: ignore[assignment]
    try:
        if stop_before:
            tunnel.stop()
        try:
            got: object = tunnel.start()
        except TunnelError as e:
            got = e
    finally:
        tunnel_module.time, tunnel_module.threading = held
    return {"got": got, "procs": procs, "lines": lines, "pause": seen.get("pause"), "tunnel": tunnel}


URL = "https://проба.trycloudflare.com"
base, jitter = declared["retry_base_seconds"], declared["jitter"]

print("== 5. Первый подъём с повтором (D40): пауза выдержана, после удачного повтора надзор жив ==")
run = lifted([TunnelError("край не ответил вовремя"), URL])
check("первая попытка упала, вторая подняла туннель — start вернул URL, а не отмену", run["got"] == URL, run["got"])
# Обновлено 2026-10-18 (D28): добавка — объявленная доля (надзор.jitter) от шва rng; на rng = 0.5 пауза точная.
check(f"пауза перед повтором выдержана: base·(1+jitter·0.5) = {base * (1 + jitter * 0.5)} с",
      run["pause"] is not None and abs(run["pause"] - base * (1 + jitter * 0.5)) < 1e-9, run["pause"])
check("процесс упавшей попытки погашен до повтора", len(run["procs"]) >= 2 and run["procs"][0].ended,
      [p.ended for p in run["procs"]])
check("надзор после удачного повтора жив: все строки процесса дошли до разбора",
      run["lines"] == ["INF a", "INF b", "INF c"], run["lines"])

print("== 6. Остановка во время подъёма (D41): флаг до попытки и после порождения — отмена, процесс не остаётся ==")
run = lifted([URL], stop_before=True)
check("остановка до подъёма: start — TunnelCancelled, процесс не порождён",
      isinstance(run["got"], TunnelCancelled) and run["procs"] == [], (run["got"], len(run["procs"])))
run = lifted([URL], stop_in_spawn=True)
check("остановка во время порождения: start — TunnelCancelled, а не URL", isinstance(run["got"], TunnelCancelled),
      run["got"])
check("процесс, порождённый под остановкой, погашен; повторов нет; надзор не начат",
      len(run["procs"]) == 1 and run["procs"][0].ended and run["lines"] == [] and run["tunnel"]._proc is None,
      ([p.ended for p in run["procs"]], run["lines"]))

print("== 7. Пауза перезапуска (D28): событие остановки, а не опрос; часы — шов, монотонные ==")
tunnel = built(declarations_copy())
assert isinstance(tunnel, CloudflaredTunnel)
clock = Clock()
waits = Waits(clock)
tunnel._stopped = waits                                 # type: ignore[assignment]
held = tunnel_module.time
tunnel_module.time = clock                              # type: ignore[assignment]
try:
    tunnel._sleep_interruptible(5)
finally:
    tunnel_module.time = held
check("пауза — одно ожидание события остановки на весь срок, без опроса флага по кусочкам",
      waits.calls == [5] and clock.slept == 5, (waits.calls, clock.slept))
tunnel = built(declarations_copy())
assert isinstance(tunnel, CloudflaredTunnel)
tunnel.stop()
began = time.monotonic()
tunnel._sleep_interruptible(3600)
check("остановка до паузы — пауза кончается сразу событием, а не через час", time.monotonic() - began < 1,
      time.monotonic() - began)
tunnel = built(declarations_copy())
assert isinstance(tunnel, CloudflaredTunnel)
clock = Clock()
tunnel.clock = clock.time                               # type: ignore[misc]
tunnel._proc_started = clock.time()
clock.sleep(90)
check("время работы процесса — по шву часов, а не по стенным: перевод часов его не ломает",
      tunnel.status()["uptime_sec"] == 90.0, tunnel.status()["uptime_sec"])

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
