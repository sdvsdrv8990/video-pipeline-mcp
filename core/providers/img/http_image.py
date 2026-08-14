"""
core/providers/img/http_image.py — платный онлайн-провайдер «файл → файл» по объявленному описанию.

## Почему один адаптер на всех
Удаление фона и апскейл у платных сервисов — это HTTP-вызов с приложенным файлом; режима для них
в реестре LiteLLM нет, а различия между сервисами (адрес, имя поля, форма ключа, вид ответа)
описываются данными. Поэтому здесь механика вызова, а КТО именно вызывается — в
`config/providers.yaml → online.http`. Новый провайдер = блок в декларации, а не новый модуль.

## Ключ
Приходит в запросе и уходит ТОЛЬКО на объявленный адрес и только по https. В ответ клиенту он не
возвращается никогда; сообщение об отказе провайдера чистится вызывающим (`_hide_key`).
"""

import base64
from pathlib import Path

from ..adapters import MediaOutcome, MediaRequest
from ..resolver import ProviderError


class HttpImageAPI:
    """Синхронный адаптер: файл → запрос провайдеру → файл на диске."""

    def __init__(self, registry):
        self.registry = registry

    def _decl(self, provider: str) -> dict:
        table = ((self.registry.config.get("online") or {}).get("http")) or {}
        decl = table.get(provider)
        if not decl:
            raise ProviderError(
                "PROVIDER_ADAPTER_MISSING", f"Для '{provider}' не объявлено, как его вызывать.",
                reason=f"Объявлены: {', '.join(sorted(table)) or '(никто)'}. Добавь блок в "
                       "config/providers.yaml → online.http (адрес, ключ, поле файла, вид ответа) "
                       "или поставь в строке канала другого провайдера.",
                suggested_tool="media_provider_status")
        url = str(decl.get("url") or "")
        if not url.lower().startswith("https://"):
            # Ключ и картинка в открытом канале видны любому посреднику — это отказ, не оговорка.
            raise ProviderError(
                "DOWNLOAD_FORBIDDEN", f"Адрес провайдера не https: {url}",
                reason="С ключом и файлом ходим только по https.")
        return decl

    @staticmethod
    def _headers(decl: dict, api_key: str) -> dict:
        """Как провайдер принимает ключ — объявлено; форм ровно две, обе безымянные."""
        scheme = str(decl.get("auth") or "bearer")
        prefix = str(decl.get("auth_prefix") or "")
        if scheme.startswith("header:"):
            return {scheme.split(":", 1)[1]: f"{prefix}{api_key}"}
        return {"Authorization": f"{prefix or 'Bearer '}{api_key}"}

    def _fields(self, decl: dict, request: MediaRequest) -> dict:
        """Поля запроса из строки канала по объявленной карте. Чего в строке нет — не шлём."""
        out = {}
        for field, column in (decl.get("send") or {}).items():
            value = request.input if column == "input" else request.params.get(column)
            if value not in (None, ""):
                out[str(field)] = value
        return out

    def generate(self, request: MediaRequest) -> MediaOutcome:
        if request.source is None:
            raise ProviderError(
                "CONTENT_REJECTED", "Отправлять провайдеру нечего: не передан исходный файл.",
                reason="Для этого вида ресурса input — путь к файлу внутри рабочей области.")
        if not request.api_key:
            raise ProviderError(
                "PROVIDER_KEY_MISSING", f"У провайдера '{request.provider}' нет ключа доступа.",
                reason="Ключ вносит владелец: python scripts/set_provider_key.py.")
        decl = self._decl(request.provider)
        timeout = request.params.get("timeout")
        response = self._post(decl, request, float(timeout) if isinstance(timeout, (int, float)) else 120.0)
        kind = str(decl.get("response") or "binary")
        if kind == "binary":
            request.target.parent.mkdir(parents=True, exist_ok=True)
            request.target.write_bytes(response.content)
            return MediaOutcome(files=[request.target], meta={
                "engine": "http", "sync": True, "provider": request.provider,
                "compute": {"device": "remote", "why": f"вызов {decl.get('url')}"}})
        # Ссылка на готовый файл скачивается правилами media_tasks.yaml — теми же, что и у
        # остальных провайдеров: чужому адресу здесь доверия не больше.
        return MediaOutcome(files=[self.fetch(self._json(response), request.target)], meta={
            "engine": "http", "sync": True, "provider": request.provider,
            "compute": {"device": "remote", "why": f"вызов {decl.get('url')}"}})

    def _post(self, decl: dict, request: MediaRequest, timeout: float):
        """Сам вызов. Отказ провайдера — причина текстом, а не молчание."""
        import httpx

        headers = self._headers(decl, request.api_key)
        fields = self._fields(decl, request)
        field = str(decl.get("file_field") or "file")
        try:
            if str(decl.get("send_mode") or "multipart") == "json_base64":
                # Часть провайдеров принимает не multipart, а ссылку либо данные прямо в JSON.
                data = base64.b64encode(request.source.read_bytes()).decode()
                suffix = request.source.suffix.lstrip(".") or "png"
                payload = {**fields, field: f"data:image/{suffix};base64,{data}"}
                response = httpx.post(str(decl["url"]), headers=headers, json=payload,
                                      timeout=timeout, follow_redirects=False)
            else:
                with request.source.open("rb") as handle:
                    response = httpx.post(str(decl["url"]), headers=headers,
                                          files={field: (request.source.name, handle)},
                                          data=fields, timeout=timeout, follow_redirects=False)
        except Exception as e:                              # noqa: BLE001 — сеть/таймаут
            raise ProviderError(
                "PROVIDER_FAILED", f"Провайдер не ответил: {e}",
                reason="Сеть или сам сервис недоступны. Повтори позже либо переключи строку канала "
                       "на локального провайдера — он работает без сети и ключа.",
                suggested_tool="media_provider_status") from e
        if response.status_code in (401, 403):
            raise ProviderError(
                "AUTH_FAILED", f"Провайдер отклонил ключ ({response.status_code}).",
                reason="Ключ недействителен или у него нет прав на этот вызов. Заменить может "
                       "только владелец: python scripts/set_provider_key.py.")
        if response.status_code >= 400:
            raise ProviderError(
                "PROVIDER_FAILED", f"Провайдер ответил {response.status_code}: {response.text[:300]}",
                reason="Это ответ самого сервиса — в нём причина отказа (модерация, исчерпанный "
                       "платный лимит, неподходящий файл).")
        return response

    @staticmethod
    def _json(response) -> dict:
        try:
            return response.json()
        except Exception as e:                              # noqa: BLE001 — не JSON в теле
            raise ProviderError(
                "PROVIDER_FAILED", f"Ответ провайдера не разобрать как JSON: {e}",
                reason="Возможно, он отдаёт файл телом — тогда в декларации нужно "
                       "response: binary.") from e

    def fetch(self, answer: dict, target: Path) -> Path:
        """Забрать результат по ссылке из ответа. Правила загрузки — общие, media_tasks.yaml."""
        from ..download import ResultDownloader

        downloader = ResultDownloader(Path(self.registry.config_file).with_name("media_tasks.yaml"))
        for field in (downloader.rules.get("url_fields") or []):
            value = answer.get(field)
            if isinstance(value, str) and value.strip():
                downloader.fetch(value.strip(), target)
                return target
        raise ProviderError(
            "PROVIDER_FAILED", "В ответе провайдера нет ссылки на результат.",
            reason="Сервер ищет ссылку в полях из config/media_tasks.yaml → download.fetch."
                   "url_fields — добавь туда поле этого провайдера.")
