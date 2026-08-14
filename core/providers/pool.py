"""
core/providers/pool.py — поднятая модель переживает вызов инструмента.

## Зачем
Каждый `media_generate` поднимает адаптер заново, а вместе с ним и модель. Замер S24: sd-turbo
на холодную 10.9 с, с прогретым дисковым кэшем ядра 1.4 с, и 0.2 с — если модель уже поднята. То есть
1.2 с уходило на каждый кадр впустую: страницы весов ядро кэширует само, а вот разбор в структуры
и перенос на карту приходится повторять.

## Почему это не противоречит «демона нет» (S15)
Пул ничего не делает сам: он не тикает, не подхватывает задачи и не просыпается в фоне. Он лишь
не выбрасывает то, что уже подняли, и убирает залежавшееся В МОМЕНТ следующего обращения. Отсюда
честная граница: пока сервер простаивает, память остаётся занятой — выгрузка случится с первым же
вызовом, а не «когда-нибудь сама».

## Бюджет вместо веры
Модель заранее неизвестна, поэтому пул не знает её размера — он его СПРАШИВАЕТ у вызывающего
(оценка по описи) и уточняет замером на карте. Пока укладываемся в объявленную долю свободной
памяти — держим; не укладываемся — выгружаем давно не использованное, начиная с самого старого.
"""

import threading
import time

_LOCK = threading.Lock()
_ENTRIES: dict[str, dict] = {}


class ModelPool:
    """Живые модели процесса: ключ → поднятый объект. Один на процесс, состояние модульное."""

    def __init__(self, rules: dict | None = None, hardware: dict | None = None):
        self.rules = rules or {}
        self.hardware = hardware or {}

    @property
    def enabled(self) -> bool:
        return bool(self.rules.get("enabled", True))

    # ═══ Бюджет ═══

    def budget_bytes(self, device: str) -> int:
        """Сколько памяти пулу разрешено занять на этом устройстве.

        Доля СВОБОДНОГО, а не всего: сервер живёт рядом с чужими процессами, и забирать всё
        значит менять один тормоз на другой.
        """
        ratio = float(self.rules.get("budget_ratio") or 0.5)
        free_mb = 0.0
        if device != "cpu":
            cards = [c for c in (self.hardware.get("gpu") or []) if c.get("usable")]
            free_mb = float(cards[0].get("vram_free_mb") or 0) if cards else 0.0
        if not free_mb:
            free_mb = float(self.hardware.get("ram_available_mb") or 0)
        return int(free_mb * 1e6 * ratio)

    def held_bytes(self, device: str = "") -> int:
        return sum(e["bytes"] for e in _ENTRIES.values() if not device or e["device"] == device)

    # ═══ Доступ ═══

    def get(self, key: str, loader, need_bytes: int = 0, device: str = "cpu"):
        """Отдать поднятую модель или поднять её, освободив место по объявленному бюджету."""
        if not self.enabled:
            return loader()
        with _LOCK:
            self._evict_idle()
            entry = _ENTRIES.get(key)
            if entry is not None:
                entry["used"] = time.time()
                entry["hits"] += 1
                return entry["object"]
            self._make_room(need_bytes, device)
        # Подъём идёт ВНЕ замка: он длится секундами, и держать на нём остальные вызовы незачем.
        before = self._device_used(device)
        obj = loader()
        measured = max(0, self._device_used(device) - before)
        with _LOCK:
            _ENTRIES[key] = {"object": obj, "device": device, "hits": 0,
                             # Замер точнее оценки, но он есть только на карте: на процессоре
                             # занятое моделью не отделить от прочего, и там остаётся оценка.
                             "bytes": measured or need_bytes,
                             "measured": bool(measured),
                             "loaded": time.time(), "used": time.time()}
        return obj

    def stats(self) -> dict:
        """Что держим прямо сейчас — вместе с бюджетом, иначе числа не с чем сравнить."""
        with _LOCK:
            rows = [{"key": k, "device": e["device"], "mb": round(e["bytes"] / 1e6, 1),
                     "measured": e["measured"], "hits": e["hits"],
                     "idle_sec": round(time.time() - e["used"], 1)}
                    for k, e in _ENTRIES.items()]
        devices = sorted({r["device"] for r in rows})
        return {"enabled": self.enabled, "models": rows,
                "budget_mb": {d: round(self.budget_bytes(d) / 1e6) for d in devices},
                "held_mb": {d: round(self.held_bytes(d) / 1e6, 1) for d in devices},
                "idle_ttl_sec": float(self.rules.get("idle_ttl_sec") or 0),
                "note": ("Модель держится поднятой между вызовами: подъём стоит секунд, повторный "
                         "вызов — доли секунды. Залежавшееся выгружается при следующем обращении, "
                         "а не демоном в фоне (демона на сервере нет).")}

    def release_all(self) -> int:
        """Выгрузить всё. Нужен там, где память важнее скорости (и в тестах)."""
        with _LOCK:
            keys = list(_ENTRIES)
            for key in keys:
                self._drop(key)
        return len(keys)

    # ═══ Освобождение ═══

    def _evict_idle(self) -> None:
        """Убрать то, чем давно не пользовались. Карта дороже прогрева: 1.4 с против занятых ГБ."""
        ttl = float(self.rules.get("idle_ttl_sec") or 0)
        if not ttl:
            return
        now = time.time()
        for key in [k for k, e in _ENTRIES.items() if now - e["used"] > ttl]:
            self._drop(key)

    def _make_room(self, need_bytes: int, device: str) -> None:
        """Освободить место под новую модель: сначала самое давнее."""
        limit = int(self.rules.get("max_entries") or 0)
        budget = self.budget_bytes(device)
        order = sorted((k for k, e in _ENTRIES.items() if e["device"] == device),
                       key=lambda k: _ENTRIES[k]["used"])
        while order and ((budget and self.held_bytes(device) + need_bytes > budget)
                         or (limit and len(_ENTRIES) >= limit)):
            self._drop(order.pop(0))

    @staticmethod
    def _drop(key: str) -> None:
        entry = _ENTRIES.pop(key, None)
        if entry is None:
            return
        del entry["object"]
        import gc
        gc.collect()
        if entry["device"] != "cpu":
            # Освобождённое на карте возвращается драйверу не само: без этого память остаётся
            # занятой процессом, и следующая модель «не влезает» на пустом месте.
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:                               # noqa: BLE001 — torch не обязателен
                pass

    @staticmethod
    def _device_used(device: str) -> int:
        """Сколько памяти занято на устройстве сейчас. На процессоре — не измерить честно."""
        if device == "cpu":
            return 0
        try:
            import torch
            return int(torch.cuda.memory_allocated()) if torch.cuda.is_available() else 0
        except Exception:                                   # noqa: BLE001 — torch не обязателен
            return 0
