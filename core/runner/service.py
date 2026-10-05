"""core/runner/service.py — раннер: тот же инференс, но в отдельном процессе.

Своей логики инференса здесь нет — это транспорт вокруг уже работающих локальных адаптеров, и между
запросами раннер не делает ничего, кроме удержания поднятых моделей. Слушает только петлю и никогда
не выставляется за туннель. Токен приходит переменной окружения от того, кто его поднял, и
сверяется постоянным по времени сравнением. Пути из запроса проходят тот же containment и тот же
список разрешённых типов, что у сервера: раннер доверяет вызывающему не больше, чем сервер клиенту,
— даже зная, что вызывающий на этой же машине.
"""

import asyncio
import logging
import secrets
import time
import uuid
from pathlib import Path

from aiohttp import web

from core.contracts import ContractError
from core.paths import Files, PathRefused
from core.providers import AdapterRegistry, MediaRequest
from core.reactions import Reactions
from core.write_policy import WritePolicy, WritePolicyError

log = logging.getLogger(__name__)


class RunnerService:
    """Приложение раннера: `/run` (исполнить) и `/health` (чем занят)."""

    def __init__(self, config_file: str | Path, project_root: str | Path,
                 workspace: str | Path, token: str = ""):
        self.registry = AdapterRegistry(config_file, project_root)
        self.reactions = Reactions(Path(config_file).parent / "server_reactions.yaml")
        self.write_policy = WritePolicy(Path(config_file).parent)
        self.workspace = Path(workspace).resolve()
        self.token = token
        self.started = time.time()
        self.calls = 0
        self.last_error = ""

    # ═══ Декларация ═══

    @property
    def rules(self) -> dict:
        return ((self.registry.config.get("local") or {}).get("runner")) or {}

    @property
    def endpoint(self) -> dict:
        """Свой же блок в `online.http`: адрес, порт и имя заголовка объявлены там один раз."""
        name = str(self.rules.get("provider") or "")
        return (((self.registry.config.get("online") or {}).get("http")) or {}).get(name) or {}

    @property
    def token_header(self) -> str:
        scheme = str(self.endpoint.get("auth") or "")
        return scheme.split(":", 1)[1] if scheme.startswith("header:") else "Authorization"

    def engine_for(self, kind: str) -> str:
        """Каким локальным провайдером исполнять этот вид ресурса (объявлено сервером)."""
        return str((self.rules.get("by_resource") or {}).get(kind) or "")

    # ═══ Приложение ═══

    def app(self) -> web.Application:
        app = web.Application()
        app.add_routes([web.post("/run", self.run), web.get("/health", self.health)])
        return app

    def authorized(self, request: web.Request) -> bool:
        got = request.headers.get(self.token_header, "")
        return bool(self.token) and secrets.compare_digest(got, self.token)

    # ═══ Здоровье ═══

    async def health(self, request: web.Request) -> web.Response:
        """Живость — без токена, содержимое — с токеном.

        «Жив ли процесс» спрашивает и супервизор, и HEALTHCHECK контейнера, и ответ на это не
        секрет. А вот ЧТО он держит поднятым — уже сведения о работе канала, и они под токеном.
        """
        alive = {"ok": True, "uptime_sec": round(time.time() - self.started, 1)}
        if not self.authorized(request):
            return web.json_response(alive)
        return web.json_response({
            **alive, "calls": self.calls, "last_error": self.last_error,
            "workspace": str(self.workspace),
            "pool": self.registry.pool.stats(),
            "engines": self.rules.get("by_resource") or {},
            # Готовность каждой модели описи по составу на диске — только по длине: без отпечатка и сети.
            "models": self.registry.readiness(),
        })

    # ═══ Исполнение ═══

    async def run(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            return self._answer("AUTH_FAILED", 401, facts={"header": self.token_header})
        try:
            body = await request.json()
        except Exception:  # тело не разобрать
            return self._answer("VALIDATION_ERROR", 400, errors=[{"field": "body", "rule": "json"}])
        if not isinstance(body, dict):
            return self._answer("VALIDATION_ERROR", 400, errors=[{"field": "body", "rule": "object"}])

        kind = str(body.get("kind") or "")
        provider = self.engine_for(kind)
        if not provider:
            return self._answer("PROVIDER_NOT_CONFIGURED", 400, facts={
                "kind": kind, "declared": ", ".join(sorted(self.rules.get("by_resource") or {}))})

        try:
            target = self._place(str(body.get("target") or ""))
            source = self._locate(str(body.get("source") or ""))
        except (PathRefused, WritePolicyError) as e:  # дверь пути и политика записи — своим кодом: секрет ≠ выход
            return self._answer(e.code, 403, facts=e.facts, errors=e.errors, message=e.message)
        except ContractError as e:
            return self._answer(e.code, 400, facts=e.facts, errors=e.errors, message=e.message)
        except FileNotFoundError as e:
            return self._answer("FILE_NOT_FOUND", 404, facts={"path": str(e)})

        params = body.get("params")
        media = MediaRequest(
            input=str(body.get("input") or ""), params=params if isinstance(params, dict) else {},
            target=target, models_dir=self.registry.models_dir, source=source,
            provider=provider, resource_type=kind, workspace=self.workspace)
        try:
            adapter = self.registry.load(provider, kind)
            # Инференс блокирует секунды и минуты. В отдельном потоке — чтобы `/health` отвечал,
            # пока идёт расчёт: иначе «занят» было бы неотличимо от «умер».
            outcome = await asyncio.to_thread(adapter.generate, media)
        except ContractError as e:  # отказ ядра (поставщик, дверь пути) — своим кодом, а не «расчёт оборвался»
            self.last_error = e.code
            return self._answer(e.code, 502, facts=e.facts, errors=e.errors, message=e.message)
        except Exception as e:  # чужой код инференса: причина с трассой — в журнал раннера, клиенту — номер события
            event = uuid.uuid4().hex[:8]
            log.error("раннер: расчёт %s оборвался, событие %s", provider, event, exc_info=e)
            self.last_error = f"LOCAL_INFERENCE_FAILED (событие {event})"
            return self._answer("LOCAL_INFERENCE_FAILED", 502, facts={"provider": provider, "event": event})

        self.calls += 1
        return web.json_response({
            "files": [str(Path(p).resolve().relative_to(self.workspace)) for p in outcome.files],
            "compute": outcome.meta.get("compute") or {},
            "engine": provider, "task_id": outcome.task_id,
        })

    def _answer(self, code: str, status: int, *, facts: dict | None = None, errors: list[dict] | None = None,
                message: str = "") -> web.Response:
        """Отказ телом ErrorDetail — тем же сборщиком реестра, что и ответ MCP; HTTP-статус — у места."""
        detail = self.reactions.get_error(code, message, facts=facts, errors=errors)
        return web.json_response(detail.model_dump(), status=status)

    # ═══ Пути ═══

    def _place(self, rel: str) -> Path:
        """Куда писать. Тип файла — тот же allowlist, что у сервера, а не «раз просят»."""
        if not rel:
            raise ContractError("VALIDATION_ERROR", errors=[{"field": "target", "rule": "required"}])
        self.write_policy.check(rel)
        target = Files(self.workspace).place(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _locate(self, rel: str) -> Path | None:
        """Откуда читать. Пусто — законно: озвучка и картинка создают из текста, а не из файла."""
        if not rel:
            return None
        source = Files(self.workspace).place(rel)
        if not source.is_file():
            raise FileNotFoundError(rel)
        return source
