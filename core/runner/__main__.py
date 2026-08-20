"""
core/runner/__main__.py — точка входа раннера: `python -m core.runner`.

Адрес по умолчанию не выдуман здесь, а взят из объявления (`online.http.<провайдер>.url`) — того
же, по которому сервер будет стучаться. Второй копии порта в проекте нет.
Токен приходит переменной окружения от того, кто поднял раннер: в списке процессов его быть не
должно, а `--token` в командной строке видит любой `ps`.
"""

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.runner.service import RunnerService  # noqa: E402 — импорт после правки sys.path: раннер запускается файлом

TOKEN_ENV = "MCP_RUNNER_TOKEN"


def main() -> int:
    parser = argparse.ArgumentParser(description="Раннер локальных моделей (инференс вне сервера)")
    parser.add_argument("--config", default=str(ROOT / "config" / "providers.yaml"))
    parser.add_argument("--workspace", default=str(ROOT / "workspace"))
    parser.add_argument("--host", default="", help="пусто → хост из объявленного адреса")
    parser.add_argument("--port", type=int, default=0, help="0 → порт из объявленного адреса")
    parser.add_argument("--container", action="store_true",
                        help="раннер внутри контейнера: слушать все его интерфейсы")
    args = parser.parse_args()

    service = RunnerService(args.config, ROOT, args.workspace, token=os.environ.get(TOKEN_ENV, ""))
    if not service.token:
        print(f"Раннер не поднят: нет токена. Он приходит переменной {TOKEN_ENV} от того, кто "
              "запускает раннер (media_runner(action='start') делает это сам). Без токена раннер "
              "исполнял бы задачи любого процесса пользователя.", file=sys.stderr)
        return 2

    declared = urlparse(str(service.endpoint.get("url") or ""))
    # Внутри контейнера петля бесполезна: опубликованный порт приходит на интерфейс контейнера, а
    # не на его loopback. Границу там держит САМА публикация — супервизор публикует порт только на
    # петлю хоста (`127.0.0.1:порт:порт`), и наружу машины раннер по-прежнему не выходит.
    host = args.host or ("0.0.0.0" if args.container else (declared.hostname or "127.0.0.1"))  # nosec B104 — граница здесь у публикации порта (см. комментарий выше), не у bind
    port = args.port or declared.port or 8770
    if not args.container and host not in ("127.0.0.1", "::1", "localhost"):
        # Раннер исполняет модели по запросу и пишет файлы. За пределы машины он не выставляется
        # никогда — ни за туннель, ни в локальную сеть; это отказ, а не предупреждение.
        print(f"Раннер не поднят: адрес {host} не петлевой. Раннер слушает только 127.0.0.1 — "
              "внутри контейнера это снимается ключом --container, и границей становится то, на "
              "какой адрес опубликован порт.", file=sys.stderr)
        return 2

    print(f"Раннер локальных моделей: http://{host}:{port} (рабочая область {service.workspace})",
          flush=True)
    web.run_app(service.app(), host=host, port=port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
