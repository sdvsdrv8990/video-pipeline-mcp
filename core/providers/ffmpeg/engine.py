"""core/providers/ffmpeg/engine.py — движок монтажа: собрать, запустить, принять результат.

Единственное место, где сцена превращается в файл: длительность выводится из книги и фактов о
входах, отказ бинаря переводится в код реестра, а готовый файл проходит приёмку — иначе успех
означал бы «файл есть», а не «файл годен». Ни книги, ни листов движок не знает, на вход приходит
замысел сцены. Оболочка не используется: аргументы уходят списком, поэтому текст из книги остаётся
значением, а не командой. Срока у рендера нет: живость — ход ffmpeg (`-progress`), остановка — снятие задания.
"""

import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.capacity import Capacity
from core.environment import child_spawn
from core.paths import Files

from .command import SceneCommand
from .dictionary import FfmpegDictionary
from .errors import FfmpegError, FfmpegSaid
from .probe import MediaFacts, accept, facts, own
from .probe import binary as on_path
from .spec import SceneSpec

PLACE = "render"                # класс места работы рендера: число мест — resources.yaml `work`
_GROWTH = ("out_time_sec", "frame", "total_size")   # рост любого из них с прошлого блока хода — работа


@dataclass(frozen=True)
class RenderOutcome:
    """Результат рендера: файл, подтверждённые факты о нём и команда, которой он собран."""

    path: Path
    duration_sec: float
    width: int
    height: int
    argv: list[str]


