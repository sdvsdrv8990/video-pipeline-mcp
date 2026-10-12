"""Приспособление «в интерпретаторе сервера нет cryptography»: пакет закрыт для импорта, как в среде без него."""
import sys

sys.modules["cryptography"] = None
