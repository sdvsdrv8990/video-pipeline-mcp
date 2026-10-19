#!/bin/bash
# deploy_tunnel_server.sh — поставить серверный конец туннеля (Xray VLESS+REALITY) на сервер по SSH, без ручных шагов.
#
# Адрес и логин — флагами или VPM_TUNNEL_HOST/VPM_TUNNEL_USER, без них в терминале спрашивает сам. Пароль, если ключа
# нет, спрашивает ssh — через этот код он не проходит. Повтор безопасен: ключи остаются на сервере ($DIR/client.env).
# Клиентские параметры пишутся в config/deploy.local.yaml (в .gitignore, права 600).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${VPM_TUNNEL_HOST:-}"; USER_="${VPM_TUNNEL_USER:-root}"
PORT="${VPM_TUNNEL_PORT:-443}"; SNI="${VPM_TUNNEL_SNI:-www.yandex.ru}"
OUT="$ROOT/config/deploy.local.yaml"

usage() { echo "Использование: $0 [--host <ip|домен>] [--user root] [--port 443] [--sni www.yandex.ru]"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --user) USER_="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --sni)  SNI="$2";  shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Неизвестный аргумент: $1"; usage; exit 1 ;;
    esac
done

if [ -z "$HOST" ] && [ -t 0 ]; then
    read -rp "Адрес сервера (ip или домен): " HOST
    read -rp "Логин [$USER_]: " ANSWER; USER_="${ANSWER:-$USER_}"
fi
[ -n "$HOST" ] || { echo "✗ адрес сервера не задан: --host или VPM_TUNNEL_HOST"; exit 1; }

# Каталог на сервере — из каталога бинарей: тот же путь проверяет приёмка.
XRAY="$(awk '/^бинари:/{b=1; next} b && /^[^ #]/{b=0} b && /^  xray:/{x=1; next} x && /^  [^ ]/{x=0}
             x && /в_дереве:/{sub(/.*в_дереве: */, ""); print; exit}' "$ROOT/config/tunnel_diagnostics.yaml")"
[ -n "$XRAY" ] || { echo "✗ путь xray не объявлен: config/tunnel_diagnostics.yaml → бинари → xray → в_дереве"; exit 1; }
DIR="$(dirname "$XRAY")"

# Порт веб-заглушки — из того же каталога (в shell не хардкодим). Файлы заглушки уедут на сервер в
# одном SSH-канале через base64: отдельной передачи (scp) не заводим.
STUB_PORT="$(awk '/^заглушка:/{z=1; next} z && /^[^ #]/{z=0} z && /порт:/{print $2}' "$ROOT/config/tunnel_diagnostics.yaml")"
[ -n "$STUB_PORT" ] || { echo "✗ порт заглушки не объявлен: config/tunnel_diagnostics.yaml → заглушка → порт"; exit 1; }
STUB_DIR="$ROOT/deploy/tunnel_stub"
STUB_HTML_B64="$(base64 -w0 "$STUB_DIR/index.html")"
STUB_SRV_B64="$(base64 -w0 "$STUB_DIR/serve.py")"

