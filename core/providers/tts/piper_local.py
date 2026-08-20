"""
core/providers/tts/piper_local.py — озвучка локальной моделью (piper), без сети и ключей.

## Назначение
Локальная озвучка замыкает цепочку «строка канала → адаптер → файл → приёмка → расход» на этой
машине: облачную нельзя проверить без ключа и живого счёта, и всё вокруг остаётся теорией.

## Границы
- параметры вызова — столбцы строки провайдера, здесь только перевод их в API piper; имён
  моделей в коде нет;
- где лежат веса, знает реестр (`registry.models_dir`), а не этот файл: вторая копия раскладки
  разошлась бы молча;
- имя модели из строки проходит containment — строку правит ИИ, и `../../.env` в поле `model`
  уехало бы читать чужое; нет весов — честный `LOCAL_MODEL_MISSING`, а не тишина.
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
                raise ProviderError(
                    "LOCAL_INFERENCE_FAILED", f"Среда локальной озвучки не установлена: {e}",
                    reason="Поставь зависимости группы local (piper-tts) в окружение сервера.") from e
            return PiperVoice.load(model_path)

        size = model_path.stat().st_size if model_path.is_file() else 0
        return self.registry.pool.get(f"piper|{model_path}", load, need_bytes=size, device="cpu")

    def generate(self, request: MediaRequest) -> MediaOutcome:
        """Озвучить текст в файл. Синхронно: ждать нечего, результат сразу на диске."""
        if not (request.input or "").strip():
            raise ProviderError(
                "CONTENT_REJECTED", "Пустой текст озвучивать нечем.",
                reason="Передай текст фрагмента — повтор пустого запроса даст тот же отказ.")
        voice = self._voice(self.registry.require_model(request.params.get("model")))
        syn = self._synthesis_config(request.params)
        request.target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with wave.open(str(request.target), "wb") as wav:
                voice.synthesize_wav(request.input, wav, syn_config=syn)
        except Exception as e:                              # noqa: BLE001 — среда/веса/ресурсы
            raise ProviderError(
                "LOCAL_INFERENCE_FAILED", f"Локальная озвучка не выполнена: {e}",
                reason="Проверь веса модели и ресурсы машины; при повторе того же входа "
                       "результат будет тем же.") from e
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
