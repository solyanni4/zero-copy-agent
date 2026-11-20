# 🛠 Подключение Zero-Copy Agent к Visual Studio Code (Continue)

Вы можете настроить VS Code + Continue **автоматически в 1 команду**, либо вручную.

---

## Вариант 1. Автоматическая настройка в 1 клик (Рекомендуется!)

Просто вставьте эту команду в **PowerShell**:

```powershell
irm https://raw.githubusercontent.com/solyanni4/zero-copy-agent/main/setup_vscode.ps1 | iex
```

### Что делает скрипт полностью сам:
1. Проверяет наличие VS Code на компьютере и автоматически устанавливает расширение **`Continue`**.
2. Создает и правильно конфигурирует файл `~/.continue/config.yaml` со схемой Continue 2.0 (`roles: [chat, edit]`).
3. Добавляет сразу 3 готовые модели с живым пробросом подписок:
   * **Gemini Advanced** (`model: gemini-advanced`)
   * **ChatGPT Plus** (`model: gpt-4o`)
   * **Claude 3.7 Sonnet** (`model: claude-3-7-sonnet`)

---

## Вариант 2. Ручная настройка config.yaml

Если вы хотите вручную настроить конфигурацию, откройте файл `%USERPROFILE%\.continue\config.yaml` и вставьте:

```yaml
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
```

---

## 🎮 Как отправить первый запрос:
1. Убедитесь, что сервер запущен: `.\start.bat` (пункт `1`).
2. В VS Code нажмите комбинацию клавиш **`Ctrl + L`**.
3. В чате Continue выберите модель **`Gemini Advanced (Zero-Copy)`**.
4. Напишите любой вопрос, например:
   > *«Напиши функцию на Python для поиска дубликатов в массиве»*
5. В начале ответа шлюз выведет плашку активной модели:
   `⚡ [zero-copy-agent | Gemini Advanced (Живая веб-подписка 🟢)]`

---

## 🛡️ Защита от протухания сессий (Keep-Alive Daemon)
Шлюз непрерывно поддерживает вашу сессию активной: встроенный фоновый таймер каждые 90 минут обновляет сессионные куки веб-интерфейса, поэтому вы можете оставлять редактор открытым на сутки и более без страха отвала авторизации.
