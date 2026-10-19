"""core/providers/tts/piper_local.py — озвучка локальной моделью, без сети и ключей.

Локальная озвучка замыкает цепочку «строка канала → адаптер → файл → приёмка → расход» на этой
машине: облачную нельзя проверить без ключа и живого счёта, и всё вокруг остаётся теорией.
Параметры вызова — столбцы строки провайдера, имён моделей в коде нет. Где лежат веса, знает
реестр, а не этот файл: вторая копия раскладки разошлась бы молча. Имя модели из строки проходит
containment — строку правит ИИ, и путь наружу уехал бы читать чужое; нет весов — честный отказ, а
не тишина.
"""

import wave
from pathlib import Path

from ..adapters import MediaOutcome, MediaRequest
from ..resolver import ProviderError


class PiperLocalTTS:
    """Синхронный адаптер: текст → wav на диске."""

    def __init__(self, registry):
        self.registry = registry

    def _voice(self, model_path: Path):
        """Модель живёт в пуле процесса: адаптер создаётся на каждый вызов, а голос — нет."""
        def load():
            try:
                from piper import PiperVoice
            except ImportError as e:
                raise ProviderError("LOCAL_INFERENCE_FAILED") from e
            return PiperVoice.load(model_path)

        size = model_path.stat().st_size if model_path.is_file() else None
        return self.registry.pool.get(f"piper|{model_path}", load, need_bytes=size, device="cpu")

    def ready(self, request: MediaRequest) -> dict:
        """Готовность до вызова (MF-13): текст есть, веса голоса готовы — путь и ступень проверки; иначе отказ кодом
        причины. Зовут дверь до задания и `generate` перед расчётом: диск мог смениться между ними."""
        if not (request.input or "").strip():
            raise ProviderError("VALIDATION_ERROR", errors=[{"field": "input", "rule": "required"}])
        return self.registry.ready(request.params.get("model"))

    def generate(self, request: MediaRequest) -> MediaOutcome:
        """Озвучить текст в файл. Синхронно: ждать нечего, результат сразу на диске."""
        voice = self._voice(self.ready(request)["path"])
        syn = self._synthesis_config(request.params)
        request.target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with wave.open(str(request.target), "wb") as wav:
                voice.synthesize_wav(request.input, wav, syn_config=syn)
        except Exception as e:  # среда/веса/ресурсы
            raise ProviderError("LOCAL_INFERENCE_FAILED") from e
        return MediaOutcome(files=[request.target], meta={"engine": "piper", "sync": True})

    @staticmethod
    def _synthesis_config(params: dict):
        """Столбцы строки → параметры синтеза. Пустой столбец = дефолт модели."""
        from piper import SynthesisConfig

        cfg = SynthesisConfig()
        speed = params.get("speed")
        if isinstance(speed, (int, float)) and speed > 0:
            # У piper масштаб ДЛИТЕЛЬНОСТИ: быстрее речь = короче длительность.
            cfg.length_scale = 1.0 / float(speed)
        speaker = params.get("voice")
        if isinstance(speaker, (int, float)):
            cfg.speaker_id = int(speaker)
        elif isinstance(speaker, str) and speaker.strip().isdigit():
            cfg.speaker_id = int(speaker.strip())
        return cfg
