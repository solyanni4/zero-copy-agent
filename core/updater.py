"""
GitHub Update Checker for zero-copy-agent.
Queries GitHub releases for updates, handles proxies and network interruptions cleanly.
"""
import os
import sys
import json
import time
import logging
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("zcao.updater")

try:
    from core.version import __version__
    CURRENT_VERSION = __version__
except Exception:
    CURRENT_VERSION = "1.0.6"
GITHUB_REPO = "solyanni4/zero-copy-agent"


def check_github_updates(current_version: str = CURRENT_VERSION) -> Dict[str, Any]:
    """
    Checks GitHub releases for latest version.
    Respects HTTPS_PROXY or HTTP_PROXY environment variables if present, or uses direct connection.
    """
    proxy_url = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or None
    
    repos = [GITHUB_REPO]
    latest_release = None
    last_error = ""

    for repo in repos:
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        try:
            handlers = []
            if proxy_url:
                proxy_handler = urllib.request.ProxyHandler({
                    "http": proxy_url,
                    "https": proxy_url
                })
                handlers.append(proxy_handler)

            opener = urllib.request.build_opener(*handlers)
            req = urllib.request.Request(
                api_url,
                headers={
                    "User-Agent": "zero-copy-agent-updater",
                    "Accept": "application/vnd.github.v3+json"
                }
            )
            with opener.open(req, timeout=4.0) as resp:
                if resp.status == 200:
                    latest_release = json.loads(resp.read().decode("utf-8"))
                    break
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}"
        except Exception as e:
            last_error = str(e)

    if not latest_release:
        # Offline / unreachable status
        return {
            "status": "offline",
            "has_update": False,
            "version": current_version,
            "latest_version": current_version,
            "commit": "local",
            "message": "Нет связи с GitHub (проверка не выполнена)",
            "download_url": f"https://github.com/{GITHUB_REPO}/releases"
        }

    tag_name = latest_release.get("tag_name", "").lstrip("v")
    html_url = latest_release.get("html_url", "")
    body = latest_release.get("body", "Плановое обновление стабильности")

    download_url = html_url
    for asset in latest_release.get("assets", []):
        if asset.get("name", "").endswith(".exe"):
            download_url = asset.get("browser_download_url", html_url)
            break

    # Simple semver check
    def parse_ver(v: str):
        try:
            return [int(x) for x in v.split(".") if x.isdigit()]
        except Exception:
            return [0]

    has_update = parse_ver(tag_name) > parse_ver(current_version)

    return {
        "status": "ok",
        "has_update": has_update,
        "version": current_version,
        "latest_version": tag_name or current_version,
        "commit": tag_name or "latest",
        "release_url": html_url,
        "download_url": download_url,
        "release_notes": body[:300] if body else "",
        "message": f"Доступна новая версия v{tag_name}!" if has_update else f"Версия v{current_version} актуальна."
    }


# ------------------------------------------------------------------------
# In-App Self-Update Engine
# ------------------------------------------------------------------------
_update_state: Dict[str, Any] = {
    "status": "idle",  # idle, downloading, applying, done, error
    "percent": 0.0,
    "downloaded_bytes": 0,
    "total_bytes": 0,
    "speed": "",
    "error": "",
    "message": ""
}


def get_update_state() -> Dict[str, Any]:
    """Returns current state of the in-app update process."""
    return dict(_update_state)


