#!/bin/bash
# install.sh — установка MCP-сервера видеопайплайна

set -e

# Роль машины (до .venv). Неинтерактивно: VPM_INSTALL_CHOICE=local|server|tunnel-remote|tunnel-here
python3 scripts/installer_tui.py || exit 1
MCP="$(sed -n 's/^mcp: *//p' config/install.yaml)"
TUNNEL_END="$(sed -n 's/^tunnel: *//p' config/install.yaml)"

echo ""
echo "=== Установка Video Pipeline MCP ==="

if [ "$MCP" = "true" ]; then
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
    echo "Установка зависимостей из pyproject.toml..."
    pip install -e .

    # 5. Проверка FFmpeg
    echo "Проверка FFmpeg..."
    if command -v ffmpeg &> /dev/null; then
        echo "✓ FFmpeg: $(ffmpeg -version | head -n 1)"
    else
        echo "✗ FFmpeg не найден в PATH. Установите: sudo apt install ffmpeg"
    fi

    # 6. Проверка stable-ts
    echo "Проверка stable-ts..."
    python3 -c "import stable_whisper; print('✓ stable-ts')" 2>/dev/null \
        || echo "  stable-ts будет доступен после установки PyTorch"

    # 7. Локальный инференс картинок (удаление фона, апскейл) — граф ONNX.
    # Веса сюда НЕ тянутся: какую модель ставить, решает владелец из живого каталога.
    echo "Проверка onnxruntime..."
    if python3 -c "import onnxruntime" 2>/dev/null; then
        echo "✓ onnxruntime (модели ставятся в настройках)"
    else
        echo "✗ onnxruntime не поднялся — удаление фона и апскейл откажут честно"
    fi

    # 8. Дверь коммита. Без неё немой промах уезжает в историю, а её отсутствие выглядит чистым
    # проходом: локально не проверяет никто, CI ловит уже после push.
    echo "Установка двери коммита (pre-commit)..."
    if python3 -c "import pre_commit" 2>/dev/null && pre-commit install; then
        echo "✓ дверь коммита"
    else
        echo "  дверь коммита не поставлена: pip install -e '.[dev]' && pre-commit install"
    fi
fi

# 9. Клиентский конец туннеля — cloudflared.
case "$TUNNEL_END" in
    client|both)
        echo "Проверка cloudflared..."
        if command -v cloudflared &> /dev/null; then
            echo "✓ cloudflared: $(cloudflared --version 2>/dev/null | head -n 1)"
        elif [ -x "./bin/cloudflared" ]; then
            echo "✓ cloudflared: ./bin/cloudflared"
        else
            echo "Загрузка cloudflared..."
            mkdir -p bin
            case "$(uname -m)" in
                x86_64|amd64) CF_ARCH="amd64" ;;
                aarch64|arm64) CF_ARCH="arm64" ;;
                *) CF_ARCH="" ;;
            esac
            CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}"
            if [ -n "$CF_ARCH" ] && { curl -fsSL "$CF_URL" -o bin/cloudflared 2>/dev/null \
                                      || wget -q "$CF_URL" -O bin/cloudflared 2>/dev/null; }; then
                chmod +x bin/cloudflared
                echo "✓ cloudflared: ./bin/cloudflared"
            else
                echo "✗ не удалось загрузить cloudflared — установите его вручную"
            fi
        fi
        ;;
esac

# 10. Серверный конец туннеля ставится отдельным шагом: ему нужен адрес машины и её пароль,
# который через этот код не проходит (его спрашивает сам ssh).
case "$TUNNEL_END" in
    server|both)
        echo ""
        echo "Серверный конец туннеля настраивается отдельно:"
        echo "  ./scripts/deploy_tunnel_server.sh --host <адрес сервера>"
        echo "  (без --go печатает план и ничего не выполняет)"
        ;;
esac

echo ""
echo "=== Установка завершена ==="

# Меню, а не автозапуск настроек: настройки доступны, но обязательным шагом установки не являются.
# Без терминала (CI) меню не поднимается — там же печатается отчёт быстрой проверки.
# Роль без MCP окружения не ставит, и меню тогда нечем поднять: печатаем команды, а не падаем.
if [ "$MCP" = "true" ]; then
    PY=".venv/bin/python"
else
    PY="python3"
fi
if "$PY" -c "import yaml, httpx" 2>/dev/null; then
    "$PY" scripts/control_center.py
else
    echo "Проверка туннеля:  $PY scripts/control_center.py  (нужны пакеты yaml и httpx)"
fi
