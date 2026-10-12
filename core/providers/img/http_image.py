"""core/providers/img/http_image.py — провайдер «файл → файл» по HTTP, по объявленному описанию.

Механика вызова общая на всех таких провайдеров: различия — адрес, имя поля, форма ключа, вид
ответа — описаны объявлением, и новый провайдер есть блок в нём, а не файл кода. Раннер локальных
моделей говорит тем же языком и едет сюда же, поэтому переключение «в процессе → в раннере»
остаётся правкой строки канала. Форма ответа объявлена: файл телом, ссылка, задача с опросом, путь
на диске. Ключ уходит ТОЛЬКО на объявленный адрес и по объявленной схеме (не https — только литералу петли, где
сеть не пересекается), клиенту не возвращается и из сообщения об отказе вычищается. В сеть вызов идёт одной дверью
загрузчика (`Fetcher.call`): суд адреса и IP, срок вызова, повтор только того, что провайдер не применил.
"""

import base64
import functools
import random
import socket
import time
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

from pydantic import ValidationError

from core.declaration import Declaration
from core.declaration_schemas import ONLINE_CALL, RESULT_ANSWER, CallPolicy, ProviderUrl, Slot
from core.paths import Files

from ..adapters import MediaOutcome, MediaRequest
from ..catalog import provider_refusal
from ..download import addressed, answers, results
from ..resolver import ProviderError


