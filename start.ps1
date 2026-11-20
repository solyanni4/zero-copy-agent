param (
    [switch]$Test,
    [string]$Login = ""
)

# ==============================================================================
# Zero-Copy Agent Launcher for PowerShell
# ==============================================================================
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$targetDir = $PSScriptRoot
if (-not $targetDir) { $targetDir = (Get-Location).Path }
Set-Location $targetDir

$py = Join-Path $targetDir "venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[ОШИБКА] Виртуальное окружение не найдено в $targetDir\venv" -ForegroundColor Red
    Write-Host "Пожалуйста, сначала запустите установку: irm .../install.ps1 | iex" -ForegroundColor Yellow
    exit 1
}

if ($Test) {
    Write-Host "⚡ Запуск теста подключения..." -ForegroundColor Cyan
    & $py test_client.py
    exit 0
}

if ($Login) {
    Write-Host "🔐 Открываю браузер для входа в $Login..." -ForegroundColor Cyan
    & $py -m core.auth --provider $Login
    exit 0
}

function Show-Menu {
    Clear-Host
    Write-Host "=================================================================" -ForegroundColor Cyan
    Write-Host "   ⚡ ZERO-COPY AGENT — Панель управления                        " -ForegroundColor Green
    Write-Host "=================================================================" -ForegroundColor Cyan
    Write-Host "   1. 🖥️ Запустить приложение (Единое окно + Системный трей)     " -ForegroundColor White
    Write-Host "   2. 🚀 Запустить в консольном режиме (Headless-сервер)         " -ForegroundColor Gray
    Write-Host "   3. 🔐 Войти в Google Gemini (браузер для авторизации)         " -ForegroundColor Yellow
    Write-Host "   4. 🔐 Войти в ChatGPT (браузер для авторизации)               " -ForegroundColor Yellow
    Write-Host "   5. 🔐 Войти в Claude (браузер для авторизации)                " -ForegroundColor Yellow
    Write-Host "   6. ⚡ Проверить работу (быстрый тест соединения)               " -ForegroundColor Cyan
    Write-Host "   7. 🔌 Мастер настройки IDE и Агентов (Cursor, Antigravity, VS Code) " -ForegroundColor Magenta
    Write-Host "   8. 🔄 Обновить zero-copy-agent                                " -ForegroundColor Blue
    Write-Host "   0. Выход                                                      " -ForegroundColor Gray
    Write-Host "=================================================================" -ForegroundColor Cyan
    Write-Host ""
}

while ($true) {
    Show-Menu
    $choice = Read-Host "Выберите действие [0-8]"
    switch ($choice) {
        "1" {
            Clear-Host
            Write-Host "Запуск zero-copy-agent (Окно + Трей)..." -ForegroundColor Green
            & $py desktop/app.py
            break
        }
        "2" {
            Clear-Host
            Write-Host "Запуск в консольном режиме: http://127.0.0.1:8000" -ForegroundColor Cyan
            & $py main.py
            break
        }
        "3" {
            Write-Host "`nОткрываю браузер для входа в Gemini..." -ForegroundColor Cyan
            & $py -m core.auth --provider gemini
            Read-Host "`nНажмите ENTER для возврата в меню"
        }
        "4" {
            Write-Host "`nОткрываю браузер для входа в ChatGPT..." -ForegroundColor Cyan
            & $py -m core.auth --provider chatgpt
            Read-Host "`nНажмите ENTER для возврата в меню"
        }
        "5" {
            Write-Host "`nОткрываю браузер для входа в Claude..." -ForegroundColor Cyan
            & $py -m core.auth --provider claude
            Read-Host "`nНажмите ENTER для возврата в меню"
        }
        "6" {
            Write-Host "`nПроверка связи со шлюзом..." -ForegroundColor Cyan
            & $py test_client.py
            Read-Host "`nНажмите ENTER для возврата в меню"
        }
        "7" {
            Write-Host "`nЗапуск мастера настройки IDE..." -ForegroundColor Magenta
            & $py -m core.cli_integrate apply all
            Read-Host "`nНажмите ENTER для возврата в меню"
        }
        "8" {
            Write-Host "`nПроверка и установка обновлений..." -ForegroundColor Blue
            & .\update.ps1
            Read-Host "`nНажмите ENTER для возврата в меню"
        }
        "3" {
            Write-Host "`nОткрываю браузер для входа в ChatGPT..." -ForegroundColor Cyan
            & $py -m core.auth --provider chatgpt
            Read-Host "`nНажмите ENTER для возврата в меню"
        }
        "4" {
            Write-Host "`nОткрываю браузер для входа в Claude..." -ForegroundColor Cyan
            & $py -m core.auth --provider claude
            Read-Host "`nНажмите ENTER для возврата в меню"
        }
        "5" {
            Write-Host "`nЗапуск быстрого теста..." -ForegroundColor Cyan
            & $py test_client.py
            Read-Host "`nНажмите ENTER для возврата в меню"
        }
        "6" {
            Clear-Host
            & $py -m core.ide_setup --scan
            Write-Host "Выберите вариант настройки:" -ForegroundColor Cyan
            Write-Host "   1. ⚡ Автонастройка всех обнаруженных сред (Безопасно + Бэкап)" -ForegroundColor Green
            Write-Host "   2. 🤖 Настроить Google Antigravity (Все версии: MCP + CLI)" -ForegroundColor Cyan
            Write-Host "   3. 💻 Настроить VS Code + Continue (1 клик)" -ForegroundColor Magenta
            Write-Host "   4. 🎯 Настроить Cursor IDE (MCP Server)" -ForegroundColor Yellow
            Write-Host "   5. 🌊 Настроить Windsurf (Codeium MCP)" -ForegroundColor Blue
            Write-Host "   6. 🔄 Откатить изменения (Автооткат из истории бэкапов)" -ForegroundColor Red
            Write-Host "   7. 📜 Посмотреть историю бэкапов (~/.zero-copy-agent/backups)" -ForegroundColor Gray
            Write-Host "   0. Назад в главное меню" -ForegroundColor White
            $ideChoice = Read-Host "`nВыберите действие [0-7]"
            switch ($ideChoice) {
                "1" { & $py -m core.ide_setup --configure all }
                "2" { & $py -m core.ide_setup --configure antigravity }
                "3" { & $py -m core.ide_setup --configure vscode }
                "4" { & $py -m core.ide_setup --configure cursor }
                "5" { & $py -m core.ide_setup --configure windsurf }
                "6" { & $py -m core.ide_setup --rollback }
                "7" { & $py -m core.ide_setup --history }
            }
            Read-Host "`nНажмите ENTER для возврата в главное меню"
        }
        "7" {
            Write-Host "`nЗагрузка обновления..." -ForegroundColor Cyan
            if (Test-Path "update.ps1") {
                & powershell -NoProfile -ExecutionPolicy Bypass -File .\update.ps1
            } else {
                irm https://raw.githubusercontent.com/solyanni4/zero-copy-agent/main/update.ps1 | iex
            }
            Read-Host "`nНажмите ENTER для возврата в меню"
        }
        "0" {
            exit 0
        }
    }
}
