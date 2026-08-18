"""
core/providers/hardware.py — что за железо под сервером, БЕЗ прав root.

## Назначение
Ответ на «влезет ли локальная модель на эту машину»: память, ядра, диск, видеокарты.

## Границы
- всё берётся из читаемых любым пользователем источников (`/proc/meminfo`, cgroup,
  `/sys/class/drm/card*/device`, `pci.ids`, `nvidia-smi`) — root нужен DMI, не расчёту;
- у памяти два разных числа: ДОСТУПНАЯ выше свободной, потому что занятое кэшем ядро отдаёт;
- `usable` с причиной у каждой карты: видеть память карты и уметь ею считать — разные вещи
  (сборка `torch+cu…` не видит AMD), вердикт считается по достижимой памяти, а не по всей;
- карт не нашли или драйвер молчит про объём — ответ «не проверено», а не «её нет».
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
# Что делать с моделью, которая больше устройства. Механизмы — библиотечные (diffusers/accelerate),
# здесь только их человеческая цена: она нужна и вердикту в каталоге, и объяснению при подъёме.
OVERSIZE_MODES = {
    "model_offload": "держим на устройстве по одной части, остальное в ОЗУ — медленнее в разы",
    "sequential_offload": "держим по одному слою — медленнее в десятки раз, но влезает почти всё",
    "error": "объявлен отказ вместо выгрузки",
}
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


def compute_device(gpu_rules: dict, want: str = "", hw: dict | None = None) -> dict:
    """Где считать эту модель: устройство и разрядность. Решает железо, а не адаптер.

    Автовыбор — карта, если она есть, её память известна и сборка расчёта до неё дотягивается;
    иначе процессор. `want` (столбец строки канала) перекрывает автовыбор: оператор может знать
    про машину то, чего проба не видит — например, что карту уже занял соседний процесс.
    Имя устройства объявлено у драйвера: у ROCm-сборки оно то же `cuda`, отдельного нет.
    """
    rules = gpu_rules or {}
    dtypes = rules.get("dtype") or {}
    drivers = rules.get("drivers") or {}
    hw = probe(".", rules) if hw is None else hw
    ram = int(hw.get("ram_available_mb") or 0)
    asked = str(want or "").strip().lower()
    if asked and asked != "auto":
        on_cpu = asked == "cpu"
        card: dict = next((c for c in (hw.get("gpu") or []) if c.get("vram_free_mb")), {})
        return {"device": asked, "dtype": str(dtypes.get("cpu" if on_cpu else "gpu", "")),
                "available_mb": ram if on_cpu else int(card.get("vram_free_mb") or 0),
                "source": "строка канала", "why": f"устройство '{asked}' задано столбцом строки"}
    for card in hw.get("gpu") or []:
        name = str((drivers.get(card.get("driver")) or {}).get("torch_device") or "")
        if card.get("usable") and card.get("vram_free_mb") and name:
            return {"device": name, "dtype": str(dtypes.get("gpu", "")),
                    "available_mb": int(card["vram_free_mb"]), "source": "проба железа",
                    "why": f"{card['name']}: {card['vram_free_mb']} МБ свободно, {card['why']}"}
    idle = [c for c in (hw.get("gpu") or []) if not c.get("usable")]
    return {"device": "cpu", "dtype": str(dtypes.get("cpu", "")), "available_mb": ram,
            "source": "проба железа",
            "why": ("считаем на процессоре: " + (idle[0]["why"] if idle else "карты не нашлось"))}


def placement(gpu_rules: dict, fit_rules: dict, entry: dict, where: dict) -> dict:
    """Класть модель целиком или с выгрузкой: она может быть больше выбранного устройства.

    Ответ на «не влезает» — не другой набор весов, а механизм самой библиотеки: держать на карте
    по одному блоку, остальное в ОЗУ. Медленнее, но работает; какой режим брать — объявлено, а не
    решено здесь. `error` означает отказ вслух: молча посчитать вдесятеро дольше — хуже отказа.
    """
    mode = str((gpu_rules or {}).get("oversize") or "error").lower()
    need_mb = round(FitEstimator(fit_rules).bytes_for(
        entry.get("params_by_dtype") or {}, int(entry.get("params_total") or 0),
        as_dtype=where.get("dtype", "")) / 1e6)
    free = int(where.get("available_mb") or 0)
    base = {"need_mb": need_mb, "available_mb": free, "device": where.get("device", "")}
    if where.get("device") == "cpu" or not need_mb or not free or need_mb <= free:
        # Неизвестный размер тоже кладём целиком: гадать «наверное, не влезет» — не лучше, а
        # отказ библиотеки по памяти будет громким и с настоящей причиной.
        return {**base, "mode": "device",
                "why": (f"нужно ~{need_mb} МБ из {free} МБ" if need_mb and free
                        else "размер модели неизвестен — кладём целиком")}
    return {**base, "mode": mode,
            "why": (f"нужно ~{need_mb} МБ, а на устройстве {free} МБ — "
                    + OVERSIZE_MODES.get(mode, f"режим '{mode}' не объявлен, размещать нечем"))}


class FitEstimator:
    """Влезет ли модель: параметры → память, память → вердикт. Пороги объявлены, не зашиты."""

    def __init__(self, rules: dict | None = None):
        rules = rules or {}
        self.dtype_bytes = {str(k).upper(): float(v) for k, v in (rules.get("dtype_bytes") or {}).items()}
        self.default_bytes = float(rules.get("default_dtype_bytes", 4))
        self.overhead = float(rules.get("overhead", 1.35))
        self.tight_ratio = float(rules.get("tight_ratio", 0.8))
        self.aliases = {str(k).lower(): str(v).upper() for k, v in (rules.get("dtype_alias") or {}).items()}

    def bytes_for(self, params_by_dtype: dict, params_total: int = 0, as_dtype: str = "") -> int:
        """Сколько памяти нужно под веса + запас на активации.

        Считаем по РАЗРЯДНОСТИ каждого куска весов: одна и та же модель в fp16 весит вдвое
        меньше, чем в fp32, и вердикт из-за этого меняется на противоположный. `as_dtype` —
        разрядность, в которой модель РЕАЛЬНО поднимут (её задаёт устройство): веса лежат в
        fp32, а грузятся в fp16 — считать по диску значило бы завысить вдвое.
        """
        key = self.aliases.get(str(as_dtype).lower(), "")
        total = int(params_total or 0) or sum(int(c) for c in (params_by_dtype or {}).values())
        if key and total:
            raw = total * self.dtype_bytes.get(key, self.default_bytes)
        elif params_by_dtype:
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
