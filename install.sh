#!/bin/bash
# install.sh — установка MCP-сервера видеопайплайна

set -e

echo "=== Установка MCP-сервера видеопайплайна ==="

# 1. Создание виртуального окружения
echo "Создание .venv..."
python3 -m venv .venv

# 2. Активация окружения
echo "Активация .venv..."
source .venv/bin/activate

# 3. Обновление pip
echo "Обновление pip..."
pip install --upgrade pip

# 4. Установка зависимостей
echo "Установка зависимостей из requirements.txt..."
pip install -r requirements.txt

# 5. Проверка FFmpeg
echo "Проверка FFmpeg..."
if command -v ffmpeg &> /dev/null; then
    echo "FFmpeg найден: $(ffmpeg -version | head -n 1)"
else
    echo "ВНИМАНИЕ: FFmpeg не найден в PATH"
    echo "Установите FFmpeg: sudo apt install ffmpeg"
fi

# 6. Проверка stable-ts
echo "Проверка stable-ts..."
python3 -c "import stable_whisper; print('stable-ts OK')" 2>/dev/null || echo "stable-ts будет доступен после установки PyTorch"

# 7. Установка cloudflared (туннель к Claude AI Web — D11)
echo "Проверка cloudflared..."
if command -v cloudflared &> /dev/null; then
    echo "cloudflared найден: $(cloudflared --version 2>/dev/null | head -n 1)"
elif [ -x "./bin/cloudflared" ]; then
    echo "cloudflared найден локально: ./bin/cloudflared"
else
    echo "cloudflared не найден — скачиваю статический бинарь в ./bin/ ..."
    mkdir -p bin
    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64|amd64) CF_ARCH="amd64" ;;
        aarch64|arm64) CF_ARCH="arm64" ;;
        *) CF_ARCH="" ;;
    esac
    if [ -n "$CF_ARCH" ]; then
        CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}"
        if curl -fsSL "$CF_URL" -o bin/cloudflared 2>/dev/null || wget -q "$CF_URL" -O bin/cloudflared 2>/dev/null; then
            chmod +x bin/cloudflared
            echo "cloudflared установлен: ./bin/cloudflared ($(./bin/cloudflared --version 2>/dev/null | head -n 1))"
        else
            echo "ВНИМАНИЕ: не удалось скачать cloudflared. Установите вручную:"
            echo "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
        fi
    else
        echo "ВНИМАНИЕ: неизвестная архитектура '$ARCH'. Установите cloudflared вручную."
    fi
fi

echo ""
echo "=== Установка завершена ==="
echo "Для запуска сервера + туннеля одной командой: ./run.sh"
