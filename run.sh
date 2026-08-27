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

# Запуск сервера + туннеля ОДНОЙ командой.
# --tunnel поднимает cloudflared вместе с сервером; сервер слушает 127.0.0.1.
#   ./run.sh --no-tunnel   только локально, без публичного URL
#   ./run.sh --no-auth     цикл разработки: коннектор подцепляется без ключа
TUNNEL="--tunnel"
for arg in "$@"; do
    case "$arg" in
        --no-tunnel) TUNNEL="" ;;
        --no-auth)   export MCP_ALLOW_NO_AUTH=1 ;;
    esac
done

# Пара «без ключа + публичный URL» открывает сервер каждому, кто узнает адрес: периметр
# (Origin/Host) и файрвол остаются, идентификации нет. Сам сервер про туннель не знает.
if [ -n "$MCP_ALLOW_NO_AUTH" ] && [ -n "$TUNNEL" ]; then
    echo "⚠ БЕЗ КЛЮЧА И С ТУННЕЛЕМ — публичный адрес пустит любого, кто его узнает."
fi

python3 server.py $TUNNEL
