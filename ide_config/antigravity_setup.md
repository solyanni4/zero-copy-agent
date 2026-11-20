# 🤖 Подключение к Antigravity (CLI & IDE)

Шлюз **zero-copy-agent** предоставляет локальный OpenAI-совместимый интерфейс, который позволяет направлять запросы Antigravity напрямую в ваши активные веб-подписки (Gemini Advanced, ChatGPT Plus) без расхода платных API-токенов.

---

## Вариант 1. Переменные окружения (Терминальный агент)

Задайте переменные окружения перед запуском `agy`:

```bash
# Linux / macOS / WSL:
export OPENAI_API_BASE="http://127.0.0.1:8000/v1"
export OPENAI_API_KEY="sk-zero-copy"

# Windows (PowerShell):
$env:OPENAI_API_BASE="http://127.0.0.1:8000/v1"
$env:OPENAI_API_KEY="sk-zero-copy"
```

---

## Вариант 2. Интеграция через LiteLLM Gateway

Если у вас в Homelab поднят LiteLLM Proxy (порт 4000), добавьте секцию в `config.yaml` LiteLLM:

```yaml
model_list:
  - model_name: gemini-web-advanced
    litellm_params:
      model: openai/gemini-advanced
      api_base: http://127.0.0.1:8000/v1
      api_key: sk-zero-copy
```
Теперь Antigravity может обращаться к модели `gemini-web-advanced` как к стандартному провайдеру!

---

## Вариант 3. Вызов в Python скриптах агентов

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="sk-zero-copy"
)

response = client.chat.completions.create(
    model="gemini-advanced",
    messages=[{"role": "user", "content": "Проанализируй архитектуру сервера"}],
    stream=True
)

for chunk in response:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

---

## Вариант 4. Нативное подключение через Model Context Protocol (MCP Server)

Начиная с версии **v0.2.3**, шлюз содержит встроенный MCP-сервер с поддержкой SSE (`Server-Sent Events`). Это позволяет Google Antigravity и субагентам напрямую вызывать инструменты Zero-Copy Agent.

### Добавление сервера в конфигурацию Antigravity:
В конфигурационном файле MCP серверов Antigravity (например, `~/.gemini/antigravity/mcp_config.json`):

```json
{
  "mcpServers": {
    "zero-copy-agent": {
      "url": "http://127.0.0.1:8000/sse"
    }
  }
}
```

### Доступные инструменты (MCP Tools):
* 🏛️ **`cascade_architect`** — двухэтапный каскад «Архитектор» (проектирование системного чертежа через Gemini 4 Neon ➔ чистая кодогенерация через локальную GPU Qwen2.5-Coder 14B).
* 🦙 **`local_gpu_code`** — мгновенное локальное исполнение кода на GPU через Ollama с нулевой задержкой и без расхода токенов.
* 🧠 **`gemini_deep_analysis`** — глубокий логический аудит и анализ через веб-модель Gemini 4 Neon.
* 📊 **`get_token_savings`** — получение онлайн-метрик экономии токенов и сэкономленной суммы в долларах ($).
* 🗜️ **`compress_code_context`** — контекстное L1-сжатие исходного кода (до 35% экономии токенов).

