# scripts/sync_gdrive.ps1
# Скрипт синхронизации кодовой базы zero-copy-agent с локальной зеркальной папкой Google Диска

$ErrorActionPreference = "Stop"

$SourceDir = (Resolve-Path "$PSScriptRoot/..").Path
$TargetDir = $env:ZCA_GDRIVE_DIR

if (-not $TargetDir) {
    # Автопоиск стандартных путей Google Drive Desktop
    $CandidatePaths = @(
        "G:\Мой диск\zero-copy-agent",
        "G:\My Drive\zero-copy-agent",
        "G:\zero-copy-agent",
        "$env:USERPROFILE\Google Drive\zero-copy-agent",
        "$env:USERPROFILE\GoogleDrive\zero-copy-agent",
        "$env:USERPROFILE\Мой диск\zero-copy-agent"
    )

    foreach ($Candidate in $CandidatePaths) {
        $Parent = Split-Path -Parent $Candidate
        if (Test-Path $Parent) {
            $TargetDir = $Candidate
            break
        }
    }
}

if (-not $TargetDir) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Yellow
    Write-Host " [ИНФО] Локальный путь к Google Диску не обнаружен." -ForegroundColor Yellow
    Write-Host "============================================================" -ForegroundColor Yellow
    Write-Host "Для запуска синхронизации задайте переменную окружения ZCA_GDRIVE_DIR:"
    Write-Host ""
    Write-Host '  $env:ZCA_GDRIVE_DIR = "G:\Мой диск\zero-copy-agent"' -ForegroundColor Cyan
    Write-Host '  .\scripts\sync_gdrive.ps1' -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Либо укажите целевую папку параметром при вызове."
    Write-Host "============================================================"
    exit 0
}

Write-Host ">>> Запуск синхронизации с Google Диском..." -ForegroundColor Cyan
Write-Host "    Источник:   $SourceDir"
Write-Host "    Назначение: $TargetDir"

if (-not (Test-Path $TargetDir)) {
    New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
}

$ExcludeDirs = @(".git", "venv", ".venv", "__pycache__", ".pytest_cache", "browser_profile", "dist", "build")
$ExcludeFiles = @(".env", "*.db", "*.db-wal", "*.db-shm", "*.log", "zero-copy-agent.exe")

$RoboArgs = @(
    "`"$SourceDir`"",
    "`"$TargetDir`"",
    "/MIR",
    "/FFT",
    "/R:1",
    "/W:1",
    "/XD"
) + $ExcludeDirs + @("/XF") + $ExcludeFiles

Write-Host ">>> Выполняется robocopy..."
$Process = Start-Process -FilePath "robocopy.exe" -ArgumentList $RoboArgs -NoNewWindow -Wait -PassThru
$ExitCode = $Process.ExitCode

# Коды возврата robocopy: 0-7 - успех (0: нет изменений, 1-7: файлы скопированы/обновлены), >=8 - ошибка
if ($ExitCode -le 7) {
    Write-Host "[OK] Синхронизация с Google Диском успешно завершена! (Код: $ExitCode)" -ForegroundColor Green
} else {
    Write-Error "[ОШИБКА] Ошибка при выполнении robocopy. Код завершения: $ExitCode"
}
