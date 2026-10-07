"""core/providers/hardware.py — влезет ли локальная модель на эту машину, БЕЗ прав root.

Всё берётся из источников, читаемых любым пользователем: root нужен разве что описи DMI, а не
расчёту. У памяти два разных числа — ДОСТУПНАЯ выше свободной, потому что занятое кэшем ядро
отдаёт. У каждой карты называется пригодность с причиной: видеть память карты и уметь ею считать —
разные вещи (сборка под чужого вендора не видит нашу), и вердикт считается по достижимой памяти, а
не по всей. Карт не нашли или драйвер молчит про объём — ответ «не проверено», а не «её нет».
"""

import importlib.util
import math
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

PROC = Path("/proc")             # корни чтения ядер и памяти; проверки подают временные каталоги
FS_ROOT = Path("/")
SYSFS_DRM = Path("/sys/class/drm")
# Что делать с моделью, которая больше устройства. Механизмы — библиотечные (diffusers/accelerate),
# здесь только их человеческая цена: она нужна и вердикту в каталоге, и объяснению при подъёме.
OVERSIZE_MODES = {
    "model_offload": "держим на устройстве по одной части, остальное в ОЗУ — медленнее в разы",
    "sequential_offload": "держим по одному слою — медленнее в десятки раз, но влезает почти всё",
    "error": "объявлен отказ вместо выгрузки",
}
PCI_IDS = Path("/usr/share/hwdata/pci.ids")     # системная база имён устройств, читается всеми


def _meminfo(path: Path) -> dict:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            out[name.strip()] = int(parts[0]) * 1024        # килобайты → байты
    return out


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


