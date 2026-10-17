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
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml

from core.contracts import ContractError
from core.transport.tunnel import CloudflaredTunnel
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

print(f"\n{'=' * 50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