def get_current_exe_path() -> Path:
    """Finds real target executable to be updated."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    root_exe = Path(__file__).resolve().parent.parent / "zero-copy-agent.exe"
    if root_exe.exists():
        return root_exe.resolve()
    return Path(sys.executable).resolve()


def start_in_app_update(download_url: str = None) -> Dict[str, Any]:
    """Initiates chunked download and detached swap in background thread."""
    global _update_state
    if _update_state["status"] == "downloading":
        return {"status": "ok", "message": "Обновление уже скачивается"}

    _update_state = {
        "status": "downloading",
        "percent": 0.0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "speed": "0 КБ/с",
        "error": "",
        "message": "Подключение к серверу обновлений..."
    }

    import threading
    threading.Thread(target=_run_update_worker, args=(download_url,), daemon=True).start()
    return {"status": "ok", "message": "Процесс обновления запущен"}


def _run_update_worker(download_url: str = None):
    global _update_state
    import time
    from pathlib import Path

    try:
        # 1. Resolve URL if not supplied
        if not download_url:
            info = check_github_updates()
            download_url = info.get("download_url")
            if not download_url or not info.get("has_update"):
                download_url = info.get("download_url") or "https://github.com/solyanni4/zero-copy-agent/releases/latest"

        if not download_url.endswith(".exe"):
            info = check_github_updates()
            download_url = info.get("download_url")

        # 2. Prepare staging dir in LOCALAPPDATA
        local_app_data = os.getenv("LOCALAPPDATA") or str(Path.home())
        updates_dir = Path(local_app_data) / "zero-copy-agent" / "updates"
        updates_dir.mkdir(parents=True, exist_ok=True)

        target_exe = get_current_exe_path()
        staged_exe = updates_dir / "zero-copy-agent.new.exe"
        part_file = updates_dir / "zero-copy-agent.part"
        backup_exe = updates_dir / "zero-copy-agent.prev.exe"

        # 3. Download via proxy if present
        proxy_url = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or None
        handlers = []
        if proxy_url:
            handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        opener = urllib.request.build_opener(*handlers)

        req = urllib.request.Request(
            download_url,
            headers={
                "User-Agent": "zero-copy-agent-self-updater",
                "Accept": "application/octet-stream"
            }
        )

        _update_state["message"] = "Скачивание новой версии..."
        start_time = time.monotonic()
        last_emit = 0.0

        with opener.open(req, timeout=30.0) as resp:
            total_bytes = int(resp.headers.get("Content-Length", 0) or 75000000)
            _update_state["total_bytes"] = total_bytes
            downloaded = 0

            with open(part_file, "wb") as out:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()

                    if now - last_emit >= 0.1 or downloaded == total_bytes:
                        elapsed = max(now - start_time, 0.001)
                        speed_kbs = (downloaded / 1024) / elapsed
                        pct = round((downloaded / total_bytes) * 100, 1) if total_bytes else 0.0
                        _update_state["percent"] = min(pct, 99.9)
                        _update_state["downloaded_bytes"] = downloaded
                        _update_state["speed"] = f"{speed_kbs:.1f} КБ/с" if speed_kbs < 1024 else f"{(speed_kbs/1024):.2f} МБ/с"
                        _update_state["message"] = f"Скачивание: {_update_state['percent']}% ({_update_state['speed']})"
                        last_emit = now

        # Finalize file
        if staged_exe.exists():
            staged_exe.unlink(missing_ok=True)
        part_file.replace(staged_exe)

        _update_state["percent"] = 100.0
        _update_state["status"] = "applying"
        _update_state["message"] = "Установка обновления и перезапуск..."
        time.sleep(0.5)

        # 4. Create and launch detached updater script
        pid = os.getpid()
        if sys.platform == "win32":
            script_path = updates_dir / "apply_update.ps1"
            ps_script = f"""$ErrorActionPreference = "SilentlyContinue"
$targetPid = {pid}
$targetExe = "{str(target_exe)}"
$stagedExe = "{str(staged_exe)}"
$backupExe = "{str(backup_exe)}"

for ($i = 0; $i -lt 80; $i++) {{
    $p = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
    if (-not $p) {{ break }}
    Start-Sleep -Milliseconds 250
}}

for ($i = 0; $i -lt 40; $i++) {{
    try {{
        $fs = [System.IO.File]::Open($targetExe, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        $fs.Close()
        break
    }} catch {{
        Start-Sleep -Milliseconds 250
    }}
}}

if (Test-Path $targetExe) {{
    Move-Item -Path $targetExe -Destination $backupExe -Force
}}
Move-Item -Path $stagedExe -Destination $targetExe -Force
Start-Process -FilePath $targetExe
"""
            script_path.write_text(ps_script, encoding="utf-8")

            import subprocess
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-WindowStyle",
                    "Hidden",
                    "-File",
                    str(script_path)
                ],
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                close_fds=True
            )
        else:
            # Unix / macOS swap
            script_path = updates_dir / "apply_update.sh"
            sh_script = f"""#!/bin/sh
while kill -0 {pid} 2>/dev/null; do sleep 0.2; done
mv -f "{staged_exe}" "{target_exe}"
chmod +x "{target_exe}"
nohup "{target_exe}" >/dev/null 2>&1 &
"""
            script_path.write_text(sh_script, encoding="utf-8")
            os.chmod(script_path, 0o755)
            import subprocess
            subprocess.Popen(["/bin/sh", str(script_path)], close_fds=True)

        # 5. Terminate current process cleanly
        time.sleep(0.4)
        os._exit(0)

    except Exception as e:
        logger.exception(f"In-app update failed: {e}")
        _update_state["status"] = "error"
        _update_state["error"] = str(e)
        _update_state["message"] = f"Ошибка обновления: {e}"
