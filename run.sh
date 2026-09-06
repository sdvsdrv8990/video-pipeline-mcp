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
STOP=""
for arg in "$@"; do
    case "$arg" in
        --no-tunnel) TUNNEL="" ;;
        --no-auth)   export MCP_ALLOW_NO_AUTH=1 ;;
        --stop)      STOP="1" ;;
    esac
done

PORT="${VPM_PORT:-8080}"

# Аварийная остановка. Цели ищутся ТРЕМЯ независимыми уликами, потому что одна отказывает:
# порт молчит при упавшем сервере, имя не находит переименованное, id туннеля пуст в quick-режиме.
# Вердикт выносится по СОСТОЯНИЮ (порт свободен, процессов нет), а не по факту отправки сигнала.
targets() {
    local tid
    tid=$(grep -oP 'tunnel_id:\s*"\K[^"]+' config/tunnel.yaml 2>/dev/null || true)
    {
        ss -ltnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+'
        pgrep -f "python3? .*server\.py" 2>/dev/null || true
        pgrep -f "cloudflared.*${tid:-127\.0\.0\.1:$PORT}" 2>/dev/null || true
        pgrep -f "cloudflared.*127\.0\.0\.1:$PORT" 2>/dev/null || true
    } | sort -un | grep -v "^$$\$"
}

stop_all() {
    local pids sig
    for sig in TERM KILL; do
        pids=$(targets)
        [ -z "$pids" ] && break
        echo "── остановка сигналом $sig: $(echo $pids | tr '\n' ' ')"
        kill -"$sig" $pids 2>/dev/null || true
        sleep 2
    done
    pids=$(targets)
    if [ -n "$pids" ]; then
        echo "✗ живы после KILL: $(echo $pids | tr '\n' ' ') — проверь права"
        return 1
    fi
    if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
        echo "✗ порт $PORT всё ещё занят — остановка не полна"
        return 1
    fi
    echo "✓ остановлено: процессов нет, порт $PORT свободен"
}

if [ -n "$STOP" ]; then
    stop_all
    exit $?
fi

# Пара «без ключа + публичный URL» открывает сервер каждому, кто узнает адрес: периметр
# (Origin/Host) и файрвол остаются, идентификации нет. Сам сервер про туннель не знает.
if [ -n "$MCP_ALLOW_NO_AUTH" ] && [ -n "$TUNNEL" ]; then
    echo "⚠ БЕЗ КЛЮЧА И С ТУННЕЛЕМ — публичный адрес пустит любого, кто его узнает."
fi

python3 server.py $TUNNEL