# Выполняется на сервере: прогресс — в stderr, параметры клиента — в stdout, их забирает этот скрипт.
# Тело в функции: bash -s дочитывает её целиком до запуска, и команда, читающая stdin, не съест остаток.
remote_payload() {
cat <<PAYLOAD
main() {
set -euo pipefail
exec 3>&1 1>&2
DIR="$DIR"; PORT="$PORT"; SNI="$SNI"; STUB_PORT="$STUB_PORT"
fail() { echo "✗ \$*"; exit 1; }
open_port() {
    if command -v firewall-cmd >/dev/null && firewall-cmd -q --state 2>/dev/null; then
        firewall-cmd -q --permanent --add-port="\$1/tcp" && firewall-cmd -q --reload
        echo "✓ firewalld: открыт \$1/tcp"
    elif command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
        ufw allow "\$1/tcp" >/dev/null
        echo "✓ ufw: открыт \$1/tcp"
    fi
}
mkdir -p "\$DIR" && chmod 755 "\$DIR"

if [ ! -x "\$DIR/xray" ]; then
    case "\$(uname -m)" in
        x86_64|amd64) Z=Xray-linux-64.zip ;;
        aarch64|arm64) Z=Xray-linux-arm64-v8a.zip ;;
        *) fail "архитектура \$(uname -m): сборки Xray для неё установщик не знает" ;;
    esac
    command -v curl >/dev/null || fail "на сервере нет curl"
    T=\$(mktemp -d); trap 'rm -rf "\$T"' EXIT
    U="https://github.com/XTLS/Xray-core/releases/latest/download/\$Z"
    echo "→ загрузка \$U"
    curl -fsSL --retry 3 -o "\$T/\$Z" "\$U" && curl -fsSL --retry 3 -o "\$T/\$Z.dgst" "\$U.dgst" || fail "Xray не скачан: \$U"
    [ "\$(sed -n 's/^SHA2-256= *//p' "\$T/\$Z.dgst")" = "\$(sha256sum "\$T/\$Z" | cut -d' ' -f1)" ] \
        || fail "сумма архива Xray не сошлась с \$U.dgst"
    if command -v unzip >/dev/null; then unzip -q -o "\$T/\$Z" xray -d "\$T"
    elif command -v python3 >/dev/null; then python3 -m zipfile -e "\$T/\$Z" "\$T"
    else fail "нечем распаковать Xray: нет unzip и python3"; fi
    install -m 755 "\$T/xray" "\$DIR/xray"
fi
echo "✓ \$("\$DIR/xray" version | head -n 1)"

if [ ! -f "\$DIR/client.env" ]; then
    KP=\$("\$DIR/xray" x25519)
    # Xray до 25.x печатает «Public key:», с 26.x — «Password (PublicKey):».
    PRIV=\$(printf '%s\n' "\$KP" | sed -n 's/^Private[ ]*[Kk]ey: *//p')
    PUB=\$(printf '%s\n' "\$KP" | sed -n 's/.*Public[ ]*[Kk]ey)*: *//p')
    [ -n "\$PRIV" ] && [ -n "\$PUB" ] || fail "xray x25519: формат вывода не распознан"
    (umask 077; printf 'PRIV=%s\nPUB=%s\nUUID=%s\nSHORTID=%s\n' "\$PRIV" "\$PUB" "\$("\$DIR/xray" uuid)" \
        "\$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')" > "\$DIR/client.env")
fi
. "\$DIR/client.env"

BUSY=\$(ss -Htlnp "sport = :\$PORT" | grep -v '"xray"' || true)
[ -z "\$BUSY" ] || fail "порт \$PORT занят: \$BUSY"

(umask 027; cat > "\$DIR/config.json" <<CFG
{ "inbounds": [ { "listen": "0.0.0.0", "port": \$PORT, "protocol": "vless",
  "settings": { "clients": [ { "id": "\$UUID", "flow": "xtls-rprx-vision" } ], "decryption": "none" },
  "streamSettings": { "network": "tcp", "security": "reality",
    "realitySettings": { "dest": "\$SNI:443", "serverNames": [ "\$SNI" ], "privateKey": "\$PRIV", "shortIds": [ "\$SHORTID" ] } } } ],
  "outbounds": [ { "protocol": "freedom" } ] }
CFG
)
chgrp "\$(id -gn nobody)" "\$DIR/config.json"
"\$DIR/xray" -test -c "\$DIR/config.json" >/dev/null || fail "конфиг не прошёл проверку: \$DIR/xray -test -c \$DIR/config.json"

