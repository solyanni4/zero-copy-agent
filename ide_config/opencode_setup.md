# 🚀 Подключение к OpenCode, Cline и OpenHands

Популярные агентские расширения для VS Code (Cline, Roo Code, OpenCode) и автономные среды разработки (OpenHands) нативно поддерживают любые OpenAI-совместимые шлюзы.

---

## 1. Настройка в Cline / Roo Code (VS Code Extension)

1. Откройте расширение **Cline** в боковой панели VS Code.
2. Нажмите на иконку шестерёнки ⚙️ (**Settings**).
3. В выпадающем списке **API Provider** выберите:
   ```text
   OpenAI Compatible
   ```
4. Заполните поля подключения:
   * **Base URL:** `http://127.0.0.1:8000/v1`
   * **API Key:** `sk-zero-copy`
   * **Model ID:** `gemini-advanced` *(или `chatgpt-plus`, `claude-pro`)*
5. Нажмите **Done**. Теперь Cline будет запускать автономные агентские циклы, обращаясь напрямую в веб-сессию!

---

## 2. Настройка в OpenCode CLI

При запуске OpenCode укажите локальный эндпоинт:

```bash
opencode --api-base http://127.0.0.1:8000/v1 --api-key sk-zero-copy --model gemini-advanced
```

---

## 3. Настройка в OpenHands (Docker)

В файле конфигурации `config.toml` OpenHands:

```toml
[llm]
model = "openai/gemini-advanced"
base_url = "http://host.docker.internal:8000/v1"
api_key = "sk-zero-copy"
```
