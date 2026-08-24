"""tests/scenarios/steps.py — шаги-помощники: то, чего вызовом инструмента не выразить."""

from pathlib import Path


def remove_path(srv, path: str) -> dict:
    """Файл исчез МИМО инструментов — так его сносит человек в проводнике, а не сервер."""
    target = Path(srv.workspace) / path
    if not target.exists():
        return {"ok": False, "code": "MISSING_TARGET_FILE", "message": f"нечего убирать: {path}"}
    target.unlink()
    return {"ok": True, "data": {"removed": path}}


STEPS = {"remove_path": remove_path}
