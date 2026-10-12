"""Приспособление «двойник сети» (род C): швы двери у классов, которые ходят в сеть, — на двойник по сценарию сети из файла
`NET_DOUBLE`; журнал — `MCP_LOGS/JOURNAL`; запрос мимо двери — растяжка. Нет среды — отказ старта."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))      # корень цели: core и tests.harness

from tests.harness.net_double import JOURNAL, install

install(Path(os.environ["NET_DOUBLE"]), Path(os.environ["MCP_LOGS"]) / JOURNAL)
