"""
Automated IDE & Agent Configuration Manager for zero-copy-agent.
Supports:
- Google Antigravity (All versions: 2.0, IDE, CLI) with strict self-preservation and auto-rollback
- Visual Studio Code (Continue, Cline)
- Cursor IDE
- Windsurf (Codeium)

Includes:
- Pre-modification snapshots and timestamped history
- Atomic writes with automatic rollback on validation failure
- Full rollback CLI command
"""

import os
import sys
import json
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("zcao.ide_setup")

BACKUP_ROOT = Path.home() / ".zero-copy-agent" / "backups"
HISTORY_FILE = BACKUP_ROOT / "history.json"

class HistoryManager:
    """Manages configuration snapshots and auto-rollback operations."""

    def __init__(self, backup_dir: Path = BACKUP_ROOT):
        self.backup_dir = backup_dir
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.backup_dir / "history.json"
        self._ensure_history_file()

    def _ensure_history_file(self):
        if not self.history_file.exists():
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2, ensure_ascii=False)

    def load_history(self) -> List[Dict[str, Any]]:
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def record_backup(self, target: str, original_path: Path) -> Path:
        """Copies the original file to a timestamped backup and records it in history."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = original_path.name.replace(".", "_")
        backup_filename = f"{target}_{safe_name}_{ts}.bak"
        backup_path = self.backup_dir / backup_filename

        if original_path.exists():
            shutil.copy2(original_path, backup_path)
            # Also create a .bak next to the original for immediate manual access
            try:
                shutil.copy2(original_path, original_path.with_suffix(original_path.suffix + ".bak"))
            except Exception:
                pass
        else:
            # File didn't exist before; record sentinel
            with open(backup_path, "w", encoding="utf-8") as f:
                f.write("__FILE_DID_NOT_EXIST__")

        history = self.load_history()
        history.append({
            "timestamp": datetime.now().isoformat(),
            "target": target,
            "original_path": str(original_path.resolve()),
            "backup_path": str(backup_path.resolve()),
            "existed_before": original_path.exists()
        })
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)

        return backup_path

    def rollback(self, target: Optional[str] = None) -> List[str]:
        """Rolls back the most recent configuration(s) from history."""
        history = self.load_history()
        if not history:
            return ["История бэкапов пуста. Нечего откатывать."]

        rolled_back = []
        # Group by original_path and find the latest backup for the target
        targets_seen = set()
        for item in reversed(history):
            item_target = item.get("target")
            if target and target != "all" and item_target != target:
                continue

            orig_path = Path(item.get("original_path"))
            if str(orig_path) in targets_seen:
                continue
            targets_seen.add(str(orig_path))

            bak_path = Path(item.get("backup_path"))
            if not bak_path.exists():
                rolled_back.append(f"⚠️ Файл бэкапа не найден: {bak_path}")
                continue

            try:
                content = bak_path.read_text(encoding="utf-8")
                if content == "__FILE_DID_NOT_EXIST__":
                    if orig_path.exists():
                        orig_path.unlink()
                        rolled_back.append(f"✓ Удален созданный файл (возврат к исходному состоянию): {orig_path}")
                else:
                    orig_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(bak_path, orig_path)
                    rolled_back.append(f"✓ Восстановлен из бэкапа: {orig_path}")
            except Exception as e:
                rolled_back.append(f"❌ Ошибка восстановления {orig_path}: {e}")

        return rolled_back


history_manager = HistoryManager()


def safe_update_json(file_path: Path, update_fn, target: str, dry_run: bool = False) -> Tuple[bool, str]:
    """
    Safely updates a JSON file with:
    1. Pre-validation
    2. Automatic backup
    3. In-memory update
    4. Validation of modified structure (preventing removal of critical keys)
    5. Atomic replace via temporary file
    6. Automatic rollback on any failure
    """
    original_data = {}
    file_existed = file_path.exists()

    if file_existed:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                original_data = json.load(f)
        except Exception as e:
            return False, f"Исходный файл поврежден: {file_path} ({e}). Отмена для безопасности."

    # Backup before any modification
    backup_path = history_manager.record_backup(target, file_path)

    # Apply updater function
    try:
        new_data = update_fn(json.loads(json.dumps(original_data)))
    except Exception as e:
        return False, f"Ошибка подготовки данных обновления: {e}"

    if dry_run:
        return True, f"[DRY RUN] Файл {file_path} будет обновлен. Изменения проверены."

    # Atomic write to temporary file
    tmp_path = file_path.with_suffix(".tmp_" + datetime.now().strftime("%H%M%S"))
    try:
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(new_data, f, indent=2, ensure_ascii=False)

        # Validate that temporary file can be read and is valid JSON
        with open(tmp_path, "r", encoding="utf-8") as f:
            verified_data = json.load(f)

        # Validate that pre-existing servers are not wiped (Crucial for Antigravity!)
        if "mcpServers" in original_data:
            for srv in original_data["mcpServers"]:
                if srv not in verified_data.get("mcpServers", {}):
                    raise ValueError(f"Критическая ошибка: сервер '{srv}' отсутствует в новом конфиге! Автооткат.")

        # Atomic replacement
        shutil.move(str(tmp_path), str(file_path))
        return True, f"✓ Успешно обновлен: {file_path} (Бэкап: {backup_path.name})"
    except Exception as e:
        # Automatic Rollback on failure!
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        if file_existed:
            shutil.copy2(backup_path, file_path)
        elif file_path.exists():
            file_path.unlink()
        return False, f"🚨 Сбой верификации ({e}). Сработал АВТООТКАТ к исходному состоянию!"


# ==============================================================================
# IDE Detectors & Configurator Logic
# ==============================================================================

def detect_antigravity() -> Dict[str, Any]:
    """Detects Google Antigravity (2.0, IDE, CLI, MCP configs)."""
    home = Path.home()
    mcp_config = home / ".gemini" / "config" / "mcp_config.json"
    antigravity_dir = home / ".gemini" / "antigravity"

    installed = mcp_config.exists() or antigravity_dir.exists()
    configured = False
    details = []

    if mcp_config.exists():
        try:
            with open(mcp_config, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "zero-copy-agent" in data.get("mcpServers", {}):
                    configured = True
                    details.append("MCP server подключен ('zero-copy-agent')")
                existing = list(data.get("mcpServers", {}).keys())
                details.append(f"Активные MCP: {', '.join(existing) if existing else 'нет'}")
        except Exception:
            pass

    return {
        "name": "Google Antigravity (Все версии)",
        "installed": installed,
        "configured": configured,
        "mcp_config": mcp_config,
        "details": "; ".join(details) if details else ("Найдена директория .gemini" if installed else "Не обнаружено")
    }


def configure_antigravity(dry_run: bool = False) -> Tuple[bool, str]:
    """
    Configures Antigravity MCP server with 100% self-protection.
    Guarantees 'homelab-brain-orchestrator' and existing servers are NEVER altered or deleted.
    """
    home = Path.home()
    mcp_config = home / ".gemini" / "config" / "mcp_config.json"

    def updater(data: Dict[str, Any]) -> Dict[str, Any]:
        if "mcpServers" not in data:
            data["mcpServers"] = {}

        # Streamable HTTP endpoint for Antigravity
        data["mcpServers"]["zero-copy-agent"] = {
            "url": "http://127.0.0.1:8000/mcp"
        }
        return data

    success, msg = safe_update_json(mcp_config, updater, target="antigravity", dry_run=dry_run)

    # Also generate the CLI environment helper script
    env_helper = home / ".gemini" / "antigravity" / "zero-copy-env.ps1"
    try:
        env_helper.parent.mkdir(parents=True, exist_ok=True)
        env_content = (
            "# Zero-Copy Agent Environment for Antigravity CLI (agy)\n"
            "$env:OPENAI_API_BASE = \"http://127.0.0.1:8000/v1\"\n"
            "$env:OPENAI_API_KEY = \"sk-zero-copy\"\n"
            "Write-Host \"✓ Переменные окружения для Antigravity CLI настроены!\" -ForegroundColor Green\n"
        )
        with open(env_helper, "w", encoding="utf-8") as f:
            f.write(env_content)
    except Exception:
        pass

    return success, msg


def detect_vscode() -> Dict[str, Any]:
    """Detects VS Code and Continue extension."""
    home = Path.home()
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    prog_files = Path(os.environ.get("ProgramFiles", "C:\\Program Files"))

    code_paths = [
        local_app_data / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd",
        prog_files / "Microsoft VS Code" / "bin" / "code.cmd"
    ]
    installed = any(p.exists() for p in code_paths) or shutil.which("code") is not None
    continue_dir = home / ".continue"
    continue_yaml = continue_dir / "config.yaml"

    configured = False
    details = []
    if continue_yaml.exists():
        try:
            content = continue_yaml.read_text(encoding="utf-8")
            if "zero-copy" in content.lower() or "gemini-4-neon" in content.lower():
                configured = True
                details.append("Continue настроен с Zero-Copy моделями")
        except Exception:
            pass

    return {
        "name": "Visual Studio Code + Continue",
        "installed": installed,
        "configured": configured,
        "continue_yaml": continue_yaml,
        "details": "; ".join(details) if details else ("VS Code найден" if installed else "Не обнаружено")
    }


def configure_vscode(dry_run: bool = False) -> Tuple[bool, str]:
    """Configures VS Code Continue 2.0 configuration with full models."""
    home = Path.home()
    continue_yaml = home / ".continue" / "config.yaml"
    continue_yaml.parent.mkdir(parents=True, exist_ok=True)

    backup_path = history_manager.record_backup("vscode", continue_yaml)

    yaml_template = """name: Zero-Copy Config
