import pytest
from pathlib import Path
from core.updater import get_current_exe_path, get_update_state, check_github_updates

def test_get_current_exe_path():
    p = get_current_exe_path()
    assert isinstance(p, Path)
    assert p.name.endswith(".exe") or "python" in p.name.lower()

def test_get_update_state():
    state = get_update_state()
    assert isinstance(state, dict)
    assert "status" in state
    assert "percent" in state
    assert "message" in state

def test_check_github_updates_structure():
    info = check_github_updates("1.0.0")
    assert isinstance(info, dict)
    assert info["status"] in ("ok", "offline")
    assert "has_update" in info
    assert "version" in info
    assert "download_url" in info

def test_generate_windows_update_bat():
    from core.updater import generate_windows_update_bat
    target_exe = Path(r"C:\Program Files\Zero Copy Agent\zero-copy-agent.exe")
    staged_exe = Path(r"C:\Users\Test User\AppData\Local\zero-copy-agent\updates\zero-copy-agent.new.exe")
    backup_exe = Path(r"C:\Program Files\Zero Copy Agent\zero-copy-agent.prev.exe")
    log_file = Path(r"C:\Program Files\Zero Copy Agent\update.log")
    test_pid = 9876

    bat_script = generate_windows_update_bat(
        target_exe=target_exe,
        staged_exe=staged_exe,
        backup_exe=backup_exe,
        pid=test_pid,
        log_file=log_file
    )

    # 1. Check PID waiting
    assert f'set "TARGET_PID={test_pid}"' in bat_script
    assert 'tasklist /FI "PID eq %TARGET_PID%"' in bat_script
    assert "timeout /t 1 /nobreak >nul" in bat_script

    # 2. Check quoted paths with spaces
    assert f'set "TARGET_EXE={str(target_exe.resolve())}"' in bat_script
    assert f'set "WORK_DIR={str(target_exe.parent.resolve())}"' in bat_script
    assert f'set "LOG_FILE={str(log_file.resolve())}"' in bat_script

    # 3. Check start command with --post-update
    assert 'start "" /D "%WORK_DIR%" "%TARGET_EXE%" --post-update' in bat_script

    # 4. Check move/replace with retry
    assert 'move /Y "%STAGED_EXE%" "%TARGET_EXE%"' in bat_script

    # 5. Check self-cleanup and logging
    assert 'del "%~f0" >nul 2>&1' in bat_script
    assert '>> "%LOG_FILE%"' in bat_script
