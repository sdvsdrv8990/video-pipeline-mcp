"""Приспособление «место класса ресурса занято»: вызов инструмента, для которого шаг сценария положил флаг
`<рабочая область>/.hold/<инструмент>`, отмечается файлом `<инструмент>.held` и держит своё место класса, пока флаг не
снят. Без флага инструмент исполняется как есть; держатель живёт не дольше процесса сервера сценария.
"""
import os
import time
from pathlib import Path

from core.engine import engine

_register = engine.Engine.register


def _held(name: str, handler):
    async def wrapper(**params):
        flag = Path(os.environ["MCP_WORKSPACE"]) / ".hold" / name
        if flag.exists():
            (flag.parent / f"{name}.held").touch()
            while flag.exists():
                time.sleep(0.05)
        return await handler(**params)
    return wrapper


def _register_held(self, name, description, input_schema, handler, *args, **kwargs):
    return _register(self, name, description, input_schema, _held(name, handler), *args, **kwargs)


engine.Engine.register = _register_held
