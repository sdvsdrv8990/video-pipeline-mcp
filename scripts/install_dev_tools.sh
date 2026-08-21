#!/usr/bin/env bash
# Инструменты ГЕЙТА, которых нет в PyPI. Серверу они не нужны — поэтому здесь, а не в install.sh:
# install.sh ставит то, без чего сервер не работает.
#
# Версия читается из .github/workflows/ci.yml: локальный гейт обязан гонять ровно тот бинарь,
# что и CI. Дубль версии здесь означал бы «локально зелено, в CI красно» без объяснимой причины.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CI="$ROOT/.github/workflows/ci.yml"
BIN="$ROOT/.venv/bin"

[ -d "$BIN" ] || { echo "нет $BIN — сначала install.sh (виртуальное окружение)"; exit 1; }

VERSION="$(grep -oE 'GITLEAKS_VERSION:[[:space:]]*"[0-9.]+"' "$CI" | grep -oE '[0-9.]+' || true)"
[ -n "$VERSION" ] || { echo "версия gitleaks не найдена в $CI — гейт и скрипт разошлись"; exit 1; }

ARCH="$(uname -m)"; case "$ARCH" in x86_64) ARCH=x64 ;; aarch64|arm64) ARCH=arm64 ;; esac
URL="https://github.com/gitleaks/gitleaks/releases/download/v${VERSION}/gitleaks_${VERSION}_linux_${ARCH}.tar.gz"

echo "gitleaks $VERSION ($ARCH) → $BIN"
curl -sSfL "$URL" | tar -xz -C "$BIN" gitleaks
chmod +x "$BIN/gitleaks"

# Пост-условие: молчаливая распаковка не считается установкой.
"$BIN/gitleaks" version >/dev/null || { echo "бинарь не запускается"; exit 1; }
echo "готово: $("$BIN/gitleaks" version)"
