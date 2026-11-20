# ==============================================================================
# ⚡ Zero-Copy Agent — Universal 1-Click Installer (Windows PowerShell)
# ==============================================================================
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "   ⚡ ZERO-COPY AGENT — Автоматическая установка для Windows      " -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Cyan

# 1. Поиск рабочего Python
function Find-WorkingPython {
    $candidates = @("py", "python", "python3")
    $commonPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\Program Files\Python314\python.exe",
        "C:\Program Files\Python312\python.exe"
    )
    foreach ($cp in $commonPaths) {
        if (Test-Path $cp) { $candidates += $cp }
    }
    foreach ($cmd in $candidates) {
        try {
            $testOutput = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($testOutput -and ($testOutput -match "^3\.(1[0-9]|[2-9][0-9])")) {
                return $cmd
            }
        } catch { }
    }
    return $null
}

$pyCmd = Find-WorkingPython
if (-not $pyCmd) {
    Write-Host "⚙ Установка Python 3.12..." -ForegroundColor Yellow
    winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements --silent
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    $pyCmd = Find-WorkingPython
}
Write-Host "✓ Обнаружен рабочий Python: $pyCmd" -ForegroundColor Green

# 2. Каталог проекта и обновление файлов
$targetDir = "$HOME\zero-copy-agent"
if (-not (Test-Path $targetDir)) {
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
}
Set-Location $targetDir

Write-Host "⬇ Загрузка свежих файлов проекта с GitHub..." -ForegroundColor Blue
$zipPath = "$env:TEMP\zero-copy-agent.zip"
$extractDir = "$env:TEMP\zca_extracted"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }

Invoke-WebRequest -Uri "https://github.com/solyanni4/zero-copy-agent/archive/refs/heads/main.zip" -OutFile $zipPath
Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
Remove-Item $zipPath -Force

# Сохранение пользовательских конфигов при повторной установке/обновлении
$savedConfig = $null
$savedSubs = $null
if (Test-Path "$targetDir\config.yaml") { $savedConfig = Get-Content "$targetDir\config.yaml" -Raw }
if (Test-Path "$targetDir\subscriptions.yaml") { $savedSubs = Get-Content "$targetDir\subscriptions.yaml" -Raw }

Copy-Item -Path "$extractDir\zero-copy-agent-main\*" -Destination $targetDir -Recurse -Force
Remove-Item -Recurse -Force $extractDir

if ($savedConfig) { Set-Content "$targetDir\config.yaml" $savedConfig -Encoding UTF8 }
if ($savedSubs) { Set-Content "$targetDir\subscriptions.yaml" $savedSubs -Encoding UTF8 }
Write-Host "✓ Файлы проекта обновлены до актуальной версии." -ForegroundColor Green

# 3. Виртуальное окружение
$venvPython = "$targetDir\venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "⚙ Создание изолированного виртуального окружения (venv)..." -ForegroundColor Yellow
    & $pyCmd -m venv venv
}

# 4. Установка библиотек
Write-Host "📦 Проверка библиотек..." -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip -q
& $venvPython -m pip install -r requirements.txt -q

# 5. Playwright
Write-Host "🌐 Проверка браузерного движка..." -ForegroundColor Yellow
& $venvPython -m playwright install chromium

# 6. Автоматическая настройка VS Code + Continue
Write-Host "`n🔌 Автоматическая настройка интеграции с VS Code Continue..." -ForegroundColor Cyan
if (Test-Path "$targetDir\setup_vscode.ps1") {
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$targetDir\setup_vscode.ps1"
}

Write-Host "`n🎉 ВСЕ ГОТОВО К РАБОТЕ!" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Запуск панели управления..." -ForegroundColor Yellow
Write-Host "=================================================================" -ForegroundColor Cyan

# Нативный запуск start.ps1
& "$targetDir\start.ps1"