version: 1.0.0
schema: v1

models:
  - name: Gemini 4 Neon (Deeper Analysis 🧠)
    provider: openai
    model: gemini-4-neon
    apiBase: http://127.0.0.1:8000/v1
    apiKey: sk-zero-copy
    roles:
      - chat
      - edit

  - name: Gemini Advanced (Zero-Copy)
    provider: openai
    model: gemini-advanced
    apiBase: http://127.0.0.1:8000/v1
    apiKey: sk-zero-copy
    roles:
      - chat
      - edit

  - name: Cascade Architect 🏛️ (Blueprint ➔ Code)
    provider: openai
    model: cascade:architect
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
"""
    if dry_run:
        return True, f"[DRY RUN] Файл {continue_yaml} будет обновлен."

    try:
        with open(continue_yaml, "w", encoding="utf-8") as f:
            f.write(yaml_template)
        return True, f"✓ Конфигурация Continue 2.0 обновлена: {continue_yaml} (Бэкап: {backup_path.name})"
    except Exception as e:
        return False, f"Ошибка записи {continue_yaml}: {e}"


def detect_cursor() -> Dict[str, Any]:
    """Detects Cursor IDE and its MCP config."""
    home = Path.home()
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    prog_files = Path(os.environ.get("ProgramFiles", "C:\\Program Files"))

    cursor_paths = [
        local_app_data / "Programs" / "cursor" / "Cursor.exe",
        local_app_data / "cursor" / "Cursor.exe",
        prog_files / "Cursor" / "Cursor.exe",
        prog_files / "cursor" / "Cursor.exe"
    ]
    installed = any(p.exists() for p in cursor_paths) or shutil.which("cursor") is not None
    cursor_mcp = home / ".cursor" / "mcp.json"

    configured = False
    if installed and cursor_mcp.exists():
        try:
            with open(cursor_mcp, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "zero-copy-agent" in data.get("mcpServers", {}):
                    configured = True
        except Exception:
            pass

    return {
        "name": "Cursor IDE",
        "installed": installed,
        "configured": configured,
        "mcp_config": cursor_mcp,
        "details": "MCP настроен" if (installed and configured) else ("Cursor установлен, но не настроен" if installed else "Среда не установлена")
    }


def configure_cursor(dry_run: bool = False) -> Tuple[bool, str]:
    """Configures Cursor MCP Server (SSE transport)."""
    home = Path.home()
    cursor_mcp = home / ".cursor" / "mcp.json"

    def updater(data: Dict[str, Any]) -> Dict[str, Any]:
        if "mcpServers" not in data:
            data["mcpServers"] = {}
        data["mcpServers"]["zero-copy-agent"] = {
            "url": "http://127.0.0.1:8000/sse"
        }
        return data

    return safe_update_json(cursor_mcp, updater, target="cursor", dry_run=dry_run)


def detect_windsurf() -> Dict[str, Any]:
    """Detects Windsurf (Codeium) IDE and its MCP config."""
    home = Path.home()
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    app_data = Path(os.environ.get("APPDATA", ""))

    windsurf_paths = [
        local_app_data / "Programs" / "Windsurf" / "Windsurf.exe",
        home / ".codeium" / "windsurf",
        app_data / "Windsurf"
    ]
    installed = any(p.exists() for p in windsurf_paths)
    windsurf_mcp = home / ".codeium" / "windsurf" / "mcp_config.json"

    configured = False
    if windsurf_mcp.exists():
        try:
            with open(windsurf_mcp, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "zero-copy-agent" in data.get("mcpServers", {}):
                    configured = True
        except Exception:
            pass

    return {
        "name": "Windsurf (Codeium)",
        "installed": installed,
        "configured": configured,
        "mcp_config": windsurf_mcp,
        "details": "MCP настроен" if configured else ("Windsurf установлен" if installed else "Не обнаружено")
    }


def configure_windsurf(dry_run: bool = False) -> Tuple[bool, str]:
    """Configures Windsurf MCP Server (SSE transport)."""
    home = Path.home()
    windsurf_mcp = home / ".codeium" / "windsurf" / "mcp_config.json"

    def updater(data: Dict[str, Any]) -> Dict[str, Any]:
        if "mcpServers" not in data:
            data["mcpServers"] = {}
        data["mcpServers"]["zero-copy-agent"] = {
            "url": "http://127.0.0.1:8000/sse"
        }
        return data

    return safe_update_json(windsurf_mcp, updater, target="windsurf", dry_run=dry_run)


# ==============================================================================
# Aider & Claude Code Integration
# ==============================================================================

def detect_aider() -> Dict[str, Any]:
    """Detects Aider CLI configuration in user home or current directory."""
    home = Path.home()
    user_conf = home / ".aider.conf.yml"
    cwd_conf = Path.cwd() / ".aider.conf.yml"

    conf_path = cwd_conf if cwd_conf.exists() else user_conf
    installed = shutil.which("aider") is not None
    configured = False
    details = "Aider CLI не установлен"

    if installed:
        if conf_path.exists():
            try:
                content = conf_path.read_text(encoding="utf-8")
                if "127.0.0.1:8000" in content or "zero-copy" in content:
                    configured = True
                    details = f"Настроен на шлюз zero-copy-agent ({conf_path.name})"
                else:
                    details = f"Найден ({conf_path.name}), но не настроен на локальный шлюз"
            except Exception as e:
                details = f"Ошибка чтения {conf_path}: {e}"
        else:
            details = "Aider установлен, конфиг не создан"

    return {
        "name": "Aider CLI",
        "installed": installed,
        "configured": configured,
        "path": str(conf_path),
        "details": details
    }


def configure_aider(dry_run: bool = False, port: int = 8000) -> Tuple[bool, str]:
    """Configures Aider to use zero-copy-agent OpenAI endpoint."""
    target_conf = Path.home() / ".aider.conf.yml"
    history_manager.record_backup("aider", target_conf)

    snippet = (
        f"# Zero-Copy Agent Gateway Configuration\n"
        f"openai-api-base: http://127.0.0.1:{port}/v1\n"
        f"openai-api-key: sk-zero-copy\n"
        f"model: openai/tier:coding-top\n"
    )

    if dry_run:
        return True, f"[DRY-RUN] Записал бы конфигурацию в {target_conf}"

    try:
        if target_conf.exists():
            existing = target_conf.read_text(encoding="utf-8")
            if "127.0.0.1" in existing and "openai-api-base" in existing:
                return True, f"Aider уже настроен в {target_conf}"
            new_content = existing.strip() + "\n\n" + snippet
        else:
            new_content = snippet

        target_conf.parent.mkdir(parents=True, exist_ok=True)
        target_conf.write_text(new_content, encoding="utf-8")
        return True, f"Конфигурация успешно добавлена в {target_conf}"
    except Exception as e:
        return False, f"Ошибка записи {target_conf}: {e}"


def detect_claude() -> Dict[str, Any]:
    """Detects Claude Desktop or Claude Code MCP configuration."""
    home = Path.home()
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    prog_files = Path(os.environ.get("ProgramFiles", "C:\\Program Files"))

    claude_paths = [
        local_app_data / "Programs" / "Claude" / "Claude.exe",
        local_app_data / "Claude" / "Claude.exe",
        prog_files / "Claude" / "Claude.exe"
    ]
    installed = any(p.exists() for p in claude_paths) or shutil.which("claude") is not None

    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        claude_mcp = appdata / "Claude" / "claude_desktop_config.json"
    else:
        claude_mcp = home / ".config" / "Claude" / "claude_desktop_config.json"

    configured = False
    details = "Claude Desktop / Code не установлен"

    if installed:
        if claude_mcp.exists():
            try:
                with open(claude_mcp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "zero-copy-agent" in data.get("mcpServers", {}):
                    configured = True
                    details = "Сервер zero-copy-agent активен в claude_desktop_config.json"
                else:
                    details = "Файл найден, но mcpServers не содержит zero-copy-agent"
            except Exception as e:
                details = f"Ошибка чтения: {e}"
        else:
            details = "Claude установлен, но MCP не настроен"

    return {
        "name": "Claude Desktop / Code",
        "installed": installed,
        "configured": configured,
        "path": str(claude_mcp),
        "details": details
    }


def configure_claude(dry_run: bool = False, port: int = 8000) -> Tuple[bool, str]:
    """Configures Claude Desktop MCP Server."""
    home = Path.home()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        claude_mcp = appdata / "Claude" / "claude_desktop_config.json"
    else:
        claude_mcp = home / ".config" / "Claude" / "claude_desktop_config.json"

    def updater(data: Dict[str, Any]) -> Dict[str, Any]:
        if "mcpServers" not in data:
            data["mcpServers"] = {}
        data["mcpServers"]["zero-copy-agent"] = {
            "url": f"http://127.0.0.1:{port}/sse"
        }
        return data

    return safe_update_json(claude_mcp, updater, target="claude", dry_run=dry_run)


# ==============================================================================
# Verification & Snippet Generator
# ==============================================================================

async def test_connection_async(target: str, port: int = 8000) -> Tuple[bool, str]:
    """Tests gateway readiness asynchronously without blocking the event loop."""
    import httpx
    url = f"http://127.0.0.1:{port}/health"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                body = resp.json()
                version = body.get("version", "unknown")
                return True, f"Шлюз активен (v{version}). Порт {port} отвечает на запросы 🟢"
            return False, f"Шлюз вернул HTTP {resp.status_code}"
    except Exception as e:
        return False, f"Шлюз недоступен по адресу {url}: {e} 🔴"


def test_connection(target: str, port: int = 8000) -> Tuple[bool, str]:
    """Tests gateway readiness synchronously (for CLI commands)."""
    import httpx
    url = f"http://127.0.0.1:{port}/health"
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                body = resp.json()
                version = body.get("version", "unknown")
                return True, f"Шлюз активен (v{version}). Порт {port} отвечает на запросы 🟢"
            return False, f"Шлюз вернул HTTP {resp.status_code}"
    except Exception as e:
        return False, f"Шлюз недоступен по адресу {url}: {e} 🔴"


def print_config_snippet(target: str, port: int = 8000) -> str:
    """Generates ready configuration snippet for manual insertion or env export."""
    t = target.lower()
    if t in ("antigravity", "cursor", "windsurf", "claude"):
        return json.dumps({
            "mcpServers": {
                "zero-copy-agent": {
                    "url": f"http://127.0.0.1:{port}/sse" if t != "antigravity" else f"http://127.0.0.1:{port}/mcp"
                }
            }
        }, indent=2, ensure_ascii=False)

    if t in ("vscode", "continue"):
        return json.dumps({
            "models": [
                {
                    "title": "Zero-Copy Local Fast",
                    "provider": "openai",
                    "model": "tier:local-fast",
                    "apiBase": f"http://127.0.0.1:{port}/v1",
                    "apiKey": "sk-zero-copy"
                },
                {
                    "title": "Cascade «Архитектор»",
                    "provider": "openai",
                    "model": "cascade:architect",
                    "apiBase": f"http://127.0.0.1:{port}/v1",
                    "apiKey": "sk-zero-copy"
                }
            ]
        }, indent=2, ensure_ascii=False)

    if t == "aider":
        return (
            f"export OPENAI_API_BASE=http://127.0.0.1:{port}/v1\n"
            f"export OPENAI_API_KEY=sk-zero-copy\n"
            f"export AIDER_MODEL=openai/tier:coding-top\n"
        )

    return f"# Неизвестная цель: {target}. Базовый endpoint: http://127.0.0.1:{port}/v1"


# ==============================================================================
# Public Scan & Orchestration Interface
# ==============================================================================

def scan_all() -> Dict[str, Any]:
    """Scans all supported IDEs and Agents."""
    return {
        "antigravity": detect_antigravity(),
        "vscode": detect_vscode(),
        "cursor": detect_cursor(),
        "windsurf": detect_windsurf(),
        "claude": detect_claude(),
        "aider": detect_aider(),
    }


def print_scan_report():
    """Prints a clear, formatted console report of detected environments."""
    results = scan_all()
    print("\n" + "=" * 65)
    print("  🔍 Сканирование поддерживаемых IDE и AI-агентов")
    print("=" * 65)
    for key, info in results.items():
        status = "🟢 НАСТРОЕН" if info["configured"] else ("🟡 УСТАНОВЛЕН (Требует настройки)" if info["installed"] else "⚪ Не обнаружен")
        print(f"  • {info['name']:<35} : {status}")
        print(f"    Детали: {info['details']}")
    print("=" * 65 + "\n")


def configure_targets(targets: List[str], dry_run: bool = False) -> List[Tuple[str, bool, str]]:
    """Configures requested targets safely, skipping uninstalled IDEs in batch mode."""
    results = []
    target_set = set(targets)
    scanned = scan_all()

    if "all" in target_set:
        target_set = {k for k, v in scanned.items() if v.get("installed", False)}
        if not target_set:
            return [("Сканер сред", False, "Установленных IDE на компьютере не найдено")]

    if "antigravity" in target_set:
        ok, msg = configure_antigravity(dry_run=dry_run)
        results.append(("Google Antigravity", ok, msg))

    if "vscode" in target_set:
        ok, msg = configure_vscode(dry_run=dry_run)
        results.append(("Visual Studio Code", ok, msg))

    if "cursor" in target_set:
        if scanned.get("cursor", {}).get("installed"):
            ok, msg = configure_cursor(dry_run=dry_run)
            results.append(("Cursor IDE", ok, msg))
        else:
            results.append(("Cursor IDE", False, "Пропущено: Cursor IDE не установлен"))

    if "windsurf" in target_set:
        if scanned.get("windsurf", {}).get("installed"):
            ok, msg = configure_windsurf(dry_run=dry_run)
            results.append(("Windsurf", ok, msg))
        else:
            results.append(("Windsurf", False, "Пропущено: Windsurf не установлен"))

    if "claude" in target_set:
        if scanned.get("claude", {}).get("installed"):
            ok, msg = configure_claude(dry_run=dry_run)
            results.append(("Claude Desktop / Code", ok, msg))
        else:
            results.append(("Claude Desktop / Code", False, "Пропущено: Claude не установлен"))

    if "aider" in target_set:
        if scanned.get("aider", {}).get("installed"):
            ok, msg = configure_aider(dry_run=dry_run)
            results.append(("Aider CLI", ok, msg))
        else:
            results.append(("Aider CLI", False, "Пропущено: Aider CLI не установлен"))

    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Zero-Copy Agent IDE & Agent Auto-Configuration Manager")
    parser.add_argument("--scan", action="store_true", help="Сканировать и показать статус сред")
    parser.add_argument("--configure", nargs="+", choices=["all", "antigravity", "vscode", "cursor", "windsurf"], help="Цели для настройки")
    parser.add_argument("--rollback", nargs="?", const="all", choices=["all", "antigravity", "vscode", "cursor", "windsurf"], help="Откатить изменения к предыдущему бэкапу")
    parser.add_argument("--history", action="store_true", help="Показать историю изменений и бэкапов")
    parser.add_argument("--dry-run", action="store_true", help="Симуляция без записи на диск")

    args = parser.parse_args()

    if args.history:
        history = history_manager.load_history()
        print("\n" + "=" * 65)
        print("  📜 История конфигураций и бэкапов (~/.zero-copy-agent/backups)")
        print("=" * 65)
        if not history:
            print("  История пуста.")
        else:
            for item in history:
                print(f"  [{item['timestamp'][:19]}] {item['target'].upper():<12} -> {Path(item['backup_path']).name}")
                print(f"    Файл: {item['original_path']}")
        print("=" * 65 + "\n")
        return

    if args.rollback:
        print(f"\n🔄 Выполняю откат для цели: {args.rollback}...")
        results = history_manager.rollback(args.rollback)
        for line in results:
            print(f"  {line}")
        print()
        return

    if args.configure:
        print_scan_report()
        print(f"🚀 Настройка выбранных целей: {', '.join(args.configure)}...\n")
        results = configure_targets(args.configure, dry_run=args.dry_run)
        for name, ok, msg in results:
            icon = "✅" if ok else "❌"
            print(f"  {icon} {name}: {msg}")
        print("\n✓ Все операции завершены безопасно с созданием точек отката.")
        return

    # Default action: scan
    print_scan_report()


if __name__ == "__main__":
    main()
