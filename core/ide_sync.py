"""
zero-copy-agent IDE Synchronization & Routing Rules Manager.
Maintains persistent routing rules across global and project-level configs:
- ~/.gemini/GEMINI.md (Global Antigravity rule)
- <project>/GEMINI.md (Local Antigravity rule)
- <project>/.cursorrules (Cursor IDE rule)
- ~/.cursorrules (Global Cursor rule if exists)
"""

import os
import re
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from core.config import get_runtime_cwd

logger = logging.getLogger("zcao.ide_sync")

ZCA_ROUTING_START = "<!-- ZCA_ROUTING_START -->"
ZCA_ROUTING_END = "<!-- ZCA_ROUTING_END -->"

RULE_THROUGH_ZCA = """<!-- ZCA_ROUTING_START -->
# ZERO-COPY-AGENT (ZCA) MANDATORY ROUTING RULE
> **Режим маршрутизации: Через ZCA (ВСЕ запросы)**
ВСЕ задачи на проектирование, написание, рефакторинг и аудит кода ОБЯЗАТЕЛЬНО выполнять через MCP-инструменты `zero-copy-agent` (`plan_and_execute_task` / `cascade_architect` / `local_gpu_code`) без отдельного напоминания от пользователя. Не использовать встроенный swarm/воркеры IDE в обход ZCA.

### Фоновые процессы и запуск демона (Windows)
- Сервер ZCA и любые долгоживущие процессы запускать ТОЛЬКО в фоне:
  `Start-Process -FilePath "C:\\AI\\zero-copy-agent.exe" -ArgumentList "--headless" -WorkingDirectory "C:\\AI"`
- НИКОГДА не запускать `zero-copy-agent.exe` напрямую в интерактивном терминале без `--headless`/`--daemon` (блокирует сессию).
<!-- ZCA_ROUTING_END -->"""

RULE_BYPASS = """<!-- ZCA_ROUTING_START -->
# ZERO-COPY-AGENT (ZCA) MANDATORY ROUTING RULE
> **Режим маршрутизации: В обход ZCA (Bypass)**
ZCA находится в режиме обхода (Bypass). Выполняй задачи напрямую встроенными средствами IDE, не вызывая инструменты ZCA.
<!-- ZCA_ROUTING_END -->"""


def get_rule_text_for_mode(mode: str) -> str:
    """Returns markdown rule text corresponding to routing mode."""
    if mode == "bypass":
        return RULE_BYPASS
    return RULE_THROUGH_ZCA


def apply_rule_to_file(file_path: Path, rule_block: str) -> bool:
    """
    Safely injects or updates the ZCA routing rule in target file between markers.
    Preserves all existing user rules and instructions.
    """
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8", errors="replace")
            pattern = re.compile(
                re.escape(ZCA_ROUTING_START) + r".*?" + re.escape(ZCA_ROUTING_END),
                re.DOTALL
            )
            if pattern.search(content):
                new_content = pattern.sub(lambda _: rule_block, content)
            else:
                new_content = f"{rule_block}\n\n{content}".strip() + "\n"
        else:
            content = ""
            new_content = f"{rule_block}\n"

        if content == new_content:
            return False

        file_path.write_text(new_content, encoding="utf-8")
        logger.info(f"[IDE Sync] Updated routing rule in: {file_path}")
        return True
    except Exception as e:
        logger.warning(f"[IDE Sync] Failed writing routing rule to {file_path}: {e}")
        return False


def get_target_rule_files(project_dirs: Optional[List[Path]] = None) -> List[Path]:
    """Collects list of global and local rule files to synchronize."""
    targets: List[Path] = []
    home = Path.home()

    # 1. Global Antigravity instructions
    targets.append(home / ".gemini" / "GEMINI.md")

    # 2. Global Cursor instructions if directory exists
    if (home / ".cursor").exists():
        targets.append(home / ".cursorrules")

    # 3. Project-specific directories
    dirs = list(project_dirs) if project_dirs else []
    cwd = Path.cwd()
    runtime_cwd = get_runtime_cwd()
    for d in (cwd, runtime_cwd):
        if d not in dirs:
            dirs.append(d)

    for p_dir in dirs:
        if p_dir and p_dir.exists():
            targets.append(p_dir / "GEMINI.md")
            targets.append(p_dir / ".cursorrules")

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for t in targets:
        resolved = str(t.resolve()) if t.exists() else str(t)
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(t)
    return deduped


def sync_routing_rules(mode: str = "through_zca", project_dirs: Optional[List[Path]] = None) -> Dict[str, Any]:
    """
    Applies current ide_routing_mode rule ('through_zca' or 'bypass')
    across all detected IDE instruction files.
    """
    clean_mode = "bypass" if mode == "bypass" else "through_zca"
    rule_text = get_rule_text_for_mode(clean_mode)
    files = get_target_rule_files(project_dirs)

    updated = []
    skipped = []
    failed = []

    for f_path in files:
        try:
            changed = apply_rule_to_file(f_path, rule_text)
            if changed:
                updated.append(str(f_path))
            else:
                skipped.append(str(f_path))
        except Exception as e:
            logger.warning(f"Failed to apply rule to {f_path}: {e}")
            failed.append(str(f_path))

    return {
        "status": "ok",
        "mode": clean_mode,
        "updated": updated,
        "skipped": skipped,
        "synced_files": updated + skipped,
        "failed_files": failed,
        "count": len(updated)
    }
