"""
Build script for zero-copy-agent Desktop Executable.
Uses PyInstaller to compile a single-file portable application for Windows, macOS, and Linux.
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

# Safeguard against encoding issues in Windows CI terminals (cp1252)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DESKTOP_DIR = ROOT_DIR / "desktop"
ASSETS_DIR = DESKTOP_DIR / "assets"
DIST_DIR = ROOT_DIR / "dist"
BUILD_DIR = ROOT_DIR / "build"

def build():
    print("\n" + "=" * 55)
    print("  [*] Building zero-copy-agent Desktop Package")
    print(f"  Platform : {sys.platform}")
    print(f"  Root Dir : {ROOT_DIR}")
    print("=" * 55 + "\n")

    # 1. Генерируем иконку
    from desktop.app_icon import ensure_icon_file
    icon_png = ensure_icon_file()
    print(f"[+] Application icon prepared at: {icon_png}")

    # 2. Проверяем наличие PyInstaller
    try:
        import PyInstaller
    except ImportError:
        print("[!] PyInstaller is not installed. Installing required build tools...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller", "pystray", "pillow", "pyperclip"])

    entry_script = DESKTOP_DIR / "app.py"

    # Формируем флаги PyInstaller
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name=zero-copy-agent",
        f"--paths={ROOT_DIR}",
        "--onedir" if sys.platform == "darwin" else "--onefile",
        f"--add-data={ROOT_DIR / 'config.yaml'}{';.' if sys.platform == 'win32' else ':.'}",
        f"--add-data={ROOT_DIR / 'presets.yaml'}{';.' if sys.platform == 'win32' else ':.'}",
        f"--add-data={ASSETS_DIR}{';desktop/assets' if sys.platform == 'win32' else ':desktop/assets'}",
        f"--add-data={ROOT_DIR / 'static'}{';static' if sys.platform == 'win32' else ':static'}",
        "--collect-all=core",
        "--collect-all=playwright",
        "--collect-all=patchright",
        "--collect-all=pystray",
        "--collect-all=webview",
        "--collect-all=pythonnet",
        "--collect-all=clr_loader",
        "--collect-all=bottle",
        "--collect-all=uvicorn",
        "--collect-all=fastapi",
        "--collect-all=starlette",
        "--collect-all=PIL",
        "--collect-all=httpx",
        "--hidden-import=main",
        "--hidden-import=desktop.app",
        "--hidden-import=desktop.tray_app",
        "--hidden-import=webview",
        "--hidden-import=pythonnet",
        "--hidden-import=clr_loader",
        "--hidden-import=core",
        "--hidden-import=core.ui_api",
        "--hidden-import=core.storage",
        "--hidden-import=core.compression",
        "--hidden-import=core.shield",
        "--hidden-import=core.mcp_server",
        "--hidden-import=core.ide_setup",
        "--hidden-import=core.hardware",
        "--hidden-import=core.updater",
        "--hidden-import=core.uninstaller",
        "--hidden-import=core.browser_session",
        "--hidden-import=core.providers",
        "--hidden-import=core.drivers.ollama",
        "--hidden-import=core.drivers.mock",
        "--hidden-import=core.drivers.deepseek",
        "--hidden-import=core.drivers.qwen",
        "--hidden-import=core.drivers.grok",
        "--hidden-import=core.mesh",
        "--hidden-import=core.orchestrator",
        "--hidden-import=core.solutions_db",
        "--hidden-import=core.prompt_compressor",
        "--hidden-import=core.orchestration_presets",
        "--hidden-import=core.hardware_advisor",
        "--hidden-import=sqlite3",
        "--hidden-import=pystray",
        "--hidden-import=PIL",
        "--hidden-import=uvicorn",
        "--hidden-import=fastapi",
        "--hidden-import=fastapi.staticfiles",
        "--hidden-import=playwright",
        "--hidden-import=patchright",
        "--hidden-import=patchright.async_api",
        "--hidden-import=patchright.sync_api",
        "--hidden-import=pyee",
        "--hidden-import=pyperclip",
        "--hidden-import=yaml",
        "--hidden-import=dotenv",
        "--hidden-import=httpx",
    ]

    # Для Windows не показываем консоль при запуске из трея
    if sys.platform == "win32":
        cmd.append("--noconsole")
        cmd.append("--hidden-import=pystray._win32")
        ico_path = ASSETS_DIR / "icon.ico"
        if not ico_path.exists():
            from PIL import Image
            img = Image.open(icon_png)
            img.save(ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        cmd.append(f"--icon={ico_path}")
    elif sys.platform == "darwin":
        cmd.append("--windowed")
        cmd.append(f"--icon={icon_png}")

    cmd.append(str(entry_script))

    print(f"[+] Executing PyInstaller command:\n{' '.join(cmd)}\n")
    subprocess.check_call(cmd, cwd=str(ROOT_DIR))

    target_exe = DIST_DIR / ("zero-copy-agent.exe" if sys.platform == "win32" else "zero-copy-agent")
    if target_exe.exists():
        root_exe = ROOT_DIR / target_exe.name
        try:
            shutil.copy2(target_exe, root_exe)
            print(f"  [+] Copied binary to project root: {root_exe}")
        except Exception as e:
            print(f"  [!] Could not copy to root: {e}")

    print("\n" + "=" * 55)
    print("  [OK] Build completed successfully!")
    print(f"  Artifacts located in: {DIST_DIR}")
    print("=" * 55 + "\n")

if __name__ == "__main__":
    build()
