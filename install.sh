#!/bin/bash
# install.sh — установка MCP-сервера видеопайплайна

set -e

# Роль машины (до .venv) — по клиенту: консоль на этой машине без туннеля, Claude AI Web — через туннель.
# Неинтерактивно: VPM_INSTALL_CHOICE=local|console|server|tunnel-remote|tunnel-here
python3 scripts/installer_tui.py || exit 1
MCP="$(sed -n 's/^mcp: *//p' config/install.yaml)"
TUNNEL_END="$(sed -n 's/^tunnel: *//p' config/install.yaml)"

echo ""
echo "=== Установка Video Pipeline MCP ==="

if [ "$MCP" = "true" ]; then
    # Цену считает scripts/install_cost.py дверью сервера, то есть в .venv: перед наборами расчёта — scripts/configure.py;
    # нехватку места на базовом наборе отказывает сам pip, и set -e останавливает установку.

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

    # 5. Проверка FFmpeg. Подсказка — по факту менеджера пакетов этой системы и без команды: имя пакета у
    # дистрибутивов разное (у одних ffmpeg, у других ffmpeg-free или из стороннего репозитория).
    echo "Проверка FFmpeg..."
    if command -v ffmpeg &> /dev/null; then
        echo "✓ FFmpeg: $(ffmpeg -version | head -n 1)"
    else
        PM=""
        for candidate in apt-get dnf yum zypper pacman apk brew port; do
            if command -v "$candidate" &> /dev/null; then PM="$candidate"; break; fi
        done
        echo "✗ FFmpeg не найден в PATH: это системный бинарь, а не пакет pip —" \
             "${PM:+поставьте его менеджером пакетов $PM; }сборки по системам: https://ffmpeg.org/download.html"
    fi

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

# 9. Клиентский конец туннеля — его бинарь. Адрес по системе и архитектуре и политика загрузки объявлены в
# config/tunnel_diagnostics.yaml (`загрузка_клиента`, путь в дереве — `бинари`); ставит дверь — scripts/fetch_tunnel_binary.py.
case "$TUNNEL_END" in
    client|both)
        # Переезд прежнего пути в дереве на нейтральный.
        if [ -x "./bin/cloudflared" ] && [ ! -e "./bin/tunnel" ]; then
            mv ./bin/cloudflared ./bin/tunnel
        fi
        echo "Проверка бинаря туннеля..."
        if command -v cloudflared &> /dev/null; then
            echo "✓ бинарь туннеля: $(cloudflared --version 2>/dev/null | head -n 1)"
        elif [ -x "./bin/tunnel" ]; then
            echo "✓ бинарь туннеля: ./bin/tunnel"
        else
            # Роль без MCP окружения не ставит, а двери загрузки нужны её пакеты: минимальный .venv, версии из pyproject.
            if [ ! -x .venv/bin/python ]; then
                echo "Окружение двери загрузки (.venv, минимальное)..."
                python3 -m venv .venv
                python3 scripts/door_deps.py pydantic pydantic-core pyyaml filelock httpx h11 \
                    | .venv/bin/pip install -q -r /dev/stdin
            fi
            echo "Загрузка бинаря туннеля..."
            .venv/bin/python scripts/fetch_tunnel_binary.py \
                || echo "  бинарь туннеля не поставлен — причина выше, компонент назовёт приёмка"
        fi
        ;;
esac

# 10. Серверный конец туннеля — по SSH: адрес и логин из VPM_TUNNEL_HOST/VPM_TUNNEL_USER или вопросом в терминале;
# пароль, если ключа нет, спрашивает сам ssh. Без адреса и без терминала шаг называет команду.
case "$TUNNEL_END" in
    server|both)
        echo ""
        if [ -n "${VPM_TUNNEL_HOST:-}" ] || [ -t 0 ]; then
            ./scripts/deploy_tunnel_server.sh \
                || echo "  серверный конец не поставлен — причина выше; повтор: ./scripts/deploy_tunnel_server.sh"
        else
            echo "Серверный конец туннеля: VPM_TUNNEL_HOST=<адрес> ./scripts/deploy_tunnel_server.sh"
        fi
        ;;
esac

echo ""
echo "=== Установка завершена ==="

# Меню, а не автозапуск настроек: настройки доступны, но обязательным шагом установки не являются.
# Без терминала (CI) меню не поднимается — там же печатается отчёт быстрой проверки.
# Окружение есть у MCP и у роли с клиентским концом (минимальное, шаг 9); без него меню нечем поднять — печатаем команды.
if [ -x .venv/bin/python ]; then
    PY=".venv/bin/python"
else
    PY="python3"
fi
if "$PY" -c "import yaml, httpx" 2>/dev/null; then
    "$PY" scripts/control_center.py
else
    echo "Проверка туннеля:  $PY scripts/control_center.py  (нужны пакеты yaml и httpx)"
fi
