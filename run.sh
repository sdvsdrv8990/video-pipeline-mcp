#!/bin/bash
# run.sh — запуск MCP-сервера видеопайплайна

set -e

# Проверка что .venv существует
if [ ! -d ".venv" ]; then
    echo "ОШИБКА: .venv не найден. Сначала выполните ./install.sh"
    exit 1
fi

# Активация окружения
source .venv/bin/activate

# Запуск сервера + туннеля ОДНОЙ командой (D11).
# --tunnel поднимает cloudflared вместе с сервером; сервер слушает 127.0.0.1.
# Отключить туннель (только локально): ./run.sh --no-tunnel
if [ "$1" = "--no-tunnel" ]; then
    python3 server.py
else
    python3 server.py --tunnel
fi
