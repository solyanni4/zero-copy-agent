"""
Clean Uninstallation & System Cleanup Engine for zero-copy-agent.
Reverts all IDE modifications, removes backups, logs, and configuration state cleanly.
"""
import os
import sys
import shutil
import logging
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger("zcao.uninstaller")


def clean_uninstall_system(keep_backups: bool = False, orchestrator_instance: Any = None) -> Dict[str, Any]:
    """
    Performs full uninstallation:
    1. Reverts all IDE integration configurations back to their clean original states.
    2. Removes ~/.zero-copy-agent configuration and backup directories (unless keep_backups=True).
    3. Cleans %LOCALAPPDATA%/zero-copy-agent caches and logs.
    4. Removes desktop / autostart shortcuts if any exist.
    """
    steps_executed = []
    errors = []

    # 1. Rollback all IDE configurations
    try:
        from core.ide_setup import rollback_target_clean
        for ide_key in ["vscode", "cursor", "windsurf", "antigravity", "claude", "aider", "jetbrains"]:
            ok, msg = rollback_target_clean(ide_key)
            if ok:
                steps_executed.append(f"IDE {ide_key.upper()}: {msg}")
    except Exception as e:
        errors.append(f"Ошибка отката IDE: {e}")

    # 2. Cleanup user directory ~/.zero-copy-agent
    user_app_dir = Path.home() / ".zero-copy-agent"
    if user_app_dir.exists():
        try:
            if keep_backups:
                # Remove everything except backups
                for item in user_app_dir.iterdir():
                    if item.name != "backups":
                        if item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                        else:
                            item.unlink(missing_ok=True)
                steps_executed.append(f"Очищены настройки в {user_app_dir} (бэкапы сохранены)")
            else:
                shutil.rmtree(user_app_dir, ignore_errors=True)
                steps_executed.append(f"Удалена рабочая папка {user_app_dir}")
        except Exception as e:
            errors.append(f"Ошибка удаления {user_app_dir}: {e}")

    # 3. Cleanup local app data directory
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        app_log_dir = Path(local_app_data) / "zero-copy-agent"
        if app_log_dir.exists():
            try:
                shutil.rmtree(app_log_dir, ignore_errors=True)
                steps_executed.append(f"Удалены временные логи в {app_log_dir}")
            except Exception as e:
                errors.append(f"Ошибка удаления логов: {e}")

    # 4. Cleanup any Windows Startup shortcuts
    if sys.platform == "win32" and os.getenv("APPDATA"):
        startup_dir = Path(os.getenv("APPDATA")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        shortcut = startup_dir / "zero-copy-agent.lnk"
        if shortcut.exists():
            try:
                shortcut.unlink()
                steps_executed.append("Удален ярлык из автозагрузки Windows")
            except Exception:
                pass

    # 5. Remove local subscriptions.yaml, database caches and logs in working directory
    try:
        sub_paths = [
            Path.cwd() / "subscriptions.yaml",
            Path(__file__).resolve().parent.parent / "subscriptions.yaml",
            Path.home() / ".zero-copy-agent" / "subscriptions.yaml"
        ]
        for sp in sub_paths:
            if sp.exists():
                sp.unlink(missing_ok=True)
                steps_executed.append(f"Удален файл подписок и моделей: {sp.name}")
    except Exception as e:
        logger.debug(f"Error removing subscriptions: {e}")

    # 6. Wipe in-memory orchestrator subscriptions
    try:
        if orchestrator_instance and hasattr(orchestrator_instance, "subscriptions"):
            orchestrator_instance.subscriptions.accounts = []
            steps_executed.append("Очищен каталог моделей в оперативной памяти")
        else:
            from core.mesh import mesh
            mesh.clear()
            steps_executed.append("Очищен пул моделей в оперативной памяти")
    except Exception:
        pass

    return {
        "status": "ok" if not errors else "partial",
        "steps": steps_executed,
        "errors": errors,
        "message": "Приложение успешно и полностью удалено с компьютера." if not errors else "Удаление завершено с предупреждениями."
    }
