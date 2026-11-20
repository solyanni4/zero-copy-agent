#!/usr/bin/env bash
# ==============================================================================
# ⚡ Zero-Copy Agent — Universal 1-Click Installer (Linux / macOS / WSL)
# ==============================================================================
set -e

BOLD="\033[1m"
GREEN="\033[32m"
BLUE="\033[34m"
YELLOW="\033[33m"
RED="\033[31m"
NC="\033[0m"

echo -e "${BOLD}${BLUE}=================================================================${NC}"
echo -e "${BOLD}${GREEN}   ⚡ ZERO-COPY AGENT — Автоматическая установка за 1 клик       ${NC}"
echo -e "${BOLD}${BLUE}=================================================================${NC}"

# 1. Поиск рабочего Python
PY_CMD="python3"
if ! command -v "$PY_CMD" &>/dev/null; then
    for cmd in python3.12 python3.11 python3.10 python; do
        if command -v "$cmd" &>/dev/null; then
            PY_CMD="$cmd"
            break
        fi
    done
fi

if ! command -v "$PY_CMD" &>/dev/null; then
    echo -e "${RED}[ERROR] Python 3 не найден. Пожалуйста, установите Python 3.10+${NC}"
    exit 1
fi

PY_VER=$($PY_CMD -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "${GREEN}✓ Обнаружен Python: ${PY_CMD} (${PY_VER})${NC}"

# 2. Определение рабочей директории
TARGET_DIR="${HOME}/zero-copy-agent"
if [ -f "main.py" ] && [ -f "requirements.txt" ]; then
    TARGET_DIR="$(pwd)"
    echo -e "${BLUE}ℹ Установка в текущую директорию: ${TARGET_DIR}${NC}"
else
    if [ ! -d "$TARGET_DIR" ]; then
        echo -e "${BLUE}⬇ Клонирование репозитория в ${TARGET_DIR}...${NC}"
        git clone https://github.com/solyanni4/zero-copy-agent.git "$TARGET_DIR" 2>/dev/null || {
            mkdir -p "$TARGET_DIR"
            curl -sSL https://github.com/solyanni4/zero-copy-agent/archive/refs/heads/main.tar.gz | tar -xz -C "$TARGET_DIR" --strip-components=1
        }
    fi
    cd "$TARGET_DIR"
fi

# 3. Создание изолированного виртуального окружения (с защитой от отсутствия ensurepip)
if [ ! -f "venv/bin/pip" ] || [ ! -f "venv/bin/python" ]; then
    echo -e "${YELLOW}⚙ Создание виртуального окружения (venv)...${NC}"
    rm -rf venv
    if ! "$PY_CMD" -m venv venv 2>/dev/null; then
        echo -e "${YELLOW}⚙ Системный venv не содержит pip (особенность Ubuntu/Debian).${NC}"
        echo -e "${BLUE}⬇ Автоматически загружаю pip напрямую через bootstrap...${NC}"
        "$PY_CMD" -m venv --without-pip venv
        curl -sSL https://bootstrap.pypa.io/get-pip.py | ./venv/bin/python -
    fi
fi

echo -e "${GREEN}✓ Виртуальное окружение venv готово.${NC}"

# 4. Установка зависимостей
echo -e "${YELLOW}📦 Установка зависимостей проекта...${NC}"
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install -r requirements.txt -q

# 5. Установка браузерного движка Playwright
echo -e "${YELLOW}🌐 Проверка Chromium движка...${NC}"
./venv/bin/playwright install chromium --with-deps 2>/dev/null || ./venv/bin/playwright install chromium

# 6. Конфигурация Continue для VS Code
CONTINUE_DIR="${HOME}/.continue"
mkdir -p "$CONTINUE_DIR"
if [ ! -f "$CONTINUE_DIR/config.yaml" ]; then
    echo -e "${BLUE}⚙ Создание конфигурации Continue 2.0 (~/.continue/config.yaml)...${NC}"
    cat << 'EOF' > "$CONTINUE_DIR/config.yaml"
name: Zero-Copy Config
version: 1.0.0
schema: v1

models:
  - name: Gemini Advanced (Zero-Copy)
    provider: openai
    model: gemini-advanced
    apiBase: http://127.0.0.1:8000/v1
    apiKey: sk-zero-copy
    roles:
      - chat
      - edit

  - name: ChatGPT Plus / GPT-4o (Zero-Copy)
    provider: openai
    model: gpt-4o
    apiBase: http://127.0.0.1:8000/v1
    apiKey: sk-zero-copy
    roles:
      - chat
      - edit

  - name: Claude 3.7 Sonnet (Zero-Copy)
    provider: openai
    model: claude-3-7-sonnet
    apiBase: http://127.0.0.1:8000/v1
    apiKey: sk-zero-copy
    roles:
      - chat
      - edit
EOF
    echo -e "${GREEN}✓ Конфигурация Continue успешно создана!${NC}"
fi

echo -e ""
echo -e "${BOLD}${GREEN}🎉 УСТАНОВКА ЗАВЕРШЕНА УСПЕШНО!${NC}"
echo -e "${BOLD}-----------------------------------------------------------------${NC}"
echo -e "🚀 Для запуска шлюза выполните:"
echo -e "   ${GREEN}cd ${TARGET_DIR} && ./start.sh${NC}"
echo -e ""
echo -e "🔌 В VS Code нажмите ${BOLD}Ctrl + L${NC} и выберите модель ${BOLD}Gemini Advanced${NC}."
echo -e "${BOLD}-----------------------------------------------------------------${NC}"

if [ "$1" == "--start" ]; then
    echo -e "${GREEN}Запуск сервера...${NC}"
    exec ./start.sh
fi
