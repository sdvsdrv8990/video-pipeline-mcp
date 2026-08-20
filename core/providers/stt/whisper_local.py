"""
core/providers/stt/whisper_local.py — транскрибация локальной моделью whisper, без сети и ключей.

## Назначение
Речь → текст с таймкодами по словам на этой машине: вход фазы P3, без которого разбор
таймкодов и пауз остаётся теорией, а облачный STT непроверяем без ключа и живого счёта.

## Границы
- модель поднимается ТОЛЬКО из safetensors и ТОЛЬКО с диска: веса приходят своим установщиком,
  который отсеивает pickle (`.pt`/`.bin` исполняют код при загрузке);
- параметры вызова — столбцы строки и декларация, не ветки здесь;
- звук декодирует ffmpeg (системный бинарь, не pip) — его отсутствие обязано быть слышно;
- результат уходит ФАЙЛОМ: текст в `meta` уехал бы клиенту мимо конверта провенанса (S3);
- нет весов — `LOCAL_MODEL_MISSING`, нет среды — `LOCAL_INFERENCE_FAILED`, а не тишина.
"""

import json
import shutil
from pathlib import Path

from ..adapters import MediaOutcome, MediaRequest
from ..resolver import ProviderError


def _write_json(target: Path, out: dict) -> None:
    """Текст + границы слов. Форма ответа фиксируется здесь: её читает клиент."""
    words = [{"word": (c.get("text") or "").strip(), "start": (c.get("timestamp") or [None])[0],
              "end": (c.get("timestamp") or [None, None])[1]} for c in (out.get("chunks") or [])]
    target.write_text(json.dumps({"text": (out.get("text") or "").strip(), "words": words},
                                 ensure_ascii=False, indent=2), encoding="utf-8")


def _write_txt(target: Path, out: dict) -> None:
    target.write_text((out.get("text") or "").strip() + "\n", encoding="utf-8")


# Расширение → чем писать. Каждое обязано быть разрешено write_allowlist — это инвариант,
# и он проверяется тестом, а не дисциплиной: два списка, обязанных совпадать, иначе разойдутся.
WRITERS = {"json": _write_json, "txt": _write_txt}


class WhisperLocalSTT:
    """Синхронный адаптер: аудиофайл → файл транскрипта на диске."""

    def __init__(self, registry):
        self.registry = registry

    @property
    def _local(self) -> dict:
        return self.registry.config.get("local") or {}

    @property
    def _stt_rules(self) -> dict:
        return self._local.get("stt") or {}

    def _where(self, params: dict) -> dict:
        """Где считать: решает проба железа (или столбец строки), а не этот файл."""
        from ..hardware import compute_device
        return compute_device(self._local.get("gpu") or {}, str(params.get("device") or "").strip())

    def _pipe(self, model_dir: Path, where: dict, revision: str):
        """Модель живёт в пуле процесса: адаптер создаётся на каждый вызов, а модель — нет."""
        def load():
            try:
                import torch
                from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
            except ImportError as e:
                raise ProviderError(
                    "LOCAL_INFERENCE_FAILED", f"Среда локального распознавания не установлена: {e}",
                    reason="Поставь зависимости группы local (transformers, torch).") from e
            device = where["device"]
            dtype = getattr(torch, str(where.get("dtype") or "float32"), torch.float32)
            # `local_files_only` — сеть при вызове инструмента запрещена: веса приходят только
            # своим установщиком. `revision` из описи фиксирует, каким снимком они поставлены.
            model = AutoModelForSpeechSeq2Seq.from_pretrained(
                str(model_dir), revision=revision, local_files_only=True, use_safetensors=True,
                dtype=dtype, low_cpu_mem_usage=True).to(device)
            proc = AutoProcessor.from_pretrained(str(model_dir), revision=revision,
                                                 local_files_only=True)
            return pipeline(task="automatic-speech-recognition", model=model,
                            tokenizer=proc.tokenizer, feature_extractor=proc.feature_extractor,
                            device=device, **(self._stt_rules.get("pipeline") or {}))

        size = sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file())
        return self.registry.pool.get(f"whisper|{model_dir}|{where['device']}", load,
                                      need_bytes=size, device=where["device"])

    def _generate_kwargs(self, params: dict) -> dict:
        """Столбцы строки → параметры генерации. Пустой столбец не передаётся вовсе."""
        out = {}
        for column, param in (self._stt_rules.get("generate_from_row") or {}).items():
            value = params.get(column)
            if value not in (None, ""):
                out[param] = value
        return out

    def _writer(self, target: Path):
        """Формат результата по расширению. Неизвестный — отказ, а не молчаливый дефолт."""
        ext = target.suffix.lstrip(".").lower()
        if ext not in WRITERS:
            raise ProviderError(
                "CONTENT_REJECTED", f"Формат транскрипта '{ext}' не поддерживается.",
                reason=f"Поставь в столбец response_format один из: {', '.join(sorted(WRITERS))}.")
        return WRITERS[ext]

    def generate(self, request: MediaRequest) -> MediaOutcome:
        """Транскрибировать аудио в файл. Синхронно: результат сразу на диске."""
        source = request.source
        if source is None or not source.is_file():
            raise ProviderError(
                "CONTENT_REJECTED", "Нечего транскрибировать: исходный аудиофайл не найден.",
                reason="Передай путь к существующему файлу записи — повтор того же входа даст "
                       "тот же отказ.")
        if not shutil.which("ffmpeg"):
            raise ProviderError(
                "LOCAL_INFERENCE_FAILED", "Нечем декодировать звук: ffmpeg не найден в PATH.",
                reason="ffmpeg — системный бинарь, а не пакет pip: поставь его пакетным "
                       "менеджером (apt/dnf/brew). Без него распознавание не поднимется вовсе.")
        writer = self._writer(request.target)
        where = self._where(request.params)
        # Опись хранит путь к ФАЙЛУ весов, а `from_pretrained` нужен каталог с конфигом и
        # токенайзером рядом. Различаем по диску, а не по имени модели.
        name = request.params.get("model")
        ref = self.registry.require_model(name)
        entry = self.registry.model_entry(name) or {}
        pipe = self._pipe(ref if ref.is_dir() else ref.parent, where,
                          str(entry.get("revision") or "main"))
        request.target.parent.mkdir(parents=True, exist_ok=True)
        try:
            out = pipe(str(source), generate_kwargs=self._generate_kwargs(request.params),
                       **(self._stt_rules.get("call") or {}))
            writer(request.target, out)
        except ProviderError:
            raise
        except Exception as e:  # среда/веса/ресурсы
            raise ProviderError(
                "LOCAL_INFERENCE_FAILED", f"Локальное распознавание не выполнено: {e}",
                reason="Проверь, что файл — распознаваемая запись, и ресурсы машины; при "
                       "повторе того же входа результат будет тем же.") from e
        return MediaOutcome(files=[request.target],
                            meta={"engine": "whisper", "sync": True, "compute": where})
