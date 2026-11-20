# ==============================================================================
# ⚡ Zero-Copy Agent — Автоматическая настройка VS Code + Continue (1 клик)
# ==============================================================================
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "   ⚡ Настройка Visual Studio Code + Continue в 1 клик           " -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Поиск и установка расширения Continue
$codeCmd = Get-Command code -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue
if (-not $codeCmd) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Microsoft VS Code\bin\code.cmd",
        "$env:ProgramFiles\Microsoft VS Code\bin\code.cmd",
        "${env:ProgramFiles(x86)}\Microsoft VS Code\bin\code.cmd"
    )
    foreach ($cand in $candidates) {
        if (Test-Path $cand) { $codeCmd = $cand; break }
    }
}

if ($codeCmd) {
    Write-Host "[1/3] Найдена установка VS Code: $codeCmd" -ForegroundColor Cyan
    Write-Host "      Проверяю и устанавливаю расширение Continue..." -ForegroundColor Gray
    try {
        & $codeCmd --install-extension Continue.continue --force
        Write-Host "      ✅ Расширение Continue успешно установлено!" -ForegroundColor Green
    } catch {
        Write-Host "      ⚠️ Не удалось автоматически установить расширение через CLI. Установите 'Continue' в магазине расширений VS Code вручную." -ForegroundColor Yellow
    }
} else {
    Write-Host "[1/3] ℹ️ VS Code CLI не найден в PATH. Если VS Code установлен, расширение Continue можно поставить через Ctrl+Shift+X." -ForegroundColor Yellow
}

# 2. Создание каталога .continue
$continueDir = Join-Path $HOME ".continue"
if (-not (Test-Path $continueDir)) {
    New-Item -ItemType Directory -Path $continueDir -Force | Out-Null
}

# 3. Запись конфигурации моделей Continue 2.0
$yamlConfig = @"
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
"@

$yamlPath = Join-Path $continueDir "config.yaml"
# Бэкап старого конфига при наличии
if (Test-Path $yamlPath) {
    $bakPath = Join-Path $continueDir "config.yaml.bak"
    Copy-Item $yamlPath $bakPath -Force
}

[System.IO.File]::WriteAllText($yamlPath, $yamlConfig, [System.Text.Encoding]::UTF8)

Write-Host "`n[2/3] Конфигурация Continue 2.0 записана в $yamlPath" -ForegroundColor Cyan
Write-Host "      Добавлены модели: Gemini Advanced, ChatGPT Plus (GPT-4o), Claude 3.7 Sonnet" -ForegroundColor Gray

Write-Host "`n[3/3] ✅ НАСТРОЙКА ЗАВЕРШЕНА УСПЕШНО!" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " Как начать работу:" -ForegroundColor White
Write-Host " 1. Убедитесь, что шлюз запущен: .\start.bat (пункт 1)" -ForegroundColor Yellow
Write-Host " 2. В VS Code нажмите: Ctrl + L" -ForegroundColor Yellow
Write-Host " 3. В левой панели выберите: 'Gemini Advanced (Zero-Copy)'" -ForegroundColor Yellow
Write-Host " 4. Пишите промпты без ограничений и без платы за токены!" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host ""
