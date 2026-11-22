# scripts/sync_gdrive.ps1
# Синхронизация кодовой базы zero-copy-agent с Google Диском.
# Поддерживает:
# 1. Прямую облачную выгрузку через rclone в целевую папку:
#    https://drive.google.com/drive/folders/15QGMGj3jwkz0BxYL0izu3GfZP09kwfJS
# 2. Локальную синхронизацию через robocopy (Google Drive Desktop), если диск смонтирован или задан ZCA_GDRIVE_DIR.

param (
    [string]$TargetFolderId = "15QGMGj3jwkz0BxYL0izu3GfZP09kwfJS",
    [string]$LocalTargetDir = $env:ZCA_GDRIVE_DIR,
    [switch]$ForceLocal
)

$ErrorActionPreference = "Stop"
$SourceDir = (Resolve-Path "$PSScriptRoot/..").Path

$versionMatch = (Get-Content "$SourceDir/core/version.py" -Raw -ErrorAction SilentlyContinue | Select-String '__version__\s*=\s*"([^"]+)"')
if (-not $versionMatch) {
    $versionMatch = (Get-Content "$SourceDir/core/__init__.py" -Raw -ErrorAction SilentlyContinue | Select-String '__version__\s*=\s*"([^"]+)"')
}
$CurrentVersion = if ($versionMatch) { $versionMatch.Matches.Groups[1].Value } else { "1.1.0" }
$CloudDestination = "gdrive:zero-copy-agent/zero-copy-agent-$CurrentVersion"

# Папки и файлы для исключения из синхронизации
$ExcludeRules = @(
    ".git/**",
    "venv/**",
    ".venv/**",
    "**/__pycache__/**",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".pytest_cache/**",
    "browser_profile*/**",
    "browser_profiles/**",
    "browser_profile/**",
    "dist/**",
    "build/**",
    "*.bak",
    ".env",
    "*.db*",
    "*.log",
    "zero-copy-agent.exe"
)

# Функция локальной синхронизации через robocopy
function Invoke-LocalSync([string]$DestDir) {
    Write-Host ">>> Запуск локальной синхронизации через robocopy..." -ForegroundColor Cyan
    Write-Host "    Источник:   $SourceDir"
    Write-Host "    Назначение: $DestDir"

    if (-not (Test-Path $DestDir)) {
        New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
    }

    $ExcludeDirs = @(".git", "venv", ".venv", "__pycache__", ".pytest_cache", "browser_profile", "browser_profiles", "dist", "build")
    $ExcludeFiles = @(".env", "*.db*", "*.log", "zero-copy-agent.exe", "*.bak", "*.pyc")

    $RoboArgs = @(
        "`"$SourceDir`"",
        "`"$DestDir`"",
        "/MIR",
        "/FFT",
        "/R:1",
        "/W:1",
        "/XD"
    ) + $ExcludeDirs + @("/XF") + $ExcludeFiles

    $Process = Start-Process -FilePath "robocopy.exe" -ArgumentList $RoboArgs -NoNewWindow -Wait -PassThru
    $ExitCode = $Process.ExitCode

    if ($ExitCode -le 7) {
        Write-Host "[OK] Локальная синхронизация успешно завершена (Код robocopy: $ExitCode)." -ForegroundColor Green
        return $true
    } else {
        Write-Error "[ОШИБКА] Сбой robocopy. Код завершения: $ExitCode"
        return $false
    }
}