class FfmpegEngine:
    """Сборка и исполнение команд ffmpeg с приёмкой результата."""

    def __init__(self, dictionary: FfmpegDictionary | None = None, binary: str = "ffmpeg",
                 probe: str = "ffprobe", name: Callable[[Path], str] = own):
        """`name` — как отказ называет файл клиенту: путь от корня даёт вызывающий (дверь файлов), без него — имя."""
        self.dict = dictionary or FfmpegDictionary()
        self.binary_name = binary
        self.probe_name = probe
        self.name = name

    # ═══ Бинарь ═══

    def binary(self) -> str:
        return on_path(self.binary_name)

    # ═══ Замысел → команда ═══

    def inspect(self, spec: SceneSpec) -> dict[Path, MediaFacts]:
        """Факты о каждом входе, по одному разбору на файл: один ассет часто берут два слоя."""
        paths = [el.asset_path for el in spec.layers] + [tr.asset_path for tr in spec.audio]
        return {path: facts(path, self.probe_name, self.name) for path in dict.fromkeys(paths)}

    def duration(self, spec: SceneSpec, known: dict[Path, MediaFacts]) -> float:
        """Длительность сцены: сказанная книгой либо следующая из самих материалов."""
        declared = spec.declared_duration()
        if declared:
            return declared
        ends = []
        for layer in spec.layers:
            span = known[layer.asset_path].duration_sec
            if span:
                cut = (layer.source_out or span) - (layer.source_in or 0)
                ends.append(layer.time_start + cut / layer.speed)
        for track in spec.audio:
            span = known[track.asset_path].duration_sec
            if span:
                cut = (track.source_out or span) - (track.source_in or 0)
                ends.append(track.time_start + cut / track.speed)
        if not ends:                            # неподвижные картинки длительности не задают
            raise FfmpegError("VALIDATION_ERROR", facts={"scene_id": spec.scene_id}, errors=[
                {"field": "time_end", "rule": "required_for_stills", "expected": "time_end элемента или длительность сцены"}])
        return max(ends)

    def build(self, spec: SceneSpec) -> tuple[list[str], float]:
        """Команда сцены и её длительность; контейнер профиля судится здесь (muxer словаря) — отказ до запуска."""
        self.dict.muxer(spec.profile.container)
        known = self.inspect(spec)
        span = self.duration(spec, known)
        return SceneCommand(spec, known, self.dict, span, self.name).build(self.binary()), span

    # ═══ Запуск ═══

    def live(self, argv: list[str]) -> list[str]:
        """Команда запуска: ход — в stdout (`-progress pipe:1 -nostats` после `-nostdin`), потоки кодирования — доля
        ядер процесса на одно место рендера (`-threads` перед `-y`, `Capacity.share`)."""
        at, out = argv.index("-nostdin") + 1, argv.index("-y")
        return [*argv[:at], "-progress", "pipe:1", "-nostats", *argv[at:out],
                "-threads", str(Capacity().share(PLACE)), *argv[out:]]

    def run(self, argv: list[str], pulse=None, frames: int | None = None) -> str:
        """Запуск без оболочки и без срока. Ход — блоками `-progress` в `pulse` (работа — рост времени выхода, кадра
        или размера, а не сам блок; доля — кадр из обещанных замыслом `frames`: время выхода в блоке отстаёт от кадра);
        снятие задания убивает процесс (`pulse.on_cancel`) и кончается отказом снятия. Ненулевой код — отказ кодом
        реестра, а не сырым текстом наружу."""
        proc = subprocess.Popen(self.live(argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                **child_spawn("replace"))
        said: list[str] = []
        drain = threading.Thread(target=lambda: said.append(proc.stderr.read() if proc.stderr else ""), daemon=True)
        drain.start()
        if pulse is not None:
            pulse.on_cancel(proc.kill)
        try:
            self._follow(proc, pulse, frames)
        except BaseException:
            proc.kill()                         # ход не записался — процесс не остаётся сиротой
            raise
        finally:
            proc.wait()
            drain.join()
        if pulse is not None and pulse.cancelled.is_set():
            raise pulse.refusal()
        stderr = "".join(said)
        if proc.returncode != 0:
            raise self._failure(argv, stderr, proc.returncode)
        return stderr

    def _failure(self, argv: list[str], stderr: str, exit_code: int) -> FfmpegError:
        """Ненулевой код ffmpeg — отказ кодом карты отказов с фактами объявленного источника; причина не подтверждена
        фактом (пропавшего входа нет) — код по умолчанию. Слова ffmpeg — причиной в журнал (номер события — фактом)."""
        code, source = self.dict.failure(stderr)
        facts: dict = {"exit_code": exit_code}
        if source == "missing_input":         # вход-файл команды — абсолютный путь (lavfi-источник им не бывает)
            gone = [Path(p) for i, p in enumerate(argv[1:], 1) if argv[i - 1] == "-i"
                    and Path(p).is_absolute() and not Path(p).exists()]
            if gone:
                facts["path"] = self.name(gone[0])
            else:
                code = self.dict.failure_default()
        elif source == "encodable":
            allowed = self._encodable()
            if allowed:
                facts.update(column="codec", allowed=allowed)
            else:                               # бинарь не назвал энкодеров — причина не подтверждена
                code = self.dict.failure_default()
        refused = FfmpegError(code, facts=facts)
        refused.__cause__ = FfmpegSaid(stderr)
        return refused

    def _encodable(self) -> str:
        """Кодеки словаря, чьи энкодеры есть в этом бинаре (`-encoders`): что строка профиля может назвать; бинарь не
        ответил списком — пусто."""
        probe = subprocess.run([self.binary(), "-hide_banner", "-encoders"], capture_output=True, text=True,
                               **child_spawn("replace"))
        if probe.returncode != 0:
            return ""
        present = {line.split()[1] for line in probe.stdout.splitlines() if len(line.split()) > 1}
        return ", ".join(sorted(codec for codec, encoder in self.dict.encoders().items() if encoder in present))

    @staticmethod
    def _follow(proc: subprocess.Popen, pulse, frames: int | None) -> None:
        """Блоки хода из stdout до его конца: блок кончается строкой `progress=`."""
        block: dict[str, str] = {}
        last: dict = {}
        for line in proc.stdout or ():
            key, _, value = line.strip().partition("=")
            block[key] = value
            if key != "progress":
                continue
            seen = _progress(block, frames)
            if pulse is not None:
                pulse(seen, working=any((seen.get(k) or 0) > (last.get(k) or 0) for k in _GROWTH))
            last, block = seen, {}

    def render_scene(self, spec: SceneSpec, pulse=None) -> RenderOutcome:
        """Сцена из книги → проверенный файл. Успех означает, что результат принят ffprobe."""
        argv, span = self.build(spec)
        return self.execute(spec, argv, span, pulse)

    def execute(self, spec: SceneSpec, argv: list[str], span: float, pulse=None, owner: str = "render") -> RenderOutcome:
        """Собранная команда → файл, принятый ffprobe: сборку (`build`) вызов делает сам, до задания — отказы входа
        приходят сразу. Пишет в остаток владельца `owner` рядом с выходом (`-f` — muxer контейнера: по имени остатка
        формат не угадать) и ставит его на место дверью файлов только после приёмки: снятие, отказ ffmpeg или приёмки —
        остаток снят, по пути выхода ничего нового."""
        files = Files(spec.output.parent)
        part = files.remnant(spec.output.name, owner)
        part.parent.mkdir(parents=True, exist_ok=True)
        to_part = [*argv[:-1], "-f", self.dict.muxer(spec.profile.container), str(part)]   # последний — путь выхода
        try:
            self.run(to_part, pulse, round(span * spec.profile.fps))
            got = accept(part, span, spec.profile.size, probe=self.probe_name, name=self.name)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        files.publish(spec.output.name, owner)
        return RenderOutcome(path=spec.output, duration_sec=got.duration_sec, width=got.width, height=got.height,
                             argv=argv)


def _progress(block: dict[str, str], frames: int | None) -> dict:
    """Ход рендера из блока `-progress`: кадр, время выхода, размер, скорость; доля — кадр из обещанных замыслом.
    Значение «N/A» — None, а не 0."""
    out_us, frame = _number(block.get("out_time_us")), _number(block.get("frame"))
    seen: dict = {"frame": frame, "total_size": _number(block.get("total_size")),
                  "out_time_sec": None if out_us is None else round(out_us / 1e6, 3), "speed": block.get("speed")}
    if frames and frame is not None:
        seen["share"] = round(frame / frames, 3)
    return seen


def _number(raw: str | None) -> int | None:
    return int(raw) if raw and raw.lstrip("-").isdigit() else None