cat > /etc/systemd/system/vpm-tunnel.service <<UNIT
[Unit]
Description=VPM tunnel (Xray REALITY)
After=network-online.target
Wants=network-online.target
[Service]
User=nobody
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ExecStart=\$DIR/xray run -c \$DIR/config.json
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable -q vpm-tunnel
systemctl restart vpm-tunnel

open_port "\$PORT"

for _ in \$(seq 50); do
    ss -Htln "sport = :\$PORT" | grep -q . && break
    sleep 0.1
done
systemctl is-active -q vpm-tunnel && ss -Htln "sport = :\$PORT" | grep -q . \
    || { journalctl -u vpm-tunnel -n 20 --no-pager; fail "служба vpm-tunnel не слушает порт \$PORT"; }
echo "✓ служба vpm-tunnel слушает \$PORT/tcp"

# Веб-заглушка (404): минимальный stdlib-сервер под systemd, отдельным портом (443 занят туннелем).
command -v python3 >/dev/null || fail "на сервере нет python3 для веб-заглушки"
mkdir -p "\$DIR/stub"
printf %s "$STUB_HTML_B64" | base64 -d > "\$DIR/stub/index.html"
printf %s "$STUB_SRV_B64" | base64 -d > "\$DIR/stub/serve.py"
chmod 755 "\$DIR/stub"; chmod 644 "\$DIR/stub/index.html" "\$DIR/stub/serve.py"
BUSYS=\$(ss -Htlnp "sport = :\$STUB_PORT" | grep -v '"python3"' || true)
[ -z "\$BUSYS" ] || fail "порт заглушки \$STUB_PORT занят: \$BUSYS"
cat > /etc/systemd/system/vpm-stub.service <<UNIT
[Unit]
Description=VPM tunnel stub (404)
After=network.target
[Service]
User=nobody
Environment=PORT=\$STUB_PORT
NoNewPrivileges=true
ExecStart=/usr/bin/python3 \$DIR/stub/serve.py
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable -q vpm-stub
systemctl restart vpm-stub
open_port "\$STUB_PORT"
for _ in \$(seq 50); do ss -Htln "sport = :\$STUB_PORT" | grep -q . && break; sleep 0.1; done
systemctl is-active -q vpm-stub && ss -Htln "sport = :\$STUB_PORT" | grep -q . \
    || { journalctl -u vpm-stub -n 20 --no-pager; fail "веб-заглушка не слушает порт \$STUB_PORT"; }
echo "✓ веб-заглушка (404) слушает \$STUB_PORT/tcp"

printf 'port: %s\nsni: %s\nuuid: %s\npublic_key: %s\nshort_id: %s\nstub_port: %s\n' "\$PORT" "\$SNI" "\$UUID" "\$PUB" "\$SHORTID" "\$STUB_PORT" >&3
}
main </dev/null
PAYLOAD
}

ADDR="$(ssh -G "$HOST" 2>/dev/null | awk '$1 == "hostname" {print $2; exit}')"
ADDR="${ADDR:-$HOST}"
echo "→ $USER_@$HOST: серверный конец туннеля, порт $PORT, SNI $SNI"
PARAMS="$(remote_payload | ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o ServerAliveInterval=15 \
    -l "$USER_" "$HOST" 'if [ "$(id -u)" = 0 ]; then bash -s; else sudo -n bash -s; fi')" \
    || { echo "✗ серверный конец на $HOST не поставлен — причина выше"; exit 1; }

for key in port sni uuid public_key short_id stub_port; do
    grep -q "^$key: ." <<<"$PARAMS" || { echo "✗ сервер не вернул параметр $key"; exit 1; }
done
(umask 077
 { echo "# Клиентские параметры серверного конца туннеля — пишет scripts/deploy_tunnel_server.sh; uuid — ключ доступа."
   echo "host: $ADDR"
   printf '%s\n' "$PARAMS"; } > "$OUT")
chmod 600 "$OUT"
echo "✓ серверный конец туннеля работает: $ADDR:$PORT; параметры клиента — config/deploy.local.yaml"
