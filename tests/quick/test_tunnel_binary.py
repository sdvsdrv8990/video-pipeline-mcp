#!/usr/bin/env python3
"""tests/quick/test_tunnel_binary.py — бинарь клиентского конца туннеля ставится дверью загрузки (`Fetcher`) по
декларации `config/tunnel_diagnostics.yaml → бинари.cloudflared.загрузка`.

Сеть подменена швами двери (транспорт, разрешение имён, сон), корень — временный каталог: истина о загрузке — байты и
права файла на месте каталога, об отказе — код реестра и текст для терминала. Прежде установщик качал curl/wget мимо двери
одной фразой «не удалось» на три разные причины.
"""
import contextlib
import os
import socket
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.contracts import ContractError
from core.declaration import declarations_root
from core.declaration_schemas import REACTIONS
from core.reactions.reactions import cli_text
from scripts.fetch_tunnel_binary import fetch

_checks = 0
_fails: list[str] = []
BODY = b"\x7fELF" + b"\x00" * 4096            # заголовок исполняемого и тело: истина — что легло на место
PUBLIC = "140.82.112.3"                        # адрес вне частных сетей: дверь судит разрешённый IP


def check(label, cond, detail=""):
    global _checks
    _checks += 1
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        _fails.append(label)


def resolve(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, port))]


def answering(status: int, asked: list[str]):
    def handle(request: httpx.Request) -> httpx.Response:
        # Дверь соединяется с судимым IP, имя хоста едет заголовком Host: адрес — так, как его видит источник.
        asked.append(f"{request.url.scheme}://{request.headers.get('host')}{request.url.path}")
        if status != 200:
            return httpx.Response(status, content=b"")
        return httpx.Response(200, content=BODY, headers={"content-length": str(len(BODY)), "etag": '"v1"'})
    return handle


@contextlib.contextmanager
def root():
    with tempfile.TemporaryDirectory(prefix="vpm-tunnel-bin-") as tmp:
        yield Path(tmp)


config = declarations_root()

print("== 1. загрузка дверью: адрес по системе и архитектуре, файл на месте каталога и исполняем ==")
with root() as where:
    asked: list[str] = []
    path = fetch(config, where, "Linux", "x86_64", transport=httpx.MockTransport(answering(200, asked)),
                 resolve=resolve, sleep=lambda _s: None)
    check("адрес собран по декларации: система linux, архитектура amd64",
          asked == ["https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"], asked)
    check("файл лёг в путь каталога `в_дереве`", path == (where / "bin" / "tunnel").resolve(), path)
    check("байты те, что отдал источник", path.read_bytes() == BODY)
    check("файл исполняем", os.access(path, os.X_OK))
    check("остатка рядом не осталось", not list(path.parent.glob("*.part")), list(path.parent.iterdir()))

print("== 2. архитектура без готового бинаря — отказ с фактами, в сеть не ходили ==")
with root() as where:
    asked = []
    try:
        fetch(config, where, "Linux", "riscv64", transport=httpx.MockTransport(answering(200, asked)),
              resolve=resolve, sleep=lambda _s: None)
        check("отказ TUNNEL_BINARY_UNAVAILABLE", False, "загрузка прошла")
    except ContractError as refused:
        check("отказ TUNNEL_BINARY_UNAVAILABLE с системой и архитектурой",
              refused.code == "TUNNEL_BINARY_UNAVAILABLE" and refused.facts == {"system": "Linux", "arch": "riscv64"},
              (refused.code, refused.facts))
        said = cli_text(refused, config / REACTIONS)
        check("терминал получает текст реестра с фактами", "riscv64" in said and "готового бинаря туннеля нет" in said, said)
    check("запросов к источнику не было", asked == [], asked)

print("== 3. источник отказал — код реестра с хостом и ответом, файла на месте нет ==")
with root() as where:
    asked = []
    try:
        fetch(config, where, "Linux", "aarch64", transport=httpx.MockTransport(answering(404, asked)),
              resolve=resolve, sleep=lambda _s: None)
        check("отказ TUNNEL_BINARY_SOURCE_FAILED", False, "загрузка прошла")
    except ContractError as refused:
        check("отказ TUNNEL_BINARY_SOURCE_FAILED с хостом источника",
              refused.code == "TUNNEL_BINARY_SOURCE_FAILED" and refused.facts.get("host") == "github.com",
              (refused.code, refused.facts))
        said = cli_text(refused, config / REACTIONS)
        check("терминал называет хост и ответ", "github.com" in said and "404" in said, said)
    check("на месте каталога файла нет", not (where / "bin" / "tunnel").exists())

print(f"\nРЕЗУЛЬТАТ: {_checks - len(_fails)}/{_checks} прошло")
if _fails:
    for label in _fails:
        print(f"  - {label}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ ✅")
