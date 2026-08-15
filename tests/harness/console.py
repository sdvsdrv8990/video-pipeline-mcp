"""
tests/harness/console.py — консоль сервера как наблюдаемый объект (требование C2 из `08 §6`).

Без чтения консоли «реал-пруф» невозможен: половина того, что сервер СООБЩАЕТ (бан по IP, отказ
файрвола, смена конфига на лету), видна только в его выводе, а не в ответе JSON-RPC. Тест, который
смотрит лишь на ответ, не отличит «правило сработало» от «правила нет».

Читаем в отдельном потоке и до конца: пайп с непрочитанным выводом переполняется, и сервер
встаёт на записи — тот же урок, что у супервизора туннеля.
"""

import re
import threading
import time


class Console:
    """Живой вывод процесса: копится в память, ждётся по образцу, проверяется целиком."""

    def __init__(self, stream, echo: bool = False):
        self._stream = stream
        self._echo = echo
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        for raw in self._stream:                      # блокирующее чтение до EOF
            line = raw.rstrip("\n")
            with self._lock:
                self._lines.append(line)
            if self._echo:
                print(f"    │ {line}")

    @property
    def lines(self) -> list[str]:
        with self._lock:
            return list(self._lines)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def contains(self, pattern: str) -> bool:
        """Regex по всему выводу. Именно regex: точные строки сервера меняются, суть — нет."""
        return re.search(pattern, self.text, re.I | re.M) is not None

    def wait_for(self, pattern: str, timeout: float = 10.0, poll: float = 0.05) -> str:
        """Дождаться строки по образцу. Не дождались — вернуть пусто, а не соврать про успех."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for line in self.lines:
                if re.search(pattern, line, re.I):
                    return line
            time.sleep(poll)
        return ""

    def tail(self, count: int = 20) -> str:
        return "\n".join(self.lines[-count:])
