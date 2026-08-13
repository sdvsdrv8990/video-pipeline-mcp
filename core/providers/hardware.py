"""
core/providers/hardware.py — что за железо под сервером, БЕЗ прав root.

## Почему это вообще спрашивают
Локальная модель либо влезает в память этой машины, либо нет. Число параметров без объёма памяти
ничего не значит, а объём памяти без числа параметров — тем более: сопоставлять надо оба.

## Root не нужен — и это проверено, а не предположено
`/proc/meminfo` читается любым пользователем (там и общий объём, и ДОСТУПНЫЙ — это разные числа:
занятое кэшем ядро отдаёт обратно, поэтому «свободно» занижает). Число ядер даёт сам процесс,
свободное место — обычный статвызов, лимит контейнера — файл cgroup, если он есть. Root нужен для
серийников и DMI (`core/integrity` уже упирался в это), но для «влезет ли модель» они не нужны.

## Чего не знаем — говорим, что не знаем
Видеокарта видна, только если в системе есть `nvidia-smi`; его отсутствие означает «не знаю», а не
«карты нет». Так и пишем: пустой ответ здесь врал бы уверенностью, которой нет.
"""

import os
import shutil
import subprocess
from pathlib import Path

MEMINFO = Path("/proc/meminfo")
CGROUP_MAX = Path("/sys/fs/cgroup/memory.max")


def _meminfo() -> dict:
    if not MEMINFO.exists():
        return {}
    out = {}
    for line in MEMINFO.read_text(encoding="utf-8").splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            out[name.strip()] = int(parts[0]) * 1024        # килобайты → байты
    return out


def _cgroup_limit() -> int:
    """Лимит контейнера, если сервер живёт в нём: «памяти много» на хосте ещё ничего не значит."""
    try:
        raw = CGROUP_MAX.read_text(encoding="utf-8").strip()
        return int(raw) if raw.isdigit() else 0
    except OSError:
        return 0


def _gpu() -> list[dict]:
    """Видеокарты — только если есть nvidia-smi. Иначе честно ничего не утверждаем."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    cards = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3 and parts[1].isdigit():
            cards.append({"name": parts[0], "total_mb": int(parts[1]), "free_mb": int(parts[2])})
    return cards


def probe(disk_for: str | Path = ".") -> dict:
    """Снимок железа, доступный без root. Неизвестное названо неизвестным."""
    mem = _meminfo()
    limit = _cgroup_limit()
    total = mem.get("MemTotal", 0)
    # «Доступно» ≠ «свободно»: ядро отдаёт кэш под запрос, поэтому MemFree систематически занижает.
    available = mem.get("MemAvailable", mem.get("MemFree", 0))
    if limit and (not total or limit < total):
        total = limit
        available = min(available or limit, limit)
    try:
        free_disk = shutil.disk_usage(str(disk_for)).free
    except OSError:
        free_disk = 0
    cards = _gpu()
    unknown = []
    if not mem:
        unknown.append("объём памяти (нет /proc/meminfo — не Linux?)")
    if not cards:
        unknown.append("видеокарта: nvidia-smi в системе нет, поэтому наличие GPU НЕ проверено")
    return {
        "ram_total_mb": round(total / 1e6),
        "ram_available_mb": round(available / 1e6),
        "container_limit_mb": round(limit / 1e6) if limit else 0,
        "cpu_count": os.cpu_count() or 0,
        "disk_free_mb": round(free_disk / 1e6),
        "gpu": cards,
        "unknown": unknown,
        "note": ("Прочитано без прав root: /proc/meminfo, cgroup, число ядер, свободное место. "
                 "Серийники и DMI требуют root и для совместимости не нужны."),
    }


class FitEstimator:
    """Влезет ли модель: параметры → память, память → вердикт. Пороги объявлены, не зашиты."""

    def __init__(self, rules: dict | None = None):
        rules = rules or {}
        self.dtype_bytes = {str(k).upper(): float(v) for k, v in (rules.get("dtype_bytes") or {}).items()}
        self.default_bytes = float(rules.get("default_dtype_bytes", 4))
        self.overhead = float(rules.get("overhead", 1.35))
        self.tight_ratio = float(rules.get("tight_ratio", 0.8))

    def bytes_for(self, params_by_dtype: dict, params_total: int = 0) -> int:
        """Сколько памяти нужно под веса + запас на активации.

        Считаем по РАЗРЯДНОСТИ каждого куска весов: одна и та же модель в fp16 весит вдвое
        меньше, чем в fp32, и вердикт из-за этого меняется на противоположный.
        """
        if params_by_dtype:
            raw = sum(int(count) * self.dtype_bytes.get(str(dtype).upper(), self.default_bytes)
                      for dtype, count in params_by_dtype.items())
        else:
            raw = int(params_total or 0) * self.default_bytes
        return int(raw * self.overhead)

    def verdict(self, need_bytes: int, available_mb: int) -> dict:
        """Влезает / впритык / не влезает — с числами, по которым видно, почему."""
        need_mb = round(need_bytes / 1e6)
        if not need_mb:
            return {"verdict": "unknown", "need_mb": 0, "available_mb": available_mb,
                    "why": "источник не сообщает число параметров — оценить нечем"}
        if not available_mb:
            return {"verdict": "unknown", "need_mb": need_mb, "available_mb": 0,
                    "why": "объём доступной памяти неизвестен"}
        ratio = need_mb / available_mb
        if ratio > 1:
            verdict, why = "no", f"нужно ~{need_mb} МБ при доступных {available_mb} МБ"
        elif ratio > self.tight_ratio:
            verdict, why = "tight", (f"нужно ~{need_mb} МБ из {available_mb} МБ доступных — "
                                     "впритык, соседние процессы могут не оставить места")
        else:
            verdict, why = "fits", f"нужно ~{need_mb} МБ из {available_mb} МБ доступных"
        return {"verdict": verdict, "need_mb": need_mb, "available_mb": available_mb,
                "why": why + ". Это ОЦЕНКА по весам и объявленному запасу, а не замер."}
