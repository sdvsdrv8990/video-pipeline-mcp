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

## Видеокарта — тоже без root, и не только nvidia
Локальные модели крутят на карте, а не на процессоре, поэтому её память — главное число здесь.
Ядро выкладывает карту в `/sys/class/drm/card*/device` любому пользователю: драйвер и PCI-номер
из `uevent`, а `amdgpu` отдаёт там же `mem_info_vram_*` — сколько памяти всего и сколько занято.
Имя берётся из системной базы `pci.ids`. Для nvidia в sysfs объёма нет — там остаётся `nvidia-smi`.

## Память карты ≠ она посчитает
Видеть 16 ГБ на карте и уметь ими воспользоваться — разные вещи: сборка `torch+cu…` не увидит
AMD, для неё нужна `+rocm`. Поэтому у каждой карты стоит `usable` с причиной, а вердикт считается
по той памяти, до которой расчёт реально дотягивается. Иначе сервер обещал бы чужую память.

## Чего не знаем — говорим, что не знаем
Карты не нашли или драйвер молчит про объём — так и пишем: «не проверено», а не «её нет». Пустой
ответ здесь врал бы уверенностью, которой нет.
"""

import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

MEMINFO = Path("/proc/meminfo")
CGROUP_MAX = Path("/sys/fs/cgroup/memory.max")
SYSFS_DRM = Path("/sys/class/drm")
PCI_IDS = Path("/usr/share/hwdata/pci.ids")     # системная база имён устройств, читается всеми


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


def _int_file(path: Path) -> int:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return 0
    return int(raw) if raw.isdigit() else 0


def _uevent(dev: Path) -> dict:
    """`uevent` карты: драйвер и PCI-номер. Разъёмы монитора драйвера не имеют — так и отсеются."""
    out = {}
    try:
        for line in (dev / "uevent").read_text(encoding="utf-8").splitlines():
            key, _, val = line.partition("=")
            out[key.strip()] = val.strip()
    except OSError:
        return {}
    return out


def _pci_names(wanted: set[str]) -> dict:
    """Имена карт из `pci.ids`: строка вендора без отступа, устройства под ней с одним табом."""
    if not wanted or not PCI_IDS.exists():
        return {}
    names, vendor = {}, ""
    try:
        with PCI_IDS.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                if not line.startswith("\t"):
                    vendor = line.split()[0].lower()
                elif vendor and not line.startswith("\t\t"):
                    dev, _, name = line.strip().partition(" ")
                    if f"{vendor}:{dev.lower()}" in wanted and name.strip():
                        names[f"{vendor}:{dev.lower()}"] = name.strip()
    except OSError:
        return {}
    return names


def _nvidia_smi() -> list[dict]:
    """Для nvidia объём памяти в sysfs не выложен — остаётся её собственная утилита."""
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
            cards.append({"name": parts[0], "vram_total_mb": int(parts[1]),
                          "vram_free_mb": int(parts[2])})
    return cards


def _accel_tag(package: str, build_vars: dict) -> str:
    """Под какой ускоритель собран пакет расчёта: `2.13.0+cu130` → `cu`, `+rocm6.2` → `rocm`.

    `version.py` пакета читается ТЕКСТОМ, а не импортом: импорт torch стоит секунды, а ответ
    нужен на каждый список моделей. В метаданных дистрибутива локального тега нет (там голое
    `2.13.0`), поэтому смотрим в сам пакет, а при пустом теге — в объявленную переменную сборки.
    """
    if not package:
        return ""
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError):
        return ""
    origin = getattr(spec, "origin", "") if spec else ""
    if not origin:
        return ""
    try:
        text = (Path(origin).parent / "version.py").read_text(encoding="utf-8")
    except OSError:
        return ""
    tagged = re.search(r"__version__\s*=\s*['\"][^'\"+]+\+([A-Za-z]+)", text)
    if tagged:
        return tagged.group(1).lower()
    for tag, var in (build_vars or {}).items():
        if re.search(rf"^{re.escape(str(var))}\s*[:=].*['\"]", text, re.MULTILINE):
            return str(tag).lower()
    return ""


def _usability(driver: str, rules: dict, tag: str, package: str) -> tuple[bool, str]:
    """Дотянется ли расчёт до этой карты. Сборка решает: `+cu…` не увидит AMD, `+rocm` — nvidia."""
    if not rules:
        return False, (f"драйвер '{driver}' не объявлен в config/providers.yaml → local.gpu.drivers, "
                       "поэтому чем считать на этой карте — неизвестно")
    need = str(rules.get("torch_tag") or "")
    runtime = str(rules.get("runtime") or "")
    if not need:
        return False, ("встроенная графика: своей памяти нет, она делит ОЗУ — считать по ней "
                       "отдельно нечего")
    if not tag:
        return False, f"пакет расчёта '{package}' не установлен или не сообщает, под что собран"
    if tag != need:
        return False, (f"{package} собран под '{tag}', а этой карте нужен '{need}' ({runtime}) — "
                       f"её память расчёту недоступна, пока не поставлена сборка {package} "
                       f"с тегом '{need}' (индекс {runtime})")
    return True, f"{package}+{tag} ({runtime}) — карта доступна расчёту"


def _gpus(rules: dict) -> tuple[list[dict], list[str]]:
    """Видеокарты из sysfs (без root), объём памяти — где драйвер его отдаёт."""
    drivers = {str(k): (v or {}) for k, v in ((rules or {}).get("drivers") or {}).items()}
    package = str((rules or {}).get("package") or "")
    tag = _accel_tag(package, (rules or {}).get("build_vars") or {})
    found, seen = [], set()
    for dev in sorted(SYSFS_DRM.glob("card*/device")) if SYSFS_DRM.is_dir() else []:
        ue = _uevent(dev)
        driver, slot = ue.get("DRIVER", ""), ue.get("PCI_SLOT_NAME", "")
        if not driver or slot in seen:
            continue
        seen.add(slot)
        pci = str(ue.get("PCI_ID", "")).lower().replace("0x", "")
        usable, why = _usability(driver, drivers.get(driver, {}), tag, package)
        total, used = _int_file(dev / "mem_info_vram_total"), _int_file(dev / "mem_info_vram_used")
        found.append({"pci": pci, "driver": driver, "name": "", "slot": slot,
                      "vram_total_mb": round(total / 1e6), "vram_free_mb": round((total - used) / 1e6),
                      "usable": usable, "why": why})
    names = _pci_names({c["pci"] for c in found if c["pci"]})
    smi = _nvidia_smi()
    for card in found:
        card["name"] = names.get(card["pci"]) or card["pci"] or card["driver"]
        if not card["vram_total_mb"] and smi:
            # sysfs объём не отдал (так у nvidia) — берём у её утилиты, по порядку карт.
            card.update({k: v for k, v in smi.pop(0).items() if k != "name"})
    found.extend({**c, "pci": "", "driver": "nvidia", "slot": "", "usable": bool(tag == "cu"),
                  "why": "карта видна только через nvidia-smi: sysfs в этой системе недоступен"}
                 for c in smi if not found)
    gaps = []
    if not found:
        gaps.append("видеокарта: в /sys/class/drm карт не найдено и nvidia-smi в системе нет — "
                    "наличие GPU НЕ проверено (а не «его нет»)")
    for card in found:
        if not card["vram_total_mb"]:
            gaps.append(f"видеокарта {card['name']}: драйвер '{card['driver']}' не сообщает объём "
                        "своей памяти — влезет ли в неё модель, не оценить")
    return found, gaps


def probe(disk_for: str | Path = ".", gpu_rules: dict | None = None) -> dict:
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
    cards, unknown = _gpus(gpu_rules or {})
    if not mem:
        unknown.append("объём памяти (нет /proc/meminfo — не Linux?)")
    return {
        "ram_total_mb": round(total / 1e6),
        "ram_available_mb": round(available / 1e6),
        "container_limit_mb": round(limit / 1e6) if limit else 0,
        "cpu_count": os.cpu_count() or 0,
        "disk_free_mb": round(free_disk / 1e6),
        "gpu": cards,
        "unknown": unknown,
        "note": ("Прочитано без прав root: /proc/meminfo, cgroup, число ядер, свободное место и "
                 "видеокарта из /sys/class/drm. Серийники и DMI требуют root и для совместимости "
                 "не нужны. У карты смотри `usable`: видеть её память и уметь ею считать — разное."),
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

    def pool(self, hw: dict) -> dict:
        """Чем считать: памятью карты, если расчёт до неё дотягивается, иначе ОЗУ.

        Карта, объём которой виден, но `usable=False`, сюда не попадает: обещать её память —
        значит обещать чужое. Она возвращается отдельно, вторым числом.
        """
        live = [c for c in (hw.get("gpu") or []) if c.get("usable") and c.get("vram_free_mb")]
        if live:
            best = max(live, key=lambda c: c["vram_free_mb"])
            return {"target": "gpu", "device": best["name"], "available_mb": best["vram_free_mb"]}
        return {"target": "cpu", "device": "ОЗУ", "available_mb": hw.get("ram_available_mb", 0)}

    def idle_gpu(self, hw: dict) -> dict:
        """Карта, память которой известна, но расчёту сейчас недоступна, — знать про неё полезно."""
        idle = [c for c in (hw.get("gpu") or []) if not c.get("usable") and c.get("vram_free_mb")]
        return max(idle, key=lambda c: c["vram_free_mb"]) if idle else {}

    def verdict(self, need_bytes: int, available_mb: int, device: str = "ОЗУ") -> dict:
        """Влезает / впритык / не влезает — с числами, по которым видно, почему."""
        need_mb = round(need_bytes / 1e6)
        base = {"need_mb": need_mb, "available_mb": available_mb, "device": device}
        if not need_mb:
            return {**base, "verdict": "unknown",
                    "why": "источник не сообщает число параметров — оценить нечем"}
        if not available_mb:
            return {**base, "verdict": "unknown", "why": f"объём памяти ({device}) неизвестен"}
        ratio = need_mb / available_mb
        if ratio > 1:
            verdict, why = "no", f"нужно ~{need_mb} МБ при доступных {available_mb} МБ ({device})"
        elif ratio > self.tight_ratio:
            verdict, why = "tight", (f"нужно ~{need_mb} МБ из {available_mb} МБ ({device}) — "
                                     "впритык, соседние процессы могут не оставить места")
        else:
            verdict, why = "fits", f"нужно ~{need_mb} МБ из {available_mb} МБ ({device})"
        return {**base, "verdict": verdict,
                "why": why + ". Это ОЦЕНКА по весам и объявленному запасу, а не замер."}
