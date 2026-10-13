"""scripts/fetch_tunnel_binary.py — бинарь клиентского конца туннеля дверью загрузки (`core.providers.download.Fetcher`).

Какой бинарь, адрес, имена системы и архитектуры у поставщика и политика загрузки — раздел `config/tunnel_diagnostics.yaml
→ загрузка_клиента` (слот TUNNEL_BINARY_FETCH); путь в дереве — `в_дереве` этого бинаря в `бинари`, его читают туннель и
диагностика. Обрыв оставляет остаток владельца установки, повтор докачивает. Отказ — код реестра текстом для терминала
(`cli_text`) и код возврата 1. Зовёт install.sh из .venv установщика.
"""
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.contracts import ContractError  # noqa: E402 — корень в пути ставится строкой выше
from core.declaration import Declaration, declarations_root  # noqa: E402
from core.declaration_schemas import REACTIONS, TUNNEL_BINARY_FETCH  # noqa: E402
from core.paths import Files  # noqa: E402
from core.providers.download import Fetcher  # noqa: E402
from core.reactions.reactions import cli_text  # noqa: E402

OWNER = "install"   # владелец остатка: повтор установки докачивает его же


def fetch(config: Path, root: Path, system: str, machine: str, **seams) -> Path:
    """Скачать бинарь для `system`/`machine` (имена `platform`) в путь каталога от корня `root`; итог — путь
    исполняемого файла. `seams` — швы двери (транспорт, разрешение имён, сон): их подменяет тест."""
    door = Declaration(config / TUNNEL_BINARY_FETCH.file, ContractError, "«tunnel_diagnostics»",
                       "Заведи config/tunnel_diagnostics.yaml — по нему ставится и ищется бинарь туннеля.")
    plan = door.section(*TUNNEL_BINARY_FETCH.section, schema=TUNNEL_BINARY_FETCH.schema)
    target = door.need("бинари", plan.binary, "в_дереве", kind=str)
    named_system, named_arch = plan.systems.get(system), plan.arches.get(machine)
    if named_system is None or named_arch is None:
        raise ContractError("TUNNEL_BINARY_UNAVAILABLE", facts={"system": system, "arch": machine})
    got = Fetcher(plan.fetch, slot=TUNNEL_BINARY_FETCH, **seams).fetch(
        plan.url.format(system=named_system, arch=named_arch), Files(root), target, OWNER)
    path = Path(got["path"])
    path.chmod(path.stat().st_mode | 0o111)    # исполняемый для всех, кто его читает: бинарь не секрет
    return path


def main() -> int:
    config = declarations_root()
    try:
        path = fetch(config, ROOT, platform.system(), platform.machine())
    except ContractError as refused:
        print(cli_text(refused, config / REACTIONS), file=sys.stderr)
        return 1
    print(f"✓ бинарь туннеля: {Files(ROOT).name(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
