#!/bin/bash
# deploy_tunnel_server.sh — развернуть серверный конец туннеля (Xray VLESS+REALITY) на VPS по SSH.
#
# Пароль НЕ проходит через этот код: зовём системный ssh, пароль спрашивает он сам в терминале.
# По умолчанию DRY-RUN — печатает план и удалённый payload, ничего не делает. Реальный запуск —
# только с --go и после подтверждения. VPN на сервере не трогаем: своя директория и порт.
# Клиентские параметры (publicKey/shortId/uuid/порт/SNI) печатаются в конце — их в config/deploy.local.yaml.

set -euo pipefail

HOST=""; USER_="root"; PORT="${VPM_TUNNEL_PORT:-8443}"; SNI="${VPM_TUNNEL_SNI:-www.yandex.ru}"; GO=""
DIR="/opt/vpm-tunnel"

usage() { echo "Использование: $0 --host <ip|домен> [--user root] [--port 8443] [--sni www.yandex.ru] [--go]"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --user) USER_="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --sni)  SNI="$2";  shift 2 ;;
        --go)   GO="1"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Неизвестный аргумент: $1"; usage; exit 1 ;;
    esac
done

[ -z "$HOST" ] && { read -rp "Адрес VPS (ip или домен): " HOST; }
[ -z "$HOST" ] && { echo "✗ адрес VPS не задан"; exit 1; }

# Удалённый payload: официальный Xray в свою директорию, ключи REALITY на самом сервере,
# конфиг VLESS+REALITY (SNI под Яндекс), свой systemd-юнит. Ничего чужого не трогает.
remote_payload() {
cat <<PAYLOAD
set -euo pipefail
DIR="$DIR"; PORT="$PORT"; SNI="$SNI"
mkdir -p "\$DIR"
if [ ! -x "\$DIR/xray" ]; then
    ARCH=\$(uname -m); case "\$ARCH" in x86_64|amd64) Z=Xray-linux-64.zip;; aarch64|arm64) Z=Xray-linux-arm64-v8a.zip;; *) echo "неизвестная арх \$ARCH"; exit 1;; esac
    curl -fsSL "https://github.com/XTLS/Xray-core/releases/latest/download/\$Z" -o /tmp/\$Z
    (cd "\$DIR" && unzip -o /tmp/\$Z xray geoip.dat geosite.dat >/dev/null) && chmod +x "\$DIR/xray"
fi
KP=\$("\$DIR/xray" x25519); PRIV=\$(echo "\$KP" | sed -n 's/.*Private key: *//p'); PUB=\$(echo "\$KP" | sed -n 's/.*Public key: *//p')
UUID=\$("\$DIR/xray" uuid); SHORTID=\$(openssl rand -hex 8)
cat > "\$DIR/config.json" <<CFG
{ "inbounds": [ { "listen": "0.0.0.0", "port": \$PORT, "protocol": "vless",
  "settings": { "clients": [ { "id": "\$UUID", "flow": "xtls-rprx-vision" } ], "decryption": "none" },
  "streamSettings": { "network": "tcp", "security": "reality",
    "realitySettings": { "dest": "\$SNI:443", "serverNames": [ "\$SNI" ], "privateKey": "\$PRIV", "shortIds": [ "\$SHORTID" ] } } } ],
  "outbounds": [ { "protocol": "freedom" } ] }
CFG
"\$DIR/xray" -test -c "\$DIR/config.json"
cat > /etc/systemd/system/vpm-tunnel.service <<UNIT
[Unit]
Description=VPM tunnel (Xray REALITY)
After=network.target
[Service]
ExecStart=\$DIR/xray run -c \$DIR/config.json
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now vpm-tunnel
echo "=== КЛИЕНТСКИЕ ПАРАМЕТРЫ (в config/deploy.local.yaml) ==="
echo "host: $HOST"; echo "port: \$PORT"; echo "uuid: \$UUID"; echo "public_key: \$PUB"; echo "short_id: \$SHORTID"; echo "sni: \$SNI"
PAYLOAD
}

echo "=== ПЛАН развёртывания серверного конца туннеля ==="
echo "  VPS:        $USER_@$HOST"
echo "  директория: $DIR (своя; VPN не трогаем)"
echo "  порт:       $PORT   SNI-камуфляж: $SNI"
echo "  ставит:     официальный Xray-core, systemd-юнит vpm-tunnel"
echo ""
echo "--- удалённый payload (выполнится на VPS) ---"
remote_payload
echo "--- конец payload ---"

if [ -z "$GO" ]; then
    echo ""; echo "DRY-RUN: ничего не выполнено. Реальный запуск: добавь --go (ssh спросит пароль сам)."
    exit 0
fi

read -rp $'\nВыполнить это на '"$USER_@$HOST"$'? Введи «да»: ' ANS
[ "$ANS" = "да" ] || { echo "Отменено."; exit 0; }
echo "→ ssh $USER_@$HOST (пароль спросит ssh, не мы)…"
remote_payload | ssh -t "$USER_@$HOST" 'bash -s'
