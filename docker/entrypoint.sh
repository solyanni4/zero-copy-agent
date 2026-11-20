#!/usr/bin/env bash
set -e

echo "============================================================"
echo "  ⚡ zero-copy-agent (Server Edition)"
echo "  OpenAI API: http://0.0.0.0:8000/v1"
echo "  noVNC Web Auth: http://0.0.0.0:6080/vnc.html"
echo "============================================================"

# Запуск виртуального экрана (Xvfb)
export DISPLAY=:99
echo "[+] Запуск виртуального X11-дисплея (Xvfb :99)..."
Xvfb :99 -screen 0 1280x800x24 &
XVFB_PID=$!
sleep 1

# Запуск легкого оконного менеджера (Fluxbox)
echo "[+] Запуск оконного менеджера (Fluxbox)..."
fluxbox &
FLUXBOX_PID=$!

# Запуск VNC-сервера
echo "[+] Запуск VNC сервера (порт 5900)..."
x11vnc -display :99 -forever -nopw -shared -bg -quiet

# Запуск noVNC веб-клиента
echo "[+] Запуск noVNC веб-моста (порт 6080)..."
websockify --web=/usr/share/novnc 6080 localhost:5900 &
WEBSOCKIFY_PID=$!

echo "[+] Система GUI готова. Веб-интерфейс для 2FA доступен на порту 6080."
echo "[+] Запуск шлюза zero-copy-agent..."

# Запуск основного приложения
exec python main.py
