"""
core/providers/model_spec.py — что модель принимает на вход и в каких пределах.

## Зачем
Строку канала заполняет ИИ. Какие параметры модель понимает и что для неё «слишком» — знает
только сама модель: `birefnet` работает на 1024 и 2048 и НЕ умеет 4К, у sd-turbo нативные 512.
Без спеки ИИ пишет параметры вслепую и узнаёт о невозможном из платного отказа — либо получает
не то, что просил, без единой жалобы.

## Формат — JSON Schema
Он уже язык этого сервера (`input_schema` инструментов), и проверяет его установленная библиотека,
а не наш код. Наше здесь — только политика: откуда взять и что считать расхождением.

## Источники и честность
Провайдер → наш справочник (`config/model_specs.yaml`) → файлы модели на диске. Что нашлось,
называется в ответе. Спеки нет — так и говорим: «не знаем» не выдаётся за «ограничений нет»,
иначе отсутствие данных выглядело бы как разрешение.
"""

import json
import time
from typing import Any
from pathlib import Path

from .declaration import Declaration
from .resolver import ProviderError


class ModelSpec:
    """Спецификация параметров модели: получение, кэш, сверка."""

    def __init__(self, registry):
        self.registry = registry
        self.models_dir = Path(registry.models_dir)

    @property
    def rules(self) -> dict:
        return (self.registry.config.get("capabilities") or {})

    @property
    def cache_dir(self) -> Path:
        return self.models_dir / str(self.rules.get("cache_dir") or "specs")

    # ═══ Кэш ═══

    def _cached(self, key: str) -> dict:
        path = self.cache_dir / f"{key}.json"
        if not path.is_file():
            return {}
        ttl = float(self.rules.get("ttl_hours") or 0) * 3600
        if ttl and (time.time() - path.stat().st_mtime) > ttl:
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _remember(self, key: str, spec: dict) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / f"{key}.json").write_text(
            json.dumps(spec, ensure_ascii=False), encoding="utf-8")

    # ═══ Получение ═══

    def of(self, provider: str, model: str, api_key: str = "", refresh: bool = False) -> dict:
        """Спека модели: схема параметров + откуда она взялась.

        Порядок — от самого достоверного к самому дешёвому. Ни один источник не обязателен:
        не нашлось нигде — возвращаем пустую схему и говорим об этом прямо.
        """
        key = f"{provider}__{str(model).replace('/', '_')}" or provider
        if not refresh:
            cached = self._cached(key)
            if cached:
                return cached
        spec = (self._from_provider(provider, model, api_key)
                or self._from_book(provider, model)
                or self._from_disk(model))
        if not spec:
            spec = {"schema": {}, "source": "none", "known": False, "why": (
                f"Ни провайдер, ни справочник, ни файлы модели не описывают параметры '{model}'. "
                "Это НЕ значит, что ограничений нет — значит, они неизвестны. Добавить их можно "
                "в config/model_specs.yaml.")}
        spec.setdefault("known", bool(spec.get("schema")))
        spec.update({"provider": provider, "model": model})
        # Кэшируется ТОЛЬКО ответ провайдера: он стоит сетевого запроса. Справочник и файлы модели
        # лежат рядом и читаются мгновенно — класть их в кэш значило бы, что правка
        # config/model_specs.yaml неделю не даёт эффекта (это и есть «конфиг не читается»).
        if spec.get("source") == "provider":
            self._remember(key, spec)
        return spec

    def _from_provider(self, provider: str, model: str, api_key: str) -> dict:
        """Спросить самого провайдера — у кого объявлен адрес спеки."""
        decl = ((self.rules.get("online") or {}).get(provider)) or {}
        url = str(decl.get("url") or "")
        if not url or not str(model).strip():
            return {}
        auth = str(decl.get("auth") or "none")
        if auth != "none" and not api_key:
            return {}
        # `model_path` — имя без версии: у Replicate в строке канала стоит `owner/name:версия`.
        url = url.format_map({"model": str(model), "model_path": str(model).split(":", 1)[0]})
        if not url.lower().startswith("https://"):
            raise ProviderError(
                "DOWNLOAD_FORBIDDEN", f"Адрес спецификации не https: {url}",
                reason="Спека тянется только по https — с ключом тем более.")
        try:
            import httpx
            headers = {"Authorization": f"Bearer {api_key}"} if auth == "bearer" else {}
            response = httpx.get(url, headers=headers, timeout=30, follow_redirects=False)
        except Exception:                                   # noqa: BLE001 — сеть/таймаут
            return {}                                       # спека необязательна: молча падать назад
        if response.status_code >= 400:
            return {}
        try:
            data = response.json()
        except Exception:                                   # noqa: BLE001 — не JSON
            return {}
        schema = self._dig_schema(data, str(decl.get("schema_path") or ""))
        if not schema:
            return {}
        return {"schema": self._normalize(schema, data), "source": "provider", "known": True,
                "why": f"Схему прислал сам провайдер ({url.split('?')[0]})."}

    @staticmethod
    def _dig_schema(data: dict, path: str) -> dict:
        """Достать схему по объявленному пути. `{Input}` — имя схемы, кончающееся на Input."""
        node: Any = data
        for part in path.split("."):
            if not part:
                continue
            if part == "{Input}" and isinstance(node, dict):
                name = next((k for k in node if str(k).endswith("Input")), "")
                node = node.get(name) if name else None
            elif isinstance(node, dict):
                node = node.get(part)
            else:
                return {}
            if node is None:
                return {}
        return node if isinstance(node, dict) else {}

    @staticmethod
    def _normalize(schema: dict, document: dict) -> dict:
        """Схема провайдера → то же самое, но самодостаточное.

        Ссылки `$ref` внутри чужого документа нам не разыменовать у клиента, поэтому свойство со
        ссылкой остаётся без ограничений — но остаётся видимым: молча выбросить параметр хуже,
        чем показать его без пределов.
        """
        props = {}
        for name, prop in (schema.get("properties") or {}).items():
            if not isinstance(prop, dict):
                continue
            keep = {k: v for k, v in prop.items()
                    if k in ("type", "default", "enum", "minimum", "maximum", "description",
                             "items", "format")}
            if "$ref" in prop and "type" not in keep:
                keep["description"] = (keep.get("description") or "") + " (тип объявлен ссылкой)"
            props[name] = keep
        return {"type": "object", "properties": props,
                "required": list(schema.get("required") or []),
                "title": schema.get("title") or document.get("info", {}).get("title", "")}

    def _from_book(self, provider: str, model: str) -> dict:
        """Наш справочник — для тех, кто схему не отдаёт, и для локальных моделей."""
        path = Path(self.registry.config_file).with_name("model_specs.yaml")
        if not path.is_file():
            return {}
        book = Declaration(path, ProviderError, "спецификаций моделей",
                           "Заведи config/model_specs.yaml или удали ссылку на него.").data
        entry = ((book.get("models") or {}).get(str(model))
                 or (book.get("providers") or {}).get(provider))
        if not entry:
            return {}
        return {"schema": entry.get("schema") or {}, "source": "book", "known": True,
                "description": str(entry.get("description") or ""),
                "why": "Параметры описаны в справочнике проекта (config/model_specs.yaml)."}

    def _from_disk(self, model: str) -> dict:
        """Прочитать саму модель: что она принимает, видно по её файлам.

        Дёшево и без сети — но и знает меньше: размер входа, кратность, нативное разрешение.
        """
        entry = self.registry.model_entry(model)
        if not entry:
            return {}
        try:
            path = self.registry.model_path(model)
        except ProviderError:
            return {}
        props: dict = {}
        notes = []
        graph = path if path.suffix == ".onnx" else next(path.glob("*.onnx"), None) if path.is_dir() else None
        if graph and graph.is_file():
            shape = self._onnx_input(graph)
            if shape:
                notes.append(f"вход графа {shape}")
                fixed = [d for d in shape[2:] if isinstance(d, int)]
                if fixed:
                    props["input_size"] = {"type": "integer", "enum": sorted(set(fixed)),
                                           "description": "Размер входа зашит в граф модели."}
                else:
                    props["input_size"] = {"type": "integer", "description": (
                        "Граф принимает любой размер (стороны округляются до кратности).")}
        native = self._diffusers_native(path)
        if native:
            notes.append(f"нативное разрешение {native}×{native}")
            props["img_size"] = {"type": "string", "default": f"{native}x{native}", "description": (
                f"Модель обучена на {native}×{native}; заметно больший кадр она портит, "
                "а не улучшает.")}
        if not props:
            return {}
        return {"schema": {"type": "object", "properties": props}, "source": "model_files",
                "known": True, "why": "Прочитано из файлов модели на диске: " + ", ".join(notes)}

    @staticmethod
    def _onnx_input(graph: Path) -> list:
        try:
            import onnxruntime as ort
            session = ort.InferenceSession(str(graph), providers=["CPUExecutionProvider"])
            return list(session.get_inputs()[0].shape)
        except Exception:                                   # noqa: BLE001 — граф не читается
            return []

    @staticmethod
    def _diffusers_native(path: Path) -> int:
        """Нативное разрешение диффузионной модели: `sample_size` латента × коэффициент VAE."""
        config = path / "unet" / "config.json" if path.is_dir() else None
        if not (config and config.is_file()):
            return 0
        try:
            sample = int(json.loads(config.read_text(encoding="utf-8")).get("sample_size") or 0)
        except (json.JSONDecodeError, OSError, ValueError):
            return 0
        return sample * 8 if sample else 0                  # VAE сжимает сторону в 8 раз

    # ═══ Сверка ═══

    def check(self, spec: dict, params: dict) -> list[dict]:
        """Расхождения строки канала со спекой. Проверяет библиотека, мы решаем, что проверять.

        Сверяются только ОБЪЯВЛЕННЫЕ моделью поля: в строке канала живут и наши служебные столбцы
        (`tile`, `cutoff`), и запрещать их значило бы ломать работающее.
        """
        schema = (spec or {}).get("schema") or {}
        props = schema.get("properties") or {}
        if not props:
            return []
        from jsonschema import Draft202012Validator

        subject = {k: v for k, v in params.items() if k in props and v not in (None, "")}
        validator = Draft202012Validator({"type": "object", "properties": props})
        out = []
        for error in validator.iter_errors(subject):
            field = error.path[0] if error.path else ""
            allowed = props.get(field, {})
            out.append({"param": str(field), "value": subject.get(field), "problem": error.message,
                        "allowed": {k: allowed[k] for k in ("enum", "minimum", "maximum", "type")
                                    if k in allowed}})
        return out