def probe(disk_for: str | Path = ".", gpu_rules: dict | None = None, *,
          proc: Path = PROC, fs_root: Path = FS_ROOT) -> dict:
    """Снимок железа, доступный без root. Ядра и память — ЭТОГО процесса (привязка и пределы cgroup по цепочке),
    всегда числом: не ответила ни одна ступень — консервативный минимум (ядер 1, памяти 0) с причиной в `unknown`
    (HW-05). `proc` и `fs_root` — корни, откуда читаются ядра и память."""
    memory = _memory(proc, fs_root)
    cards, unknown = _gpus(gpu_rules or {})
    # Каталога весов может ещё не быть (первая установка): меряется диск ближайшего существующего предка — туда он ляжет.
    disk_at = next((p for p in (Path(disk_for).absolute(), *Path(disk_for).absolute().parents) if p.exists()),
                   Path(disk_for))
    try:
        free_disk = shutil.disk_usage(str(disk_at)).free
    except OSError as exc:
        # Не прочитано — не ноль: «0 свободно» назвало бы неверную причину (нет места вместо нет данных).
        free_disk = None
        unknown.append(f"свободное место под веса ({disk_for}: {exc.strerror or exc})")
    if memory["available"] is None:
        unknown.append(f"объём памяти — консервативный 0: {memory['source']}")
    cpus, cpu_source = _cpus(proc, fs_root)
    if cpus is None:
        cpus, cpu_source = 1, f"минимум 1: {cpu_source}"
        unknown.append(f"число ядер — {cpu_source}")
    return {
        "ram_total_mb": round((memory["total"] or 0) / 1e6),
        "ram_available_mb": round((memory["available"] or 0) / 1e6),
        "container_limit_mb": round(memory["limit"] / 1e6) if memory["limit"] else 0,
        "ram_source": memory["source"],
        "cpu_count": cpus,
        "cpu_source": cpu_source,
        "disk_free_mb": None if free_disk is None else round(free_disk / 1e6),
        "gpu": cards,
        "unknown": unknown,
        "note": ("Прочитано без прав root: ядра и память процесса (привязка, /proc/meminfo, пределы cgroup по цепочке; "
                 "на прочих ОС — psutil), свободное место и видеокарта из /sys/class/drm. Серийники и DMI требуют "
                 "root и для совместимости не нужны. У карты смотри `usable`: видеть её память и уметь ею считать — "
                 "разное."),
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
    ram = hw["ram_available_mb"]
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


def _cpus(proc: Path = PROC, fs_root: Path = FS_ROOT) -> tuple[int | None, str]:
    """Ядра, которые может занять ЭТОТ процесс (HW-01): первая ответившая ступень привязки или ядер системы, затем квота
    cgroup по всей цепочке предков — берётся меньшее. Не ответили ни ступени, ни квота — (None, кого спросили): минимум
    ставит снимок (`probe`, HW-05)."""
    asked: list[str] = []
    count: int | None = None
    for name, step in _CPU_STEPS:
        asked.append(name)
        count = step(proc, fs_root)
        if count:
            break
    quota, where = _cpu_quota(proc, fs_root)
    if count is None:
        silent = "не ответила ни одна ступень — " + ", ".join(asked)
        return (quota, f"квота {where}; {silent}") if quota is not None else (None, silent)
    if quota is not None and quota < count:
        return quota, f"квота {where} (ступень {asked[-1]}: {count})"
    return count, asked[-1]


def _cpu_affinity(_proc: Path, _fs_root: Path) -> int | None:
    try:
        import psutil
    except ImportError:
        return None
    try:
        return len(psutil.Process().cpu_affinity())
    except (AttributeError, psutil.Error, OSError):  # на macOS привязки в API нет; песочница запрещает вызов
        return None


def _cpu_list(path: Path) -> int | None:
    """Число ядер в списке ядра вида `0-3,8,10-11` (`/sys/devices/system/cpu/online`)."""
    text = _cgroup_text(path)
    if not text:
        return None
    total = 0
    for part in text.split(","):
        low, _, high = part.partition("-")
        if not low.isdigit() or (high and not high.isdigit()):
            return None
        total += int(high or low) - int(low) + 1
    return total


def _count_lines(path: Path, pattern: str) -> int | None:
    found = len(re.findall(pattern, _cgroup_text(path) or "", flags=re.MULTILINE))
    return found or None


# Ступени ядер по цене (HW-11): системный вызов, psutil, sysconf, затем файлы ядра Linux.
_CPU_STEPS = (
    ("os.process_cpu_count", lambda proc, fs: (getattr(os, "process_cpu_count", None) or (lambda: None))()),
    ("psutil.Process().cpu_affinity", _cpu_affinity),
    ("os.cpu_count", lambda proc, fs: os.cpu_count()),
    ("/sys/devices/system/cpu/online", lambda proc, fs: _cpu_list(fs / "sys/devices/system/cpu/online")),
    ("/proc/cpuinfo processor", lambda proc, fs: _count_lines(proc / "cpuinfo", r"^processor\s*:")),
    ("/proc/stat cpuN", lambda proc, fs: _count_lines(proc / "stat", r"^cpu\d+\s")),
)


def _cpu_quota(proc: Path, fs_root: Path) -> tuple[int | None, str]:
    """Квота CPU — самая тесная по цепочке групп (`ceil(MAX / PERIOD)`); у v1 — `cfs_quota_us / cfs_period_us`."""
    kind, levels = _cgroup_levels(proc, fs_root, "cpu")
    best: int | None = None
    where = ""
    for name, level in levels:
        if kind == "v2":
            raw = (_cgroup_text(level / "cpu.max") or "").split()
            quota, period = (raw + ["", ""])[:2]
        else:
            quota = _cgroup_text(level / "cpu.cfs_quota_us") or ""
            period = _cgroup_text(level / "cpu.cfs_period_us") or ""
        if quota.isdigit() and period.isdigit() and int(period):
            cores = max(1, math.ceil(int(quota) / int(period)))
            if best is None or cores < best:
                best, where = cores, f"cgroup {kind} {name}: {quota}/{period}"
    return best, where


def _memory(proc: Path = PROC, fs_root: Path = FS_ROOT) -> dict:
    """ОЗУ, которое может взять ЭТОТ процесс (HW-02), в байтах — {total, available, limit, source}.

    Linux: /proc/meminfo и пределы cgroup по всей цепочке предков — действует самый тесный; занятое группой — рабочий
    набор `memory.current − inactive_file` (кэш страниц ядро вытеснит). Прочие ОС — psutil. Не ответило ничего —
    None с причиной: консервативный 0 ставит снимок (`probe`, HW-05) — «не влезет», а не «влезет»."""
    mem = _meminfo(proc / "meminfo")
    total, available, how = mem.get("MemTotal"), mem.get("MemAvailable"), "/proc/meminfo"
    fallback = ("MemFree", "Buffers", "Cached")    # доступное ядер до 3.14 без MemAvailable (формула Ollama, HW-02)
    if total and available is None and all(k in mem for k in fallback):
        available, how = sum(mem[k] for k in fallback), "/proc/meminfo: MemFree + Buffers + Cached (нет MemAvailable)"
    if not total:
        total, available, how = _memory_portable()
    limit: int | None = None
    kind, levels = _cgroup_levels(proc, fs_root, "memory")
    names = ("memory.max", "memory.current", "inactive_file") if kind == "v2" else \
        ("memory.limit_in_bytes", "memory.usage_in_bytes", "total_inactive_file")
    for name, level in levels:
        cap, used = _cgroup_text(level / names[0]) or "", _cgroup_text(level / names[1]) or ""
        if not cap.isdigit() or (total and int(cap) >= total):
            continue                                     # «max» или предел v1 выше памяти машины — предела нет
        stat = (_cgroup_text(level / "memory.stat") or "").splitlines()
        cache = next((v for k, _, v in (row.partition(" ") for row in stat) if k == names[2]), "")
        limit = int(cap) if limit is None else min(limit, int(cap))
        if not used.isdigit():
            room, why = 0, f"{names[1]} не прочитан — доступно 0"
        elif cache.isdigit():
            room, why = max(0, int(cap) - (int(used) - int(cache))), f"{names[0]} − ({names[1]} − {names[2]})"
        else:
            room, why = max(0, int(cap) - int(used)), f"{names[0]} − {names[1]} ({names[2]} нет — кэш не вычтен)"
        if available is None or room < available:
            available, how = room, f"cgroup {kind} {name}: {why}"
    if limit is not None and (not total or limit < total):
        total = limit
    return {"total": total or None, "available": available if total else None, "limit": limit, "source": how}


def _memory_portable() -> tuple[int | None, int | None, str]:
    """Нет /proc/meminfo — переносимая память psutil (HW-02: macOS `host_statistics64`, Windows `GlobalMemoryStatusEx`);
    нет и её — всего из sysconf POSIX, доступное неизвестно."""
    try:
        import psutil
    except ImportError as exc:
        why = f"нет /proc/meminfo, psutil не импортируется ({exc})"
        try:
            pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
        except (AttributeError, ValueError, OSError):  # sysconf — только POSIX, и имя есть не у всех систем
            return None, None, why
        return (pages * size, None, why + ", всего — sysconf") if pages > 0 and size > 0 else (None, None, why)
    vm = psutil.virtual_memory()
    return vm.total, vm.available, "psutil.virtual_memory"


def _cgroup_levels(proc: Path, fs_root: Path, controller: str) -> tuple[str, list[tuple[str, Path]]]:
    """Группы процесса для контроллера — от своей до корня монтирования: («v2» или «v1», [(путь группы, каталог)]).
    Контроллер не найден ни в v2, ни в v1 — пусто."""
    own = (_cgroup_text(proc / "self" / "cgroup") or "").splitlines()
    mounts = (_cgroup_text(proc / "self" / "mountinfo") or "").splitlines()
    for kind in ("v2", "v1"):
        # Строка /proc/self/cgroup — «иерархия:контроллеры:путь»; у v2 контроллеров нет («0::/путь»).
        lines = ((ctl, path) for _, _, rest in (row.partition(":") for row in own)
                 for ctl, sep, path in [rest.partition(":")] if sep)
        path = next((p for ctl, p in lines if (not ctl if kind == "v2" else controller in ctl.split(","))), None)
        if path is None:
            continue
        # Ядро дописывает « (deleted)» к пути группы, которую уже удалили.
        group = PurePosixPath(path.removesuffix(" (deleted)"))
        for row in mounts:
            head, _, tail = row.partition(" - ")
            fs_type, _, options = (tail.split() + ["", "", ""])[:3]
            try:                                        # поля mountinfo (proc(5)): id, родитель, устройство, корень, точка
                _, _, _, root, point, *_ = [re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), f) for f in head.split()]
            except ValueError:
                continue
            if fs_type != ("cgroup2" if kind == "v2" else "cgroup"):
                continue
            if kind == "v1" and controller not in options.split(","):
                continue
            top = fs_root.joinpath(*PurePosixPath(point).parts[1:])
            if kind == "v2" and controller not in (_cgroup_text(top / "cgroup.controllers") or "").split():
                break                                    # v2 смонтирован, но контроллер у v1 (гибрид)
            if not group.is_relative_to(root):
                break
            rel = PurePosixPath("/", group.relative_to(root))
            return kind, [(str(name), top.joinpath(*name.parts[1:])) for name in (rel, *rel.parents)]
    return "", []


