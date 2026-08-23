"""
core/providers/ffmpeg/command.py — замысел сцены → аргументы ffmpeg.

## Назначение
Один фильтрограф на всю сцену: холст профиля, слои по возрастанию `z_index` с окном появления и
движением, дорожки звука в микс. Запуска здесь нет — команду можно собрать и прочитать глазами.

## Границы
В граф попадают только числа и токены закрытого словаря: всё, что пришло из книги, проходит
`guard`. Роли элементов движку не видны, снятие фона нейросетью — не его работа.
"""

from pathlib import Path

from .dictionary import FfmpegDictionary, fmt, guard
from .errors import FfmpegError
from .probe import MediaFacts
from .spec import Layer, SceneSpec

# Совместимость плееров важнее лишних цветов: без этого половина устройств не покажет кадр.
PIXEL_FORMAT = "yuv420p"
AUDIO_CODEC = "aac"
# Ключевые фильтры непрозрачности объявлены в словаре; движку нужно знать, какое имя брать по
# умолчанию, когда книга говорит «снять зелень», не называя параметров.
CHROMA_FILTER = "chromakey"


def ramp(pts: list[tuple[float, ...]], idx: int) -> str:
    """Ломаная `t → значение` выражением ffmpeg. До первой точки — стоим на ней, после — на последней."""
    if not pts:
        return "0"
    if len(pts) == 1:
        return fmt(pts[0][idx])
    expr = fmt(pts[-1][idx])
    for a, b in reversed(list(zip(pts, pts[1:]))):
        span = b[0] - a[0]
        step = (f"{fmt(a[idx])}+({fmt(b[idx] - a[idx])})*(t-{fmt(a[0])})/{fmt(span)}"
                if span > 0 else fmt(b[idx]))
        expr = f"if(lt(t,{fmt(b[0])}),{step},{expr})"
    return f"if(lt(t,{fmt(pts[0][0])}),{fmt(pts[0][idx])},{expr})"


def escape_path(path: Path) -> str:
    """Путь ВНУТРИ строки фильтра: `\\` `:` `'` там разделители, а не буквы."""
    text = str(path)
    for char in ("\\", ":", "'"):
        text = text.replace(char, "\\" + char)
    return text


