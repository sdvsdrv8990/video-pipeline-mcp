"""tests/scenarios/steps.py — шаги-помощники: то, чего вызовом инструмента не выразить."""

import json
import threading
import time
from pathlib import Path


def remove_path(srv, path: str) -> dict:
    """Файл исчез МИМО инструментов — так его сносит человек в проводнике, а не сервер."""
    target = Path(srv.workspace) / path
    if not target.exists():
        return {"ok": False, "code": "MISSING_TARGET_FILE", "message": f"нечего убирать: {path}"}
    target.unlink()
    return {"ok": True, "data": {"removed": path}}


def branch_state(srv, niche: str) -> dict:
    """Снимок ВЕТКИ ниши: что в ней заведено и что сервер о ней говорит.

    Проекция, а не суждение: обход и фильтрация по префиксу — ветвление, которому в объявлении не
    место; судит уже сценарий своим `expect`. Книги считаются отдельно от структурных типов —
    материализация книг не является состоянием структуры.
    """
    root = f"niches/{niche}"
    mine = [e for e in (srv.rpc.call_tool("structure_find", {"limit": 500})["data"] or {}).get("found", [])
            if e["path"] == root or e["path"].startswith(root + "/")]
    status = srv.rpc.call_tool("structure_status", {})["data"] or {}

    def names(key):
        return sorted(e["name"] for e in status.get(key, []) if e.get("path", "").startswith(root + "/"))

    return {"ok": True, "data": {
        "types": sorted({e["type"] for e in mine if e["type"] != "table_file"}),
        "books": len([e for e in mine if e["type"] == "table_file"]),
        "orphans": names("orphans"),
        "childless": names("our_channels_without_competitor")}}


def busy_server_still_answers(srv, heavy: str, heavy_args: dict,
                              budget_sec: float = 1.0) -> dict:
    """Пока идёт тяжёлый вызов, лёгкий обязан получить ответ.

    Двумя потоками, иначе эффект не наблюдаем вовсе. `overlapped` защищает от пустого прохода:
    если тяжёлый успел закончиться раньше лёгкого, быстрый ответ ничего не доказывает.
    """
    marks: dict = {}

    def heavy_call():
        started = time.time()
        srv.rpc.call_tool(heavy, heavy_args)
        marks["heavy_sec"] = round(time.time() - started, 2)
        marks["heavy_end"] = time.time()

    worker = threading.Thread(target=heavy_call)
    worker.start()
    time.sleep(0.4)                                  # даём тяжёлому реально начаться
    started = time.time()
    tools = srv.rpc.tools_list()
    light_end = time.time()
    worker.join()

    return {"ok": True, "data": {
        "light_sec": round(light_end - started, 2),
        "heavy_sec": marks.get("heavy_sec", 0.0),
        "tools_seen": len(tools) > 0,
        "overlapped": light_end < marks.get("heavy_end", 0.0),
        "within_budget": (light_end - started) < budget_sec,
    }}


def trail_says(srv, tool: str) -> dict:
    """Что сервер записал о вызове `tool` в свой след.

    Наблюдать след через инструмент нельзя — ни один его не читает, а сценарий обязан судить по
    тому, что видно снаружи. Поэтому шаг читает файл сервера напрямую.

    Принимается только запись СВОЕГО сервера — с момента его старта. Окно по часам не годится:
    файлы следа переживают прогон, и на замере выключенный след зеленел на записи прошлого прогона.
    """
    directory = Path(__file__).resolve().parents[2] / "logs" / "trail"
    fresh = float(getattr(srv, "started", 0.0)) or time.time()
    files = sorted(directory.glob("trail-*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    for path in files[:3]:
        rows = [r for r in path.read_text(encoding="utf-8", errors="replace").splitlines() if r.strip()]
        for row in reversed(rows):
            try:
                entry = json.loads(row)
            except ValueError:
                continue
            if entry.get("tool") == tool and float(entry.get("ts") or 0) >= fresh:
                return {"ok": True, "data": {"tool": entry.get("tool"), "code": entry.get("code"),
                                             "ok_flag": entry.get("ok"), "args": entry.get("args") or {},
                                             "step": entry.get("step")}}
    return {"ok": False, "code": "MISSING_TARGET_FILE",
            "message": f"в следе сервера нет СВЕЖЕЙ записи о вызове {tool}: "
                       f"сервер не пишет то, чем воспроизводят"}


STEPS = {"remove_path": remove_path, "branch_state": branch_state,
         "busy_server_still_answers": busy_server_still_answers,
         "trail_says": trail_says}
