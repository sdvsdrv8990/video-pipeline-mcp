"""tests/conftest.py — общие фикстуры прогонов (T0)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Корень репозитория — точка отсчёта для наборов-скриптов и конфигов."""
    return ROOT
