"""
core/providers/img/http_image.py — платный онлайн-провайдер «файл → файл» по объявленному описанию.

## Почему один адаптер на всех
Удаление фона и апскейл у платных сервисов — это HTTP-вызов с приложенным файлом; режима для них
в реестре LiteLLM нет (её режимы: chat/embedding/image_generation/audio_*), а различия между
сервисами — адрес, имя поля, форма ключа, вид ответа — описываются данными. Поэтому здесь механика
вызова, а КТО именно вызывается — в `config/providers.yaml → online.http`. Новый провайдер = блок
в декларации, а не новый модуль.

## Три формы ответа, потому что их три в жизни
`binary` — файл телом (remove.bg); `json` — ссылка сразу (fal.run); `task` — идентификатор задачи и
опрос (Replicate, очередь fal). Форма объявлена, ждать умеет общий цикл `core/providers/task_cycle`.

## Ключ
Уходит ТОЛЬКО на объявленный адрес и только по https, в ответ клиенту не возвращается никогда;
сообщение об отказе провайдера чистится вызывающим (`_hide_key`).
"""

import base64
import time
from pathlib import Path
from urllib.parse import urlparse

from ..adapters import MediaOutcome, MediaRequest
from ..resolver import ProviderError


class HttpImageAPI:
    """Синхронный или очередной адаптер: файл → запрос провайдеру → файл на диске."""

    def __init__(self, registry):
        self.registry = registry
        self._decl_cache: dict = {}
        self._status_url = ""                               # адрес статуса задачи, если он динамический
        self._result_url = ""                               # у очереди fal результат лежит отдельно
        # Прозвонка задачи идёт тем же ключом, что и её создание, а `poll` получает только id.
        # Адаптер живёт ровно один вызов (реестр поднимает новый), поэтому ключ не переживает его.
        self._api_key = ""

    # ═══ Декларация ═══

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
        self._decl_cache[provider] = decl
        return decl

    def _url(self, decl: dict, params: dict) -> str:
        """Адрес вызова. В шаблон может подставляться модель из строки канала — но только имя.

        Строку канала правит ИИ, поэтому после подстановки адрес проверяется заново: он обязан
        остаться https и на том же хосте, что объявлен. Иначе `model` увела бы ключ на чужой сервер.
        """
        template = str(decl.get("url") or "")
        url = template
        if "{" in template:
            url = template.format_map({k: str(v) for k, v in params.items()})
        declared_host = urlparse(template.split("{", 1)[0]).netloc
        parsed = urlparse(url)
        if parsed.scheme != "https" or (declared_host and parsed.netloc != declared_host):
            raise ProviderError(
                "DOWNLOAD_FORBIDDEN", f"Адрес вызова не совпал с объявленным: {url}",
                reason=f"Ключ уходит только по https и только на {declared_host or 'объявленный хост'}. "
                       "Проверь столбец model строки канала — он подставляется в адрес.")
        return url

    @staticmethod
    def _headers(decl: dict, api_key: str) -> dict:
        """Как провайдер принимает ключ — объявлено; форм ровно две, обе безымянные."""
        scheme = str(decl.get("auth") or "bearer")
        prefix = str(decl.get("auth_prefix") or "")
        if scheme.startswith("header:"):
            return {scheme.split(":", 1)[1]: f"{prefix}{api_key}"}
        return {"Authorization": f"{prefix or 'Bearer '}{api_key}"}

    @staticmethod
    def _put(payload: dict, path: str, value) -> None:
        """Положить поле по объявленному пути: у Replicate параметры вложены в `input`."""
        node = payload
        parts = str(path).split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    @staticmethod
    def dig(data, path: str):
        """Достать значение по пути вида `output[0]` / `images[0].url`.

        Ответы этих API вложены: плоского поля со ссылкой у них нет, и искать её перебором имён
        значило бы угадывать. Путь объявлен провайдером в декларации.
        """
        node = data
        for part in str(path).replace("]", "").replace("[", ".").split("."):
            if part == "":
                continue
            if isinstance(node, dict):
                node = node.get(part)
            elif isinstance(node, (list, tuple)) and part.isdigit():
                node = node[int(part)] if int(part) < len(node) else None
            else:
                return None
        return node

    def _fields(self, decl: dict, request: MediaRequest) -> dict:
        """Поля запроса из строки канала по объявленной карте. Чего в строке нет — не шлём."""
        out: dict = {}
        for field, column in (decl.get("send") or {}).items():
            value = request.input if column == "input" else request.params.get(column)
            if value not in (None, ""):
                self._put(out, field, value)
        return out

    # ═══ Вызов ═══

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
        self._api_key = request.api_key
        response = self._request(decl, "POST", self._url(decl, request.params), request,
                                 body=self._body(decl, request))
        kind = str(decl.get("response") or "binary")
        meta = {"engine": "http", "provider": request.provider,
                "compute": {"device": "remote", "why": f"вызов {decl.get('url')}"}}
        if kind == "binary":
            request.target.parent.mkdir(parents=True, exist_ok=True)
            request.target.write_bytes(response.content)
            return MediaOutcome(files=[request.target], meta={**meta, "sync": True})
        answer = self._json(response)
        if kind == "task":
            # Адрес статуса провайдер сообщает сам (Replicate — urls.get, очередь fal — status_url):
            # строить его из имени модели значило бы дублировать знание, которое он уже прислал.
            self._status_url = str(self.dig(answer, str(decl.get("status_url_field") or "")) or "")
            self._result_url = str(self.dig(answer, str(decl.get("result_url_field") or "")) or "")
            task_id = str(self.dig(answer, str(decl.get("task_field") or "id")) or "")
            if not (task_id and self._status_url):
                raise ProviderError(
                    "PROVIDER_FAILED", "Провайдер не вернул задачу, которую можно прозвонить.",
                    reason="Ожидались поля из config/providers.yaml → online.http."
                           f"{request.provider}: task_field и status_url_field. Пришло: "
                           f"{', '.join(sorted(answer)[:8]) or '(пусто)'}.")
            return MediaOutcome(task_id=task_id, meta={**meta, "sync": False})
        return MediaOutcome(files=[self.fetch(answer, request.target)], meta={**meta, "sync": True})

    def _body(self, decl: dict, request: MediaRequest) -> dict:
        """Тело запроса: файл + поля строки. Форма отправки — свойство провайдера, не наше."""
        fields = self._fields(decl, request)
        field = str(decl.get("file_field") or "file")
        if str(decl.get("send_mode") or "multipart") != "json_base64":
            return {"files": {field: (request.source.name, request.source.read_bytes())},
                    "data": {k: v for k, v in fields.items() if not isinstance(v, (dict, list))}}
        # Провайдер принимает не multipart, а данные прямо в JSON. Base64 раздувает файл на треть,
        # поэтому предел объявлен: кадр 4К иначе уедет запросом на десятки мегабайт.
        limit_mb = float(decl.get("max_inline_mb") or 0)
        size_mb = request.source.stat().st_size / 1e6
        if limit_mb and size_mb > limit_mb:
            raise ProviderError(
                "CONTENT_REJECTED",
                f"Файл {round(size_mb)} МБ больше предела встроенной отправки ({round(limit_mb)} МБ).",
                reason="Этот провайдер принимает картинку внутри запроса, и большой файл он "
                       "отклонит сам. Уменьши кадр или подними max_inline_mb в "
                       "config/providers.yaml → online.http.")
        data = base64.b64encode(request.source.read_bytes()).decode()
        suffix = request.source.suffix.lstrip(".") or "png"
        self._put(fields, field, f"data:image/{suffix};base64,{data}")
        return {"json": fields}

    def _request(self, decl: dict, method: str, url: str, request: MediaRequest, body: dict | None = None):
        """Вызов с повторами. Сколько раз повторять — столбцы строки канала, а не число в коде.

        Повторяем только то, что повторять осмысленно: перегрузку и временную недоступность
        (`retry_on` в декларации — те же коды, что и у официальных клиентов этих API). Отказ по
        ключу или по содержимому повтором не лечится, и повторять его значит жечь платный лимит.
        """
        import httpx

        attempts = max(0, int(request.params.get("retry_count") or 0)) + 1
        pause = float(request.params.get("retry_delay") or 0)
        retry_on = {int(c) for c in (decl.get("retry_on") or [])}
        timeout = request.params.get("timeout")
        timeout = float(timeout) if isinstance(timeout, (int, float)) else 120.0
        headers = self._headers(decl, request.api_key)
        last = None
        for attempt in range(attempts):
            try:
                response = httpx.request(method, url, headers=headers, timeout=timeout,
                                         follow_redirects=False, **(body or {}))
            except Exception as e:                          # noqa: BLE001 — сеть/таймаут
                last = ProviderError(
                    "PROVIDER_FAILED", f"Провайдер не ответил: {e}",
                    reason="Сеть или сам сервис недоступны. Повтори позже либо переключи строку "
                           "канала на локального провайдера — он работает без сети и ключа.",
                    suggested_tool="media_provider_status")
            else:
                if response.status_code < 400:
                    return response
                last = self._refusal(response)
                if response.status_code not in retry_on:
                    raise last
                # Провайдер сам говорит, когда повторять — его срок важнее нашего.
                pause = max(pause, float(response.headers.get("Retry-After") or 0))
            if attempt + 1 < attempts and pause:
                time.sleep(pause)
                pause *= 2                                  # перегрузку не лечат равномерным долблением
        raise last

    @staticmethod
    def _refusal(response) -> ProviderError:
        """Ответ ≥400 → отказ причиной провайдера, а не общим «что-то пошло не так»."""
        if response.status_code in (401, 403):
            return ProviderError(
                "AUTH_FAILED", f"Провайдер отклонил ключ ({response.status_code}).",
                reason="Ключ недействителен или у него нет прав на этот вызов. Заменить может "
                       "только владелец: python scripts/set_provider_key.py.")
        return ProviderError(
            "PROVIDER_FAILED", f"Провайдер ответил {response.status_code}: {response.text[:300]}",
            reason="Это ответ самого сервиса — в нём причина отказа (модерация, исчерпанный "
                   "платный лимит, неподходящий файл).")

    @staticmethod
    def _json(response) -> dict:
        try:
            return response.json()
        except Exception as e:                              # noqa: BLE001 — не JSON в теле
            raise ProviderError(
                "PROVIDER_FAILED", f"Ответ провайдера не разобрать как JSON: {e}",
                reason="Возможно, он отдаёт файл телом — тогда в декларации нужно "
                       "response: binary.") from e

    # ═══ Задача ═══

    def poll(self, task_id: str) -> dict:
        """Статус задачи. Слова статусов разбирает общий цикл — здесь только запрос."""
        import httpx

        try:
            response = httpx.get(self._status_url, headers=self._poll_headers(), timeout=60,
                                 follow_redirects=False)
        except Exception as e:                              # noqa: BLE001 — сеть/таймаут
            raise ProviderError(
                "PROVIDER_FAILED", f"Не удалось прозвонить задачу '{task_id}': {e}",
                reason="Задача осталась на стороне провайдера — прозвони её повторным вызовом.") from e
        if response.status_code >= 400:
            raise self._refusal(response)
        return self._json(response)

    def _poll_headers(self) -> dict:
        decl = next(iter(self._decl_cache.values()), {})
        return self._headers(decl, self._api_key) if self._api_key else {}

    def fetch(self, answer: dict, target: Path) -> Path:
        """Забрать результат. Правила загрузки — общие, из media_tasks.yaml."""
        from ..download import ResultDownloader

        decl = next(iter(self._decl_cache.values()), {})
        if self._result_url:
            # Очередь fal отдаёт результат отдельным адресом: статус говорит лишь «готово».
            answer = self._json(self._get(self._result_url))
        url = ""
        if decl.get("result_path"):
            found = self.dig(answer, str(decl["result_path"]))
            url = found if isinstance(found, str) else ""
        downloader = ResultDownloader(Path(self.registry.config_file).with_name("media_tasks.yaml"))
        if not url:
            for field in (downloader.rules.get("url_fields") or []):
                value = answer.get(field)
                if isinstance(value, str) and value.strip():
                    url = value.strip()
                    break
        if not url:
            raise ProviderError(
                "PROVIDER_FAILED", "В ответе провайдера нет ссылки на результат.",
                reason=f"Искали по объявленному пути ({decl.get('result_path') or '—'}) и в полях "
                       "config/media_tasks.yaml → download.fetch.url_fields. Поправь путь в "
                       "config/providers.yaml → online.http.")
        downloader.fetch(url, target)
        return target

    def _get(self, url: str):
        import httpx

        try:
            response = httpx.get(url, headers=self._poll_headers(), timeout=60, follow_redirects=False)
        except Exception as e:                              # noqa: BLE001 — сеть/таймаут
            raise ProviderError(
                "PROVIDER_FAILED", f"Результат задачи не забрать: {e}",
                reason="Задача готова, но её результат недоступен — повтори прозвонку.") from e
        if response.status_code >= 400:
            raise self._refusal(response)
        return response
