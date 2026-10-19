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

# Запуск по объявлению установки (config/install.yaml): сервер и тот конец туннеля, что объявлен этой машине.
# Сервер слушает 127.0.0.1; публичный адрес даёт только туннель.
#   ./run.sh --no-tunnel   этот запуск без туннеля, объявление не меняется
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

# Аварийная остановка. Цели ищутся НЕСКОЛЬКИМИ независимыми уликами, потому что одна отказывает:
# порт молчит при упавшем сервере, родство теряет сироту упавшего сервера, id туннеля пуст в quick-режиме.
# Вердикт выносится по СОСТОЯНИЮ (порт свободен, процессов нет).
# По имени — только сервер, запущенный этим скриптом из этого дерева: серверы тестов (абсолютный путь, --port) и копий
# других сессий целями не бывают.
targets() {
    local tid srv pid
    tid=$(grep -oP 'tunnel_id:\s*"\K[^"]+' config/tunnel.yaml 2>/dev/null || true)
    srv=$({ ss -ltnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+'
            for pid in $(pgrep -f "^python3? server\.py( --tunnel)?$" 2>/dev/null || true); do
                [ "$(readlink "/proc/$pid/cwd" 2>/dev/null)" = "$PWD" ] && echo "$pid"
            done; } | sort -un)
    {
        echo "$srv"
        # Дети сервера — туннель по родству, а не по имени: бинарь в дереве назван нейтрально (bin/tunnel).
        for pid in $srv; do pgrep -P "$pid" 2>/dev/null || true; done
        pgrep -f "cloudflared.*${tid:-127\.0\.0\.1:$PORT}" 2>/dev/null || true
        pgrep -f "cloudflared.*127\.0\.0\.1:$PORT" 2>/dev/null || true
    } | grep -v "^$" | sort -un | grep -v "^$$\$" || true    # нет целей — пустой ответ, а не провал: set -e оборвал бы stop_all
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

# Что объявлено установщиком в config/install.yaml. Флаги выше — переопределение. Нет файла — рабочая машина (core/deploy).
if ! DECLARED="$(python3 -c 'from core.deploy import install; m,t=install(); print(m,t)')"; then
    echo "✗ config/install.yaml не читается — причина выше. Объявить заново: python3 scripts/installer_tui.py"
    exit 2
fi
MCP_HERE="${DECLARED% *}"
TUNNEL_END="${DECLARED#* }"
ROLE_FIX="  Сервер и туннель Cloudflare здесь — роль Local: VPM_INSTALL_CHOICE=local python3 scripts/installer_tui.py"
# Отказ называет объявление, а не факт машины: пакет сервера в .venv может стоять от прежней установки.
if [ "$MCP_HERE" != "True" ]; then
    echo "✗ не запущено: config/install.yaml объявляет машину без сервера MCP, только конец туннеля «$TUNNEL_END»."
    echo "  Туннель без сервера run.sh не поднимает: запуска концов xray ещё нет (вход через свой сервер — в работе)."
    echo "$ROLE_FIX"
    exit 2
fi
case "$TUNNEL_END" in
    none) TUNNEL="" ;;   # консоль на этой машине: сервер только по петле
    client) ;;
    *)
        # Серверный конец (xray) — не cloudflared: поднять вместо него Cloudflare значило бы молча сменить вход.
        if [ -n "$TUNNEL" ]; then
            echo "✗ не запущено: config/install.yaml объявляет серверный конец туннеля «$TUNNEL_END» (xray), а run.sh"
            echo "  поднимает только туннель Cloudflare. Запуска концов xray ещё нет (вход через свой сервер — в работе)."
            echo "  Сервер без туннеля: ./run.sh --no-tunnel"
            echo "$ROLE_FIX"
            exit 2
        fi
        ;;
esac
LAUNCH="сервер только для этой машины"
[ -n "$TUNNEL" ] && LAUNCH="сервер + туннель Cloudflare"
echo "▶ объявлено: сервер MCP, конец туннеля «$TUNNEL_END»; этот запуск — $LAUNCH"

# Предупреждение «без ключа + туннель» печатает сам сервер: его зовут и напрямую (server.py --tunnel).
python3 server.py $TUNNEL