def _cgroup_text(path: Path) -> str | None:
    """Текст файла группы или ядра; нет файла (корень иерархии, выключенный контроллер, не Linux) — None."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def placement(gpu_rules: dict, fit_rules: dict, entry: dict, where: dict) -> dict:
    """Класть модель целиком или с выгрузкой: она может быть больше выбранного устройства.

    Ответ на «не влезает» — не другой набор весов, а механизм самой библиотеки: держать на карте
    по одному блоку, остальное в ОЗУ. Медленнее, но работает; какой режим брать — объявлено, а не
    решено здесь. `error` означает отказ вслух: молча посчитать вдесятеро дольше — хуже отказа.
    """
    mode = str((gpu_rules or {}).get("oversize") or "error").lower()
    need_mb = round(FitEstimator(fit_rules).bytes_for(
        entry.get("params_by_dtype") or {}, entry.get("params_total"),
        as_dtype=where.get("dtype", "")) / 1e6)
    free = where["available_mb"]
    base = {"need_mb": need_mb, "available_mb": free, "device": where.get("device", "")}
    if where.get("device") == "cpu" or not need_mb or not free or need_mb <= free:
        # Неизвестный размер тоже кладём целиком: гадать «наверное, не влезет» — не лучше, а
        # отказ библиотеки по памяти будет громким и с настоящей причиной.
        return {**base, "mode": "device",
                "why": ("размер модели неизвестен — кладём целиком" if not need_mb
                        else f"нужно ~{need_mb} МБ из {free} МБ" if free or where.get("device") == "cpu"
                        # У карты 0 — и «ноль свободно», и «драйвер не назвал объём» (hardware._gpus).
                        else "объём памяти карты драйвер не назвал — кладём целиком, суда по памяти нет")}
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

    def bytes_for(self, params_by_dtype: dict, params_total: int | None = 0, as_dtype: str = "") -> int:
        """Сколько памяти нужно под веса + запас на активации.

        Считаем по РАЗРЯДНОСТИ каждого куска весов: одна и та же модель в fp16 весит вдвое
        меньше, чем в fp32, и вердикт из-за этого меняется на противоположный. `as_dtype` —
        разрядность, в которой модель РЕАЛЬНО поднимут (её задаёт устройство): веса лежат в
        fp32, а грузятся в fp16 — считать по диску значило бы завысить вдвое. Число параметров,
        которого источник не назвал, — None: тогда счёт по разрядностям.
        0 байт = источник не назвал ни total, ни by_dtype; реальной модели с 0 байт нет.
        """
        key = self.aliases.get(str(as_dtype).lower(), "")
        total = sum(int(c) for c in (params_by_dtype or {}).values()) if params_total is None else int(params_total)
        if key and total:
            raw = total * self.dtype_bytes.get(key, self.default_bytes)
        elif params_by_dtype:
            raw = sum(int(count) * self.dtype_bytes.get(str(dtype).upper(), self.default_bytes)
                      for dtype, count in params_by_dtype.items())
        else:
            raw = total * self.default_bytes
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
        return {"target": "cpu", "device": "ОЗУ", "available_mb": hw["ram_available_mb"]}

    def idle_gpu(self, hw: dict) -> dict:
        """Карта, память которой известна, но расчёту сейчас недоступна, — знать про неё полезно."""
        idle = [c for c in (hw.get("gpu") or []) if not c.get("usable") and c.get("vram_free_mb")]
        return max(idle, key=lambda c: c["vram_free_mb"]) if idle else {}

    def verdict(self, need_bytes: int, available_mb: int, device: str = "ОЗУ") -> dict:
        """Влезает / впритык / не влезает — с числами, по которым видно, почему. Доступно 0 — «не влезает»."""
        need_mb = round(need_bytes / 1e6)
        base = {"need_mb": need_mb, "available_mb": available_mb, "device": device}
        if not need_bytes:
            return {**base, "verdict": "unknown",
                    "why": "источник не назвал ни числа параметров, ни размера весов — оценить нечем"}
        if need_mb > available_mb:
            verdict, why = "no", f"нужно ~{need_mb} МБ при доступных {available_mb} МБ ({device})"
        elif need_mb > self.tight_ratio * available_mb:
            verdict, why = "tight", (f"нужно ~{need_mb} МБ из {available_mb} МБ ({device}) — "
                                     "впритык, соседние процессы могут не оставить места")
        else:
            verdict, why = "fits", f"нужно ~{need_mb} МБ из {available_mb} МБ ({device})"
        return {**base, "verdict": verdict,
                "why": why + ". Это ОЦЕНКА по весам и объявленному запасу, а не замер."}
