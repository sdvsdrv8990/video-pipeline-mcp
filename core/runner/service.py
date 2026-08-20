"""
core/runner/service.py — раннер: тот же инференс, но в отдельном процессе.

## Назначение
Транспорт вокруг уже работающих локальных адаптеров: `/run` принимает задачу от сервера, отдаёт
её адаптеру через тот же пул моделей и отвечает путём к готовому файлу. Своей логики инференса
здесь нет; между запросами раннер не делает ничего, кроме удержания поднятых моделей.

## Граница доверия
Слушает только петлю и никогда не выставляется за туннель. Токен приходит переменной окружения от
того, кто его поднял, и сверяется постоянным по времени сравнением. Пути из запроса проходят тот
же containment и тот же allowlist типов, что и у сервера: раннер доверяет вызывающему не больше,
чем сервер клиенту, — даже зная, что вызывающий на той же машине.
"""

import asyncio
import secrets
import time
from pathlib import Path

from aiohttp import web

from core.paths import PathEscapeError, SecretAccessError, safe_resolve
from core.providers import AdapterRegistry, MediaRequest, ProviderError
from core.write_policy import WritePolicy, WritePolicyError


class RunnerService:
    """Приложение раннера: `/run` (исполнить) и `/health` (чем занят)."""

    def __init__(self, config_file: str | Path, project_root: str | Path,
                 workspace: str | Path, token: str = ""):
        self.registry = AdapterRegistry(config_file, project_root)
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

    @staticmethod
    def _refuse(code: str, message: str, reason: str = "", status: int = 400) -> web.Response:
        """Отказ — кодом реестра реакций, а не текстом: сервер передаст его клиенту как есть."""
        return web.json_response({"code": code, "message": message, "reason": reason}, status=status)

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
        })

    # ═══ Исполнение ═══

    async def run(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            return self._refuse(
                "AUTH_FAILED", "Раннер не принял токен.",
                f"Токен выдаёт тот, кто поднял раннер, и присылает его заголовком "
                f"{self.token_header}. На петле сидит не только сервер — без токена раннер "
                "исполнял бы что угодно по просьбе любого процесса пользователя.", status=401)
        try:
            body = await request.json()
        except Exception:  # тело не разобрать
            return self._refuse("VALIDATION_ERROR", "Тело запроса не разобрать как JSON.",
                                "Раннер ждёт {kind, input, source, target, params}.")
        if not isinstance(body, dict):
            return self._refuse("VALIDATION_ERROR", "Тело запроса — не объект.", "")

        kind = str(body.get("kind") or "")
        provider = self.engine_for(kind)
        if not provider:
            return self._refuse(
                "PROVIDER_NOT_CONFIGURED", f"Раннер не знает, чем исполнять '{kind}'.",
                "Объяви вид ресурса в config/providers.yaml → local.runner.by_resource. "
                f"Сейчас объявлены: {', '.join(sorted(self.rules.get('by_resource') or {})) or '(никто)'}.")

        try:
            target = self._place(str(body.get("target") or ""))
            source = self._locate(str(body.get("source") or ""))
        except (PathEscapeError, SecretAccessError) as e:
            return self._refuse("PATH_ESCAPE", f"Путь вне рабочей области раннера: {e}",
                                "Раннер разрешает пути своим containment — присланному пути "
                                "он не верит, как и сервер не верит клиенту.", status=403)
        except WritePolicyError as e:
            return self._refuse(e.code, e.message, e.reason, status=403)
        except FileNotFoundError as e:
            return self._refuse("FILE_NOT_FOUND", f"Исходного файла нет: {e}",
                                "Раннер и сервер видят РАЗНУЮ рабочую область — проверь "
                                "монтирование workspace.", status=404)

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
        except ProviderError as e:
            self.last_error = f"{e.code}: {e.message}"
            return self._refuse(e.code, e.message, e.reason, status=502)
        except Exception as e:  # чужой код инференса
            self.last_error = f"LOCAL_INFERENCE_FAILED: {e}"
            return self._refuse(
                "LOCAL_INFERENCE_FAILED", f"Расчёт оборвался: {e}",
                "Это упал раннер, а не сервер — в том и смысл отдельного процесса. Подробности "
                "в его журнале; media_runner(action='status') покажет, жив ли он.", status=502)

        self.calls += 1
        return web.json_response({
            "files": [str(Path(p).resolve().relative_to(self.workspace)) for p in outcome.files],
            "compute": outcome.meta.get("compute") or {},
            "engine": provider, "task_id": outcome.task_id,
        })

    # ═══ Пути ═══

    def _place(self, rel: str) -> Path:
        """Куда писать. Тип файла — тот же allowlist, что у сервера (S2), а не «раз просят»."""
        if not rel:
            raise PathEscapeError("не указан путь результата")
        self.write_policy.check(rel)
        target = safe_resolve(rel, self.workspace)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _locate(self, rel: str) -> Path | None:
        """Откуда читать. Пусто — законно: озвучка и картинка создают из текста, а не из файла."""
        if not rel:
            return None
        source = safe_resolve(rel, self.workspace)
        if not source.is_file():
            raise FileNotFoundError(rel)
        return source