class HttpImageAPI:
    """Синхронный или очередной адаптер: файл → запрос провайдеру → файл на диске."""

    # Сеть загрузчика результата (MF-16): None — настоящий транспорт; адаптер поднимает реестр, подмена — на классе.
    transport: ClassVar = None
    resolve: ClassVar = staticmethod(socket.getaddrinfo)
    # Часы и сон двери ответа: ими меряется срок опроса и пауза повтора; подмена — на классе, как у сети.
    clock: ClassVar = staticmethod(time.monotonic)
    sleep: ClassVar = staticmethod(time.sleep)
    # Столбцы строки канала поверх политики вызова: ключ политики ← столбец (попыток на одну больше, чем повторов).
    ROW: ClassVar[dict[str, str]] = {"attempts": "retry_count", "backoff_sec": "retry_delay", "timeout_sec": "timeout"}

    def __init__(self, registry, rng: random.Random | None = None):
        self.registry = registry
        self._rng = rng or random.Random()                  # разброс паузы — от своего генератора, не общего
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

    def _section(self, provider: str) -> tuple[Slot, CallPolicy]:
        """Политика вызова провайдера из декларации: свой блок `online.http.<P>.call` (слияние `<<: *call` с разделом
        `online.call`) или сам раздел; битый ключ — отказ декларации с его путём."""
        own = Slot(ONLINE_CALL.file, ("online", "http", provider, "call"), CallPolicy)
        slot = own if "call" in self._decl_cache.get(provider, {}) else ONLINE_CALL
        return slot, self._providers.section(*slot.section, schema=CallPolicy)

    def _policy(self, request: MediaRequest) -> tuple[Slot, CallPolicy]:
        """Политика вызова с перекрытием столбцами строки канала (`ROW`); пустая ячейка не перекрывает. Склейку судит схема:
        негодный столбец — отказ строки с его именем, а не молчаливая обрезка."""
        slot, policy = self._section(request.provider)
        row = {key: request.params[column] for key, column in self.ROW.items()
               if request.params.get(column) not in (None, "")}
        if type(row.get("attempts")) is int:
            row["attempts"] += 1
        try:
            return slot, CallPolicy.model_validate({**policy.model_dump(exclude={"hosts"}), **row})
        except ValidationError as e:
            raise ProviderError("BOOK_ROW_INVALID", errors=[
                {"field": self.ROW.get(str(err["loc"][0]), ".".join(map(str, (*slot.section, *err["loc"])))),
                 "rule": err["type"]} for err in e.errors()]) from None

    def _url(self, decl: dict, params: dict, schemes) -> str:
        """Адрес вызова. В шаблон может подставляться модель из строки канала — но только имя.

        Строку канала правит ИИ, поэтому после подстановки адрес судится заново (`_own`): иначе `model`
        увела бы ключ на чужой сервер.
        """
        template = str(decl.get("url") or "")
        url = template
        if "{" in template:
            url = template.format_map({k: str(v) for k, v in params.items()})
        return self._own(decl, url, schemes)

    @staticmethod
    def _own(decl: dict, url: str, schemes) -> str:
        """Адрес, куда уйдёт ключ: схема из объявленных политикой вызова (`schemes`; не https загрузчик пускает только к
        литералу петли) и ровно объявленный хост с портом; иначе отказ до сети.

        Судится и адрес вызова, и адреса из ответа провайдера (статус, результат): хост, который назвал сам ответ,
        ключа не получает. В фактах только хост: в query бывают токены, в имени пользователя — пароль.
        """
        declared = urlparse(str(decl.get("url") or "").split("{", 1)[0]).netloc
        parsed = urlparse(str(url))
        if parsed.scheme not in schemes or parsed.netloc != declared:
            raise ProviderError("DOWNLOAD_FORBIDDEN",
                                facts={"host": parsed.netloc.rpartition("@")[2], "allowed": declared})
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
        значило бы угадывать. Путь объявлен провайдером в декларации; пустой путь — поле не объявлено, значения нет.
        """
        if not str(path or "").strip():
            return None
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
        decl = self._decl(request.provider)
        # Байты кладутся в тело запроса — без файла его не собрать. Обмен ПУТЯМИ (раннер) исключение:
        # там исходника может не быть вовсе (озвучка создаёт из текста), и решает это вид ресурса.
        if request.source is None and str(decl.get("send_mode") or "") != "paths":
            raise ProviderError(
                "CONTENT_REJECTED", "Отправлять провайдеру нечего: не передан исходный файл.",
                reason="Для этого вида ресурса input — путь к файлу внутри рабочей области.")
        if not request.api_key:
            raise ProviderError(
                "PROVIDER_KEY_MISSING", f"У провайдера '{request.provider}' нет ключа доступа.",
                reason="Ключ вносит владелец: python scripts/set_provider_key.py.")
        self._api_key = request.api_key
        kind = str(decl.get("response") or "binary")
        answer = self._call(decl, request, self._receiver(request) if kind == "binary" else None)
        meta = {"engine": "http", "provider": request.provider,
                "compute": {"device": "remote", "why": f"вызов {decl.get('url')}"}}
        if kind == "binary":
            return MediaOutcome(files=[request.target], meta={**meta, "sync": True})
        if kind == "files":
            # Файл уже на диске: раннер видит ту же рабочую область и пишет прямо в место,
            # назначенное сервером. Считать здесь нечего — только убедиться, что он там.
            return MediaOutcome(files=[self._landed(answer, request)],
                                meta={**meta, "sync": True,
                                      "compute": answer.get("compute") or meta["compute"]})
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

    def _call(self, decl: dict, request: MediaRequest, sink=None):
        """POST провайдеру дверью загрузчика (`Fetcher.call`): политика — `_policy`, адрес — `_url` её схемами, ключ — в
        объявленной форме, тело — `_body`. Ответ — JSON в память, со стоком — бинарное тело в загрузчик результатов.
        Отказ провайдера — его причиной (`_refusal`), отказы двери — кодами политики: не ушёл, не успел, не дочитан."""
        slot, policy = self._policy(request)
        url = self._url(decl, request.params, policy.schemes)
        declared = self.registry.need("online", "http", request.provider, "url", kind=ProviderUrl)
        fetcher = addressed(policy, slot, declared, transport=self.transport, resolve=self.resolve, clock=self.clock,
                            sleep=self.sleep, rng=self._rng)
        return fetcher.call(url, self._headers(decl, request.api_key), method="POST", payload=self._body(decl, request),
                            sink=sink, refused=self._refusal)

    def _body(self, decl: dict, request: MediaRequest) -> dict:
        """Тело запроса: файл + поля строки. Форма отправки — свойство провайдера, не наше."""
        source = request.source
        if source is None:
            raise ProviderError(
                "VALIDATION_ERROR", "Этот вид ресурса перерабатывает готовый файл, а его нет.",
                reason="В input нужен путь к существующему файлу внутри рабочей области.")
        fields = self._fields(decl, request)
        field = str(decl.get("file_field") or "file")
        mode = str(decl.get("send_mode") or "multipart")
        if mode == "paths":
            return {"json": self._paths_body(request, fields)}
        if mode != "json_base64":
            return {"files": {field: (source.name, source.read_bytes())},
                    "data": {k: v for k, v in fields.items() if not isinstance(v, (dict, list))}}
        # Провайдер принимает не multipart, а данные прямо в JSON. Base64 раздувает файл на треть,
        # поэтому предел объявлен: кадр 4К иначе уедет запросом на десятки мегабайт.
        limit_mb = float(decl.get("max_inline_mb") or 0)
        size_mb = source.stat().st_size / 1e6
        if limit_mb and size_mb > limit_mb:
            raise ProviderError(
                "CONTENT_REJECTED",
                f"Файл {round(size_mb)} МБ больше предела встроенной отправки ({round(limit_mb)} МБ).",
                reason="Этот провайдер принимает картинку внутри запроса, и большой файл он "
                       "отклонит сам. Уменьши кадр или подними max_inline_mb в "
                       "config/providers.yaml → online.http.")
        data = base64.b64encode(source.read_bytes()).decode()
        suffix = source.suffix.lstrip(".") or "png"
        self._put(fields, field, f"data:image/{suffix};base64,{data}")
        return {"json": fields}

    @staticmethod
    def _paths_body(request: MediaRequest, fields: dict) -> dict:
        """Задача раннеру: ПУТИ, а не байты — он видит ту же рабочую область.

        Пути идут относительными: тогда раннеру не нужно, чтобы рабочая область была смонтирована
        по тому же абсолютному пути, и он разрешает их собственным containment — доверия к
        вызывающему у него не больше, чем у сервера к клиенту.
        """
        root = request.workspace
        if root is None:
            raise ProviderError(
                "INTERNAL_ERROR", "Раннеру не с чем сопоставить пути: не передана рабочая область.",
                reason="MediaRequest.workspace заполняет вызывающий инструмент — это проводка, "
                       "а не данные канала.")
        return {"kind": request.resource_type, "input": request.input,
                "source": str(request.source.relative_to(root)) if request.source else "",
                "target": str(request.target.relative_to(root)),
                # Строка канала уходит целиком: какие рычаги у модели — знает модель, и
                # перечислять их здесь значило бы держать вторую копию списка столбцов.
                "params": {**request.params, **fields}}

    @staticmethod
    def _landed(answer: dict, request: MediaRequest) -> Path:
        """Файл, который назначил СЕРВЕР, — и никакой другой.

        Путь из ответа берётся не на веру: раннер отчитывается о своём, а хозяин раскладки —
        сервер. Разошлись — это отказ, а не «успех куда-то».
        """
        reported = [f for f in (answer.get("files") or []) if isinstance(f, str)]
        root = request.workspace
        same = [f for f in reported
                if not root or (root / f).resolve() == request.target.resolve()]
        if not request.target.is_file():
            raise ProviderError(
                "PROVIDER_FAILED", f"Раннер ответил успехом, но файла нет: {request.target.name}",
                reason="Он пишет результат сам, в место, назначенное сервером. Пустое место при "
                       "успешном ответе значит, что раннер и сервер видят РАЗНУЮ рабочую область: "
                       "проверь монтирование workspace (media_runner(action='status')).")
        if reported and not same:
            raise ProviderError(
                "PROVIDER_FAILED", f"Раннер записал файл не туда: {', '.join(reported)}",
                reason="Раскладкой владеет сервер, а не раннер. Путь из ответа не совпал с "
                       "назначенным — результат не принимается.")
        return request.target

    def _refusal(self, response) -> ProviderError:
        """Ответ ≥400 → отказ причиной провайдера: форма сервера у объявленного адресата (`_named_refusal`), иначе — общий
        переводчик отказа провайдера."""
        return self._named_refusal(response) or provider_refusal(response, next(iter(self._decl_cache), ""), "вызов")

    def _named_refusal(self, response) -> ProviderError | None:
        """Отказ, названный кодом реестра самим адресатом — если он объявлен отвечающим формой сервера (`error_detail`).

        Чужой платный сервис говорит своими словами, и его отказ честно приходит как
        PROVIDER_FAILED с цитатой. Раннер — наш же процесс: он отвечает ErrorDetail (код, текст, факты, ошибки
        ввода), и код с фактами едут дальше как есть. Код принимается только если адресат объявлен И код имеет форму
        кода реестра — иначе ответ на порту решал бы, каким кодом отвечает сервер.
        """
        import re

        first: dict = next(iter(self._decl_cache.values()), {})
        if not first.get("error_detail"):
            return None
        try:
            body = response.json()
        except ValueError:  # не JSON в теле (и не UTF): отказ назовёт `_refusal` цитатой ответа
            return None
        code = str(body.get("code") or "") if isinstance(body, dict) else ""
        if not re.fullmatch(r"[A-Z][A-Z_]{2,48}", code):
            return None
        # Ответ приходит от того, кто занял порт, — не обязательно наш раннер: факты и текст режутся так же, как
        # ответ платного провайдера, чтобы чужое доезжало до ИИ цитатой, а не простынёй произвольной длины.
        facts = body.get("facts") if isinstance(body.get("facts"), dict) else {}
        errors = body.get("errors") if isinstance(body.get("errors"), list) else []
        return ProviderError(
            code, str(body.get("message") or "")[:300],
            facts={str(k)[:60]: str(v)[:300] for k, v in list(facts.items())[:20]},
            errors=[e for e in errors if isinstance(e, dict)][:20])

    def _receiver(self, request: MediaRequest):
        """Приёмник бинарного ответа: допустимые типы — объявленные для формата, который просит строка канала (NI-03);
        формат не передан — любой объявленный. Тело — тем же загрузчиком результатов, с его пределом (NI-06), в срок
        вызова, под именем хоста, к которому шёл запрос, и с отказом обрыва от двери вызова (`cut`)."""
        types = self.registry.need("online", "http", request.provider, "content_types", kind=dict)
        asked = str(request.params.get(self._tasks.need("download", "format_column", kind=str)) or "").lower()
        if asked and asked not in types:
            raise ProviderError("SCHEMA_INVALID", facts={
                "details": f"online.http.{request.provider}.content_types: формат «{asked}» не объявлен"})
        accept = {str(t).lower() for f, kinds in types.items() if not asked or f == asked for t in kinds}
        target = Path(request.target)
        fetcher = results(self._tasks.config_file, transport=self.transport, resolve=self.resolve, clock=self.clock)
        return lambda response, host, deadline, cut=None: fetcher.receive(
            response, Files(target.parent), target.name, "result", accept=accept, host=host, deadline=deadline, cut=cut)

    # ═══ Задача ═══

    def poll(self, task_id: str, within: float | None = None) -> dict:
        """Статус задачи. Слова статусов разбирает общий цикл — здесь только запрос; `within` — остаток срока цикла
        ожидания (секунды): опрос с повторами в него укладывается."""
        return self._answer(self._status_url, within)

    def _poll_headers(self, url: str, schemes) -> dict:
        """Ключ к адресу, который назвал ответ провайдера, — только после суда адреса (`_own`) схемами двери опроса."""
        decl: dict = next(iter(self._decl_cache.values()), {})
        self._own(decl, url, schemes)
        return self._headers(decl, self._api_key) if self._api_key else {}

    def fetch(self, answer: dict, target: Path) -> Path:
        """Забрать результат: ссылку — по объявленному пути или полям, файл — общим загрузчиком результатов, без ключа."""
        decl: dict = next(iter(self._decl_cache.values()), {})
        if self._result_url:
            # Очередь fal отдаёт результат отдельным адресом: статус говорит лишь «готово».
            answer = self._answer(self._result_url)
        fetcher = results(self._tasks.config_file, transport=self.transport, resolve=self.resolve)
        url = ""
        if decl.get("result_path"):
            found = self.dig(answer, str(decl["result_path"]))
            url = found if isinstance(found, str) else ""
        for field in (() if url else fetcher.policy.url_fields):
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
        target = Path(target)
        fetcher.fetch(url, Files(target.parent), target.name, "result")
        return target

    def _answer(self, url: str, within: float | None = None) -> dict:
        """JSON статуса или адреса результата с объявленного хоста: ключ — после суда адреса схемами этой двери
        (`_poll_headers`), запрос и тело — дверью загрузчика по `download.answer` декларации задач, хост — из объявленного
        адреса провайдера, срок — `deadline_sec` или остаток вызывающего `within`. Отказ провайдера — его причиной
        (`_refusal`), отказ самой двери — как есть."""
        declared = self.registry.need("online", "http", next(iter(self._decl_cache), ""), "url", kind=ProviderUrl)
        fetcher = answers(self._tasks, RESULT_ANSWER, declared, transport=self.transport, resolve=self.resolve,
                          clock=self.clock, sleep=self.sleep)
        headers = self._poll_headers(url, fetcher.policy.schemes)
        return fetcher.answer(url, headers, refused=self._refusal, within=within)

    @functools.cached_property
    def _tasks(self) -> Declaration:
        return Declaration(Path(self.registry.config_file).with_name("media_tasks.yaml"), ProviderError, "ожидания",
                           "Заведи config/media_tasks.yaml — правила ожидания и загрузки объявлены там.")

    @functools.cached_property
    def _providers(self) -> Declaration:
        return Declaration(self.registry.config_file, ProviderError, "провайдеров",
                           "Заведи config/providers.yaml — как вызывать провайдера, объявлено там.")
