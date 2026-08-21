"""
tests/conformance/test_conformance.py — сверка с эталонным набором спеки MCP.

Standalone-прогон:  python tests/conformance/test_conformance.py
Нужен `npx` (пакет @modelcontextprotocol/conformance). Нет node — набор ЧЕСТНО пропускается
с причиной: сторонний инструмент недоступен ≠ сервер не соответствует спеке.

Смысл не в «зелёном прогоне»: 24 из 30 сценариев проверяют возможности, которых у нас нет
(resources, prompts, sampling, elicitation, logging, completion). Они перечислены ниже поимённо
с причиной, и набор краснеет в ОБЕ стороны: неожиданно упавший сценарий — регрессия, неожиданно
ПРОШЕДШИЙ — устаревшая запись, которую пора снять.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.harness import AuthProxy, live_server

PACKAGE = "@modelcontextprotocol/conformance@latest"

# Сценарии, которые обязаны падать: возможности спеки, которых сервер не заявляет.
# Снимать запись отсюда — осознанное действие: значит возможность появилась.
UNSUPPORTED = {
    "logging-set-level": "logging/setLevel не реализован",
    "completion-complete": "completion/complete не реализован",
    "resources-list": "ресурсов нет — сервер отдаёт данные инструментами",
    "resources-read-text": "ресурсов нет",
    "resources-read-binary": "ресурсов нет",
    "resources-templates-read": "ресурсов нет",
    "resources-subscribe": "подписок нет",
    "resources-unsubscribe": "подписок нет",
    "prompts-list": "промптов сервер не публикует",
    "prompts-get-simple": "промптов нет",
    "prompts-get-with-args": "промптов нет",
    "prompts-get-embedded-resource": "промптов нет",
    "prompts-get-with-image": "промптов нет",
    "tools-call-image": "инструменты возвращают текст и structuredContent, не картинки",
    "tools-call-audio": "аудио-контента в ответах нет",
    "tools-call-embedded-resource": "встроенных ресурсов в ответах нет",
    "tools-call-mixed-content": "смешанного контента в ответах нет",
    "tools-call-with-logging": "logging/setLevel не реализован",
    "tools-call-with-progress": "уведомлений о прогрессе нет",
    "tools-call-sampling": "сервер не запрашивает генерацию у клиента",
    "tools-call-elicitation": "сервер не запрашивает уточнений у клиента",
    "elicitation-sep1034-defaults": "elicitation не реализован",
    "elicitation-sep1330-enums": "elicitation не реализован",
}

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


def skip(reason: str) -> None:
    """Пропуск с причиной. В CI-джобе пропуск ЗАПРЕЩЁН: там он означал бы ложный зелёный."""
    if os.environ.get("VPM_CONFORMANCE_REQUIRED") == "1":
        print(f"ПРОВАЛ: {reason} — но джоба conformance обязана его прогонять")
        sys.exit(1)
    print(f"ПРОПУЩЕН: {reason}")
    print("Это не зелёный результат — это отсутствие инструмента. Гейт conformance живёт в CI-джобе.")
    sys.exit(0)


if not shutil.which("npx"):
    skip("нет `npx` (node) — сторонний conformance-клиент запустить нечем")

# Конфиг прогона: копия БОЕВОГО, в которой поднят единственный порог — частота. Сторонний клиент
# честно бьёт 30 сценариев подряд с одного адреса и на боевых 60 запросах в минуту банит сам себя
# (`Blocked: IP заблокирован`), после чего «несоответствие спеке» — это на самом деле блокировка.
cfg_dir = Path(tempfile.mkdtemp(prefix="conf_cfg_")) / "config"
shutil.copytree(ROOT / "config", cfg_dir)
fw = cfg_dir / "firewall.yaml"
src_fw = (ROOT / "config" / "firewall.yaml").read_text(encoding="utf-8")
fw.write_text(re.sub(r"max_requests_per_minute:\s*\d+",
                     "max_requests_per_minute: 100000", src_fw, count=1), encoding="utf-8")

print("== 0. Прогон идёт по боевым декларациям, кроме ОДНОГО порога ==")
diff_keys = [p.name for p in sorted((ROOT / "config").glob("*.yaml"))
             if (cfg_dir / p.name).read_text(encoding="utf-8") != p.read_text(encoding="utf-8")]
ok(diff_keys == ["firewall.yaml"],
   f"от боевого конфига отличается ровно firewall.yaml ({diff_keys})")
ok(fw.read_text(encoding="utf-8").replace("100000", "60") == src_fw,
   "и в нём — ровно порог частоты, остальные правила файрвола боевые")

with live_server(env={"MCP_CONFIG": str(cfg_dir)}) as srv, AuthProxy(srv.url, srv.token) as proxy:
    # Прокси добавляет ключ за клиента, который его не умеет: калитка MCP_ALLOW_NO_AUTH сняла бы
    # проверяемый слой целиком. Host прокси передаёт как есть — на нём стоит проверка Host.
    out_dir = Path(tempfile.mkdtemp(prefix="conf_out_"))
    run = subprocess.run(["npx", "-y", PACKAGE, "server", "--url", proxy.url, "-o", str(out_dir)],
                         capture_output=True, text=True, timeout=1200,
                         env={**os.environ, "npm_config_yes": "true"})

    results = {}
    for d in sorted(out_dir.iterdir()):
        checks_file = d / "checks.json"
        if not checks_file.exists():
            continue
        scenario = re.sub(r"^server-|-\d{4}-\d\d-\d\dT.*$", "", d.name)
        checks = json.loads(checks_file.read_text(encoding="utf-8"))
        results[scenario] = {c["status"] for c in checks}

    print("== 1. Прогон состоялся ==")
    ok(len(results) >= 25, f"инструмент отработал по сценариям ({len(results)}); stderr: {run.stderr[:200]}")

    passed = {s for s, st in results.items() if "FAILURE" not in st}
    failed = {s for s, st in results.items() if "FAILURE" in st}

    print("== 2. Заявленное спекой и реализованное у нас — проходит ==")
    supported = sorted(set(results) - set(UNSUPPORTED))
    for scenario in supported:
        ok(scenario in passed,
           f"{scenario}: соответствует спеке ({sorted(results[scenario])})")

    print("== 3. Нереализованное падает ИМЕННО ТАМ, где заявлено (иначе запись устарела) ==")
    unexpected_pass = sorted(s for s in UNSUPPORTED if s in passed)
    ok(not unexpected_pass,
       f"ни один «нереализованный» сценарий не прошёл втихую — иначе снимай его из списка ({unexpected_pass})")
    stale = sorted(s for s in UNSUPPORTED if s not in results)
    ok(not stale, f"каждая запись списка соответствует существующему сценарию ({stale})")
    ok(failed == set(UNSUPPORTED) & set(results),
       f"падают ровно объявленные ({sorted(failed - set(UNSUPPORTED))} сверх списка)")

    print("== 4. Ключевое для нас: инициализация, инвентарь, вызов, ошибка, анти-rebinding ==")
    for must in ("server-initialize", "ping", "tools-list", "tools-call-simple-text",
                 "tools-call-error", "dns-rebinding-protection"):
        ok(must in passed, f"{must} зелёный ({sorted(results.get(must, ['НЕ ЗАПУСКАЛСЯ']))})")

print(f"\n{'='*50}")
print(f"РЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    print("ПРОВАЛЫ:")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
