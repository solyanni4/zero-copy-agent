FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99

# Установка виртуального экрана, VNC и веб-интерфейса noVNC для прохождения 2FA
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    fluxbox \
    novnc \
    websockify \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Установка Python-зависимостей и Playwright Chromium
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install --with-deps chromium

COPY . .
RUN chmod +x docker/entrypoint.sh

# 8000: OpenAI API & FastMCP
# 6080: noVNC Browser UI для авторизации (2FA / Google / Cloudflare)
EXPOSE 8000 6080

ENTRYPOINT ["docker/entrypoint.sh"]
