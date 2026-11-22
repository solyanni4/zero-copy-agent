"""
UI & Operations API router for zero-copy-agent Web Dashboard.
Provides endpoints for Config Healer, Model Discovery, DLP Sandbox,
Policy Toggles, Backup Logs, and Real-time System Status.
"""

import time
import os
import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Request, HTTPException

from core import __version__
from core.config import config
from core.storage import analytics
from core.healer import config_healer
from core.ide_setup import scan_all, configure_targets, history_manager, test_connection_async, print_config_snippet
from core.shield import shield, SessionVault
from core.registry import model_registry

logger = logging.getLogger("zcao.ui_api")

ui_router = APIRouter(prefix="/v1/ui", tags=["UI Operations"])

_START_TIME = time.time()

class PolicyUpdateRequest(BaseModel):
    dlp_enabled: Optional[bool] = None
    air_gapped: Optional[bool] = None
    compression_enabled: Optional[bool] = None
    mode: Optional[str] = None  # "auto" | "cascade" | "manual"
    breakpoint_enabled: Optional[bool] = None
    max_rework_iterations: Optional[int] = None
    active_model: Optional[str] = None
    architect_model: Optional[str] = None
    executor_model: Optional[str] = None
    reviewer_model: Optional[str] = None

class DLPSandboxRequest(BaseModel):
    text: str = Field(..., description="Text or configuration code to sanitize")
    mask_keys: bool = Field(True, description="Mask API keys, tokens, and passwords")
    mask_ips: bool = Field(True, description="Mask local RFC1918 IP addresses")
    mask_hosts: bool = Field(True, description="Mask proprietary internal domains")

@ui_router.get("/status")
async def get_ui_system_status(request: Request):
    """Returns aggregated live server status, policies, hardware profile, and metrics for the Web UI."""
    from core.hardware import get_hardware_profile
    from dataclasses import asdict

    orchestrator = getattr(request.app.state, "orchestrator", None)

    # Fast cached hardware specs + live Ollama detection
    hw_profile = get_hardware_profile()
    hw = asdict(hw_profile)
    ollama_ok = hw.get("ollama_online", False)
    ollama_models = hw.get("ollama_models", [])

    savings = analytics.get_summary()

    policies = {
        "dlp_enabled": getattr(orchestrator, "dlp_enabled", True) if orchestrator else True,
        "air_gapped": getattr(orchestrator, "air_gapped", False) if orchestrator else False,
        "compression_enabled": getattr(orchestrator, "compression_enabled", True) if orchestrator else True,
        "mode": getattr(orchestrator, "mode", getattr(config.cascade, "mode", "auto")) if orchestrator else getattr(config.cascade, "mode", "auto"),
        "breakpoint_enabled": getattr(orchestrator, "breakpoint_enabled", False) if orchestrator else False,
        "max_rework_iterations": getattr(orchestrator, "max_rework_iterations", getattr(config.cascade, "max_rework_iterations", 2)) if orchestrator else getattr(config.cascade, "max_rework_iterations", 2),
        "active_model": getattr(orchestrator, "active_model", "cascade:architect") if orchestrator else "cascade:architect",
        "architect_model": getattr(orchestrator, "architect_model", getattr(config.cascade, "architect_model", "gemini-4-neon")) if orchestrator else getattr(config.cascade, "architect_model", "gemini-4-neon"),
        "executor_model": getattr(orchestrator, "executor_model", getattr(config.cascade, "coder_model", "qwen2.5-coder:14b")) if orchestrator else getattr(config.cascade, "coder_model", "qwen2.5-coder:14b"),
        "reviewer_model": getattr(orchestrator, "reviewer_model", getattr(config.cascade, "auditor_model", "gemini-4-neon")) if orchestrator else getattr(config.cascade, "auditor_model", "gemini-4-neon")
    }

    return {
        "status": "ok",
        "gateway_status": "online",
        "version": __version__,
        "uptime_seconds": int(time.time() - _START_TIME),
        "port": config.server.port,
        "host": config.server.host,
        "default_model": config.models.default,
        "hardware": hw,
        "ollama": {
            "online": ollama_ok,
            "models": ollama_models
        },
        "savings": savings,
        "policies": policies
    }