# Функция облачной синхронизации через rclone
function Invoke-CloudSync() {
    $rcloneCmd = Get-Command rclone -ErrorAction SilentlyContinue
    if (-not $rcloneCmd) {
        return $false
    }

    Write-Host ">>> Проверка подключения rclone к Google Диску..." -ForegroundColor Cyan
    $remoteCheck = & rclone listremotes 2>$null
    if ($remoteCheck -notcontains "gdrive:") {
        Write-Host "    [!] Remote 'gdrive:' не настроен в rclone." -ForegroundColor Yellow
        return $false
    }

    Write-Host ">>> Облачная синхронизация через rclone..." -ForegroundColor Cyan
    Write-Host "    Папка Google Drive ID: $TargetFolderId"
    Write-Host "    Целевой путь: $CloudDestination"

    $FilterFile = [System.IO.Path]::GetTempFileName()
    try {
        $FilterLines = @()
        foreach ($rule in $ExcludeRules) {
            $FilterLines += "- $rule"
        }
        $FilterLines += "+ **"
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllLines($FilterFile, $FilterLines, $utf8NoBom)

        # Выполняем rclone copy в целевую папку Google Диска
        $rcloneArgs = @(
            "copy",
            "$SourceDir",
            "$CloudDestination",
            "--filter-from", "$FilterFile",
            "--stats", "2s",
            "-v"
        )

        Write-Host ">>> Передача файлов..." -ForegroundColor Cyan
        $process = Start-Process -FilePath "rclone.exe" -ArgumentList $rcloneArgs -NoNewWindow -Wait -PassThru
        
        if ($process.ExitCode -eq 0) {
            Write-Host "[OK] Облачная синхронизация с Google Диском завершена успешно!" -ForegroundColor Green
            return $true
        } else {
            Write-Host ""
            Write-Host "============================================================" -ForegroundColor Yellow
            Write-Host " [ВНИМАНИЕ] rclone вернул ошибку прав доступа (Код: $($process.ExitCode))." -ForegroundColor Yellow
            Write-Host " Токен gdrive был зарегистрирован только для чтения (drive.readonly)." -ForegroundColor Yellow
            Write-Host " Чтобы разрешить запись в Google Диск, выполните один раз в терминале:" -ForegroundColor Yellow
            Write-Host ""
            Write-Host "   rclone config reconnect gdrive:" -ForegroundColor White
            Write-Host ""
            Write-Host " Откроется браузер для подтверждения доступа к Google Drive." -ForegroundColor Yellow
            Write-Host "============================================================" -ForegroundColor Yellow
            return $false
        }
    } finally {
        if (Test-Path $FilterFile) {
            Remove-Item $FilterFile -Force -ErrorAction SilentlyContinue
        }
    }
}

# --- Основной цикл выполнения ---

# 1. Если задан явный локальный путь через параметр или $env:ZCA_GDRIVE_DIR
if ($LocalTargetDir -and (Test-Path (Split-Path -Parent $LocalTargetDir -ErrorAction SilentlyContinue))) {
    Invoke-LocalSync -DestDir $LocalTargetDir
    exit 0
}

# 2. Если не включен принудительный локальный режим — пробуем облачный rclone
if (-not $ForceLocal) {
    $cloudSuccess = Invoke-CloudSync
    if ($cloudSuccess) {
        exit 0
    }
}

# 3. Автопоиск смонтированного Google Drive Desktop (только настоящие папки диска, исключая флешки)
$DriveCandidates = @(
    "G:\Мой диск\zero-copy-agent",
    "G:\My Drive\zero-copy-agent",
    "$env:USERPROFILE\Google Drive\zero-copy-agent",
    "$env:USERPROFILE\GoogleDrive\zero-copy-agent",
    "$env:USERPROFILE\Мой диск\zero-copy-agent"
)

$FoundLocal = $null
foreach ($cand in $DriveCandidates) {
    $parent = Split-Path -Parent $cand
    if (Test-Path $parent) {
        # Дополнительная проверка: убедимся, что это не флешка без папки диска
        $driveLetter = (Split-Path -Qualifier $parent).TrimEnd(':')
        $vol = Get-Volume -DriveLetter $driveLetter -ErrorAction SilentlyContinue
        if ($vol -and $vol.DriveType -ne 'Removable') {
            $FoundLocal = $cand
            break
        }
    }
}

if ($FoundLocal) {
    Invoke-LocalSync -DestDir $FoundLocal
    exit 0
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Ссылка на целевую папку Google Диска:" -ForegroundColor White
Write-Host " https://drive.google.com/drive/folders/$TargetFolderId" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Для включения прямой облачной синхронизации выдайте права на запись:"
Write-Host '  rclone config reconnect gdrive:' -ForegroundColor Yellow
Write-Host ""
Write-Host "Либо укажите локальную смонтированную папку:"
Write-Host '  $env:ZCA_GDRIVE_DIR = "C:\Users\...\Google Drive\zero-copy-agent"' -ForegroundColor Yellow
Write-Host "============================================================"
