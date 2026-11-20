# ==============================================================================
# Zero-Copy Agent Safe Auto-Updater
# ==============================================================================
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "Обновление zero-copy-agent до актуальной версии..." -ForegroundColor Cyan

$targetDir = $PSScriptRoot
if (-not $targetDir) { $targetDir = (Get-Location).Path }
Set-Location $targetDir

# 1. Резервное копирование пользовательских конфигураций
$backupDir = Join-Path $targetDir ".config_backup"
if (-not (Test-Path $backupDir)) { New-Item -ItemType Directory -Path $backupDir -Force | Out-Null }

if (Test-Path "$targetDir\config.yaml") {
    Copy-Item "$targetDir\config.yaml" "$backupDir\config.yaml.bak" -Force
    Write-Host "  -> Создан бэкап config.yaml" -ForegroundColor Gray
}
if (Test-Path "$targetDir\subscriptions.yaml") {
    Copy-Item "$targetDir\subscriptions.yaml" "$backupDir\subscriptions.yaml.bak" -Force
    Write-Host "  -> Создан бэкап subscriptions.yaml" -ForegroundColor Gray
}

# 2. Обновление кода
if (Test-Path "$targetDir\.git") {
    Write-Host "  -> Обновление через Git..." -ForegroundColor Cyan
    try {
        git pull origin main
    } catch {
        Write-Host "  ⚠️ Ошибка git pull, пробую альтернативное обновление..." -ForegroundColor Yellow
    }
} else {
    Write-Host "  -> Загрузка актуального архива с GitHub..." -ForegroundColor Cyan
    $zipPath = "$env:TEMP\zca_update.zip"
    $extractDir = "$env:TEMP\zca_update_extracted"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }

    try {
        Invoke-WebRequest -Uri "https://github.com/solyanni4/zero-copy-agent/archive/refs/heads/main.zip" -OutFile $zipPath -UseBasicParsing
        Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

        # Копируем всё с сохранением пользовательских конфигов
        $sourceDir = "$extractDir\zero-copy-agent-main"
        Get-ChildItem -Path $sourceDir -Recurse | ForEach-Object {
            $relPath = $_.FullName.Substring($sourceDir.Length + 1)
            $destPath = Join-Path $targetDir $relPath

            # Не перезаписывать пользовательские конфиги, если они уже есть
            if (($relPath -eq "config.yaml" -or $relPath -eq "subscriptions.yaml") -and (Test-Path $destPath)) {
                # пропускаем перезапись
            } else {
                if ($_.PSIsContainer) {
                    if (-not (Test-Path $destPath)) { New-Item -ItemType Directory -Path $destPath -Force | Out-Null }
                } else {
                    Copy-Item $_.FullName $destPath -Force
                }
            }
        }
    } finally {
        if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
        if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
    }
}

# 3. Обновление зависимостей в venv
$venvPy = Join-Path $targetDir "venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    Write-Host "  -> Проверка и обновление зависимостей..." -ForegroundColor Cyan
    & $venvPy -m pip install -r requirements.txt --quiet --upgrade
}

Write-Host "`n✓ zero-copy-agent успешно обновлён до последней версии!" -ForegroundColor Green
