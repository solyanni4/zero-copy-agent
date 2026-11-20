# ⚡ Настройка Cursor за 30 секунд

В редакторе **Cursor**:
1. Откройте **Settings** (`Ctrl + ,` или `Cmd + ,`) -> перейдите в раздел **Models**.
2. Включите переключатель **OpenAI API Key**.
3. Введите любой ключ, например:
   ```text
   sk-zero-copy
   ```
4. Разверните выпадающий список **Override OpenAI Base URL** и укажите:
   ```text
   http://127.0.0.1:8000/v1
   ```
5. Нажмите **Add Model** и добавьте:
   - `gemini-advanced` (или `chatgpt-plus`)
6. Готово! Откройте Cursor Chat (`Ctrl + L` / `Cmd + L`) и задайте любой вопрос.

---

## 🔌 Подключение инструментов через MCP (Model Context Protocol)

В Cursor можно подключить инструменты Zero-Copy Agent напрямую в Composer и Chat:
1. Перейдите в **Settings** (`Ctrl + ,` / `Cmd + ,`) ➔ **Features** ➔ вкладка **MCP**.
2. Нажмите **+ Add New MCP Server**.
3. Заполните параметры:
   - **Name:** `zero-copy-agent`
   - **Type:** `sse`
   - **Server URL:** `http://127.0.0.1:8000/sse`
4. Нажмите **Add**. В статусе появится зеленый индикатор и список из 5 активных инструментов (`cascade_architect`, `local_gpu_code`, `gemini_deep_analysis`, `get_token_savings`, `compress_code_context`).