@ui_router.post("/policies")
async def update_ui_policies(request: Request, body: PolicyUpdateRequest):
    """Updates operational policies (DLP, Air-Gap, Compression, Cascade iterations)."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if not orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrator instance is not attached to application state")

    if body.dlp_enabled is not None:
        orchestrator.dlp_enabled = body.dlp_enabled
    if body.air_gapped is not None:
        orchestrator.air_gapped = body.air_gapped
    if body.compression_enabled is not None:
        orchestrator.compression_enabled = body.compression_enabled
    if body.mode is not None:
        orchestrator.mode = body.mode
    if body.breakpoint_enabled is not None:
        orchestrator.breakpoint_enabled = body.breakpoint_enabled
    if body.max_rework_iterations is not None:
        orchestrator.max_rework_iterations = max(0, min(5, body.max_rework_iterations))
    if body.active_model is not None:
        orchestrator.active_model = body.active_model
    if body.architect_model is not None:
        orchestrator.architect_model = body.architect_model
    if body.executor_model is not None:
        orchestrator.executor_model = body.executor_model
    if body.reviewer_model is not None:
        orchestrator.reviewer_model = body.reviewer_model

    return {
        "status": "ok",
        "policies": {
            "dlp_enabled": orchestrator.dlp_enabled,
            "air_gapped": orchestrator.air_gapped,
            "compression_enabled": orchestrator.compression_enabled,
            "mode": orchestrator.mode,
            "breakpoint_enabled": orchestrator.breakpoint_enabled,
            "max_rework_iterations": orchestrator.max_rework_iterations,
            "active_model": orchestrator.active_model,
            "architect_model": orchestrator.architect_model,
            "executor_model": orchestrator.executor_model,
            "reviewer_model": orchestrator.reviewer_model
        }
    }

@ui_router.post("/healer/run")
async def run_config_healer():
    """Runs self-healing on subscriptions.yaml, fixes deprecated models and repairs schema."""
    cfg, mods = config_healer.load_or_create()
    return {
        "status": "ok",
        "healed": len(mods) > 0,
        "modifications": mods,
        "schema_version": cfg.schema_version,
        "accounts_count": len(cfg.accounts)
    }

@ui_router.post("/discovery")
async def trigger_model_discovery(request: Request):
    """Forces catalog rediscovery from Ollama and web sessions."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if not orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrator instance is not attached")

    models = await orchestrator.discover_models(force=True, include_inactive=True)
    raw_list = [
        m.model_dump() if hasattr(m, "model_dump") else (m.dict() if hasattr(m, "dict") else m)
        for m in models
    ]
    return {
        "status": "ok",
        "count": len(models),
        "models": raw_list
    }

@ui_router.post("/dlp/test")
async def test_dlp_sandbox(body: DLPSandboxRequest):
    """Sanitizes text via DLPShield and returns tokens mapping for UI split-view sandbox."""
    req_id = f"sandbox_{int(time.time() * 1000)}"
    clean_text, vault = shield.sanitize(
        body.text,
        req_id=req_id,
        mask_keys=body.mask_keys,
        mask_ips=body.mask_ips,
        mask_hosts=body.mask_hosts
    )

    mappings = []
    for token, original in vault.mappings.items():
        mappings.append({
            "token": token,
            "original": original,
            "type": vault.secret_types.get(token, "SECRET")
        })

    return {
        "status": "ok",
        "original": body.text,
        "sanitized": clean_text,
        "detected_count": vault.detected_count,
        "total_masked": vault.detected_count,
        "mappings": mappings
    }

@ui_router.get("/dlp/logs")
async def get_dlp_audit_logs(limit: int = 40):
    """Returns recent DLP masking events from the SQLite WAL store."""
    events = analytics.get_recent_events(limit=limit * 2)
    dlp_events = [
        e for e in events
        if e.get("stage") in ("dlp_sanitized", "dlp_restored", "air_gap_blocked", "airgap_blocked")
    ][:limit]
    return {
        "status": "ok",
        "logs": dlp_events
    }

@ui_router.get("/backups")
async def get_ide_backups():
    """Returns backup records created during IDE integrations."""
    history = history_manager.load_history()
    # Sort descending by timestamp
    sorted_history = sorted(history, key=lambda x: x.get("timestamp", ""), reverse=True)
    return {
        "status": "ok",
        "backups": sorted_history
    }

@ui_router.get("/history")
async def get_system_history(limit: int = 50):
    """Returns recent request completions from SQLite WAL analytics."""
    records = analytics.get_recent(limit=limit)
    return {
        "status": "ok",
        "history": records
    }

@ui_router.get("/updates/check")
async def check_for_updates():
    """Checks GitHub releases for latest version."""
    import asyncio
    from core.updater import check_github_updates
    return await asyncio.to_thread(check_github_updates)

@ui_router.get("/system-logs")
async def get_system_logs(limit: int = 300, level: str = "all", search: str = ""):
    """
    Returns recent lines from the application log file (zero-copy-agent.log).
    Supports filtering by log level (all, error, warning, info) and keyword search.
    """
    from pathlib import Path
    log_path = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "zero-copy-agent" / "zero-copy-agent.log"
    if not log_path.exists():
        return {"status": "ok", "lines": [], "total_lines": 0, "path": str(log_path)}

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()

        lvl = level.lower().strip()
        kw = search.lower().strip()
        filtered = []

        for line in reversed(all_lines):
            l_strip = line.strip()
            if not l_strip:
                continue

            if lvl == "error" and "[ERROR]" not in l_strip and "error" not in l_strip.lower():
                continue
            if lvl in ("warning", "warn") and "[WARN" not in l_strip and "warning" not in l_strip.lower() and "[ERROR]" not in l_strip:
                continue
            if lvl == "info" and "[INFO]" not in l_strip:
                continue

            if kw and kw not in l_strip.lower():
                continue

            filtered.append(l_strip)
            if len(filtered) >= limit:
                break

        return {
            "status": "ok",
            "lines": list(reversed(filtered)),
            "total_lines": len(all_lines),
            "path": str(log_path)
        }
    except Exception as e:
        logger.error(f"[Logs API] Failed reading {log_path}: {e}")
        return {"status": "error", "message": str(e), "lines": []}

@ui_router.post("/system-logs/clear")
async def clear_system_logs():
    """Clears the zero-copy-agent.log file."""
    from pathlib import Path
    log_path = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "zero-copy-agent" / "zero-copy-agent.log"
    if log_path.exists():
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [INFO] Log cleared by user.\n")
            return {"status": "ok", "message": "Лог-файл очищен"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "ok", "message": "Лог-файл уже пуст"}
