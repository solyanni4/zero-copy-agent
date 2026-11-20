# 🎭 Подключение к Claude Code и Claude Desktop

Шлюз **zero-copy-agent** позволяет запускать терминальный агент **Claude Code** и десктопное приложение **Claude Desktop**, перенаправляя генерацию в браузерные сессии без затрат на токены Anthropic API.

---

## Вариант 1. Claude Code CLI (через LiteLLM шлюз)

Поскольку Claude Code ожидает эндпоинты формата Anthropic Messages API, используется локальный транслятор (LiteLLM или наш встроенный адаптер):

1. Запустите мост в терминале:
   ```bash
   pip install litellm
   litellm --model openai/gemini-advanced --api_base http://127.0.0.1:8000/v1 --port 4000
   ```

2. Задайте переменные и запустите Claude Code:
   ```bash
   export ANTHROPIC_BASE_URL="http://127.0.0.1:4000"
   export ANTHROPIC_API_KEY="sk-zero-copy"
   claude
   ```

---

## Вариант 2. Claude Desktop (через FastMCP сервер)

Отредактируйте конфигурационный файл Claude Desktop:
* **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
* **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "zero-copy-bridge": {
      "command": "python",
      "args": ["-m", "core.protocol.adapters"],
      "env": {
        "ZERO_COPY_URL": "http://127.0.0.1:8000"
      }
    }
  }
}
```
