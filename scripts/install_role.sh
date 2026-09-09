#!/bin/bash
# install_role.sh — выбрать роль машины (local/server/both) и записать config/install.yaml.
#
# Работает ДО создания .venv (голый bash). Зовётся из install.sh (через source, чтобы отдать ROLE).
# Отдельный файл — чтобы выбор роли прогонялся без установки: VPM_INSTALL_ROLE=<роль> задаёт роль
# неинтерактивно (тест и CI), пустая переменная поднимает меню. Помогает владельцу, не судит — в гейте
# не участвует.

set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROLE="${VPM_INSTALL_ROLE:-}"

if [ -z "$ROLE" ]; then
    echo "Режим установки этой машины:"
    PS3="Выбери номер: "
    select _opt in "local — сервер MCP + клиентский конец туннеля (рабочая машина)" \
                   "server — только серверный конец туннеля (VPS, рядом с VPN)" \
                   "both — и то, и другое на одной машине"; do
        case "$REPLY" in
            1) ROLE=local;  break ;;
            2) ROLE=server; break ;;
            3) ROLE=both;   break ;;
            *) echo "Нужно 1, 2 или 3" ;;
        esac
    done
fi

case "$ROLE" in
    local|server|both) ;;
    *) echo "✗ неизвестная роль '$ROLE' — допустимо local|server|both" >&2; exit 1 ;;
esac

printf 'role: %s\n' "$ROLE" > "$ROOT/config/install.yaml"
echo "Режим: $ROLE → config/install.yaml"