class SceneCommand:
    """Сборка команды одной сцены: граф, входы, аргументы кодирования."""

    def __init__(self, spec: SceneSpec, facts: dict[Path, MediaFacts],
                 dictionary: FfmpegDictionary, duration_sec: float):
        self.spec = spec
        self.facts = facts
        self.dict = dictionary
        self.duration = duration_sec
        self.inputs: list[str] = []
        self.graph: list[str] = []
        self._index = 0

    # ═══ Входы ═══

    def _add_input(self, args: list[str]) -> int:
        self.inputs.extend(args)
        self._index += 1
        return self._index - 1

    def _layer_input(self, layer: Layer) -> int:
        got = self.facts[layer.asset_path]
        if got.kind == "image":
            return self._add_input(["-loop", "1", "-t", fmt(self.duration), "-i", str(layer.asset_path)])
        args = []
        if layer.source_in:
            args += ["-ss", fmt(layer.source_in)]
        if layer.source_out:
            args += ["-to", fmt(layer.source_out)]
        return self._add_input(args + ["-i", str(layer.asset_path)])

    # ═══ Слои ═══

    def _chain(self, layer: Layer) -> list[str]:
        """Цепочка обработки слоя: непрозрачность, объявленные фильтры, размер, темп."""
        if layer.alpha_mode == "REMOVE_BG":
            raise FfmpegError(
                "RENDER_INPUT_UNPREPARED", f"Слой {layer.element_id} просит снять фон нейросетью.",
                reason="ffmpeg режет фон только по цвету. Подготовь ассет провайдером и подставь готовый файл.",
                suggested_tool="media_generate")
        named = {f.name for f in layer.filters}
        steps = []
        if layer.alpha_mode == "CHROMAKEY" and not (named & {"chromakey", "colorkey"}):
            steps.append(self.dict.filter_expr(CHROMA_FILTER))
        steps += [self.dict.filter_expr(f.name, f.params) for f in layer.filters]
        width, height = layer.width, layer.height
        if layer.fit_canvas and not (width or height):
            width, height = self.spec.profile.size
        if width or height:
            steps.append(f"scale={fmt(width or -1)}:{fmt(height or -1)}")
        if layer.speed != 1.0 and self.facts[layer.asset_path].kind == "video":
            steps.append(f"setpts=PTS/{fmt(layer.speed)}")
        return steps

    def _overlay(self, layer: Layer) -> str:
        """Наложение: положение (точкой или ломаной) и окно присутствия на сцене."""
        motion = layer.motion
        x = f"'{ramp(motion, 1)}'" if motion else fmt(layer.x)
        y = f"'{ramp(motion, 2)}'" if motion else fmt(layer.y)
        args = [f"x={x}", f"y={y}"]
        if layer.time_start or layer.time_end:
            end = layer.time_end if layer.time_end else self.duration
            args.append(f"enable='between(t,{fmt(layer.time_start)},{fmt(end)})'")
        return "overlay=" + ":".join(args)

    def _video(self) -> str:
        width, height = self.spec.profile.size
        self.graph.append(f"color=c=black:s={width}x{height}:r={self.spec.profile.fps}:"
                          f"d={fmt(self.duration)}[v0]")
        last = "v0"
        for n, layer in enumerate(self.spec.ordered, start=1):
            source = f"{self._layer_input(layer)}:v"
            steps = self._chain(layer)
            if steps:
                self.graph.append(f"[{source}]{','.join(steps)}[e{n}]")
                source = f"e{n}"
            self.graph.append(f"[{last}][{source}]{self._overlay(layer)}[v{n}]")
            last = f"v{n}"
        if self.spec.subtitles_path and self.spec.profile.subtitles_mode == "BURN":
            self.graph.append(f"[{last}]subtitles=filename='{escape_path(self.spec.subtitles_path)}'[vs]")
            last = "vs"
        return last

    # ═══ Звук ═══

    def _audio(self) -> str | None:
        labels = []
        for n, track in enumerate(self.spec.audio, start=1):
            index = self._add_input(["-i", str(track.asset_path)])
            got = self.facts[track.asset_path]
            trim = f"atrim=start={fmt(track.source_in or 0)}"
            if track.source_out:
                trim += f":end={fmt(track.source_out)}"
            steps = [trim, "asetpts=PTS-STARTPTS"]
            steps += [f"atempo={fmt(step)}" for step in tempo_steps(track.speed)]
            curve = track.curve
            if curve:
                steps.append(f"volume='{ramp(curve, 1)}':eval=frame")
            elif track.volume != 1.0:
                steps.append(f"volume={fmt(track.volume)}")
            if track.fade_in_sec:
                steps.append(f"afade=t=in:st=0:d={fmt(track.fade_in_sec)}")
            if track.fade_out_sec:
                span = (track.source_out or got.duration_sec or 0) - (track.source_in or 0)
                start = max(0.0, span / track.speed - track.fade_out_sec)
                steps.append(f"afade=t=out:st={fmt(start)}:d={fmt(track.fade_out_sec)}")
            if track.time_start:
                delay = int(round(track.time_start * 1000))
                steps.append(f"adelay={delay}:all=1")
            self.graph.append(f"[{index}:a]{','.join(steps)}[a{n}]")
            labels.append(f"[a{n}]")
        if not labels:
            return None
        self.graph.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:"
                          f"dropout_transition=0:duration=longest[a]")
        return "a"

    # ═══ Команда целиком ═══

    def build(self, binary: str = "ffmpeg") -> list[str]:
        video = self._video()
        audio = self._audio()
        profile = self.spec.profile
        # Дорожкой субтитры приходят ОТДЕЛЬНЫМ входом, и он добавляется последним: номера входов
        # уже розданы графу, вставка в начало сдвинула бы каждую ссылку на слой.
        subtitles = (self._add_input(["-i", str(self.spec.subtitles_path)])
                     if self.spec.subtitles_path and profile.subtitles_mode == "TRACK" else None)
        argv = [binary, "-hide_banner", "-v", "error", "-nostdin", *self.inputs,
                "-filter_complex", ";".join(self.graph), "-map", f"[{video}]"]
        if audio:
            argv += ["-map", f"[{audio}]", "-c:a", AUDIO_CODEC]
        if subtitles is not None:
            argv += ["-map", f"{subtitles}:s", "-c:s", "mov_text"]
        argv += ["-c:v", guard(profile.codec, "codec")]
        if profile.bitrate:
            argv += ["-b:v", guard(profile.bitrate, "bitrate")]
        elif profile.crf is not None:
            argv += ["-crf", str(profile.crf)]
        argv += ["-r", str(profile.fps), "-t", fmt(self.duration), "-pix_fmt", PIXEL_FORMAT]
        return argv + ["-y", str(self.spec.output)]


def tempo_steps(speed: float) -> list[float]:
    """`atempo` держит 0.5–2.0; за диапазоном темп набирается цепочкой шагов, а не одним."""
    steps: list[float] = []
    rest = speed
    while rest > 2.0:
        steps.append(2.0)
        rest /= 2.0
    while rest < 0.5:
        steps.append(0.5)
        rest /= 0.5
    if abs(rest - 1.0) > 1e-6 or not steps:
        steps.append(rest)
    return [s for s in steps if abs(s - 1.0) > 1e-6]
