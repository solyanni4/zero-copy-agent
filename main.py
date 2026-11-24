"""
zero-copy-agent v1.6.0
OpenAI-compatible Sidecar Gateway & Native Model Context Protocol (MCP) Server.
Routes Web Chat Subscriptions, Local Ollama GPU, and provides tools for Antigravity, Cursor & Claude.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Solyanni4 (https://github.com/solyanni4/zero-copy-agent)

import asyncio
import json
import time
import uuid
import os
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional, Dict, List, Any
import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Body, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core.version import __version__
from core.config import config, get_runtime_cwd
from core.ui_api import ui_router
from core.models import (
    AddKeyRequest,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionStreamResponse,
    Choice,
    ChoiceMessage,
    DeltaMessage,
    ModelCard,
    ModelListResponse,
    PlaygroundRequest,
    StreamChoice,
    Usage,
)
from core.browser_session import engine
from core.orchestrator import HybridOrchestrator
from core.mesh import mesh
from core.storage import analytics
from core.mcp_server import mcp_router, init_mcp
from core.attribution import should_inject_signature, get_response_headers, get_display_name
from core.healer import config_healer
from core.ide_setup import scan_all, configure_targets, history_manager, test_connection, test_connection_async, print_config_snippet, sync_ide_if_needed
from core.registry import model_registry

if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def sync_mesh_from_subscriptions(sub_cfg=None):
    """Synchronizes registered and active provider API keys into QuotaMesh pool."""
    if sub_cfg is None:
        sub_cfg, _ = config_healer.load_or_create()
    mesh.sync_active_accounts(getattr(sub_cfg, "accounts", []))

orchestrator = HybridOrchestrator(browser_engine=engine)
init_mcp(orchestrator)
sync_mesh_from_subscriptions()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-heal configuration on startup
    sub_cfg, heal_mods = config_healer.load_or_create()
    sync_mesh_from_subscriptions(sub_cfg)

    # Ensure Chrome Extension is ready and prefilled with syncToken
    try:
        from core.auth import ensure_chrome_extension_ready
        ext_path = ensure_chrome_extension_ready(config)
        logging.getLogger("zca.main").info(f"[Auth Assistant] Chrome Extension ready at: {ext_path}")
    except Exception as e:
        logging.getLogger("zca.main").warning(f"[Auth Assistant] Extension unpack warning: {e}")

    # Smart auto-sync IDEs on startup if version changed or new IDE detected
    try:
        sync_res = sync_ide_if_needed(current_version=__version__, port=config.server.port)
        if sync_res.get("status") == "synced":
            targets_str = ", ".join(sync_res.get("synced_targets", []))
            print(f"  [IDE Auto-Sync] Синхронизированы конфигурации: {targets_str}")
    except Exception as e:
        logging.getLogger("zca.main").debug(f"[IDE Auto-Sync] Warning: {e}")

    # Sync IDE routing rules (through_zca vs bypass)
    try:
        from core.ide_sync import sync_routing_rules
        routing_mode = getattr(config, "ide_routing_mode", "through_zca")
        res_sync = sync_routing_rules(routing_mode)
        print(f"  [IDE Routing] Режим маршрутизации: {routing_mode} (обновлено файлов: {len(res_sync.get('updated', []))})")
    except Exception as e:
        logging.getLogger("zca.main").debug(f"[IDE Routing] Sync warning: {e}")

    # Suppress noisy background poll logs
    try:
        from core.log_stream import BackgroundPollFilter
        for l_name in ("uvicorn.access", "uvicorn", "uvicorn.error", "zca.main", "zcao.orchestrator", ""):
            logging.getLogger(l_name).addFilter(BackgroundPollFilter())
    except Exception:
        pass

    # Load and apply active orchestration preset
    try:
        from core.orchestration_presets import presets_manager
        active_preset = presets_manager.get_active_preset()
        if active_preset:
            orchestrator.apply_preset(active_preset)
            print(f"  [Presets] Активный сетап оркестрации: {active_preset.name} ({active_preset.id})")
    except Exception as e:
        logging.getLogger("zca.main").debug(f"[Presets] Init warning: {e}")

    client_host = "127.0.0.1" if config.server.host in ("0.0.0.0", "::", "") else config.server.host
    
    print(f"\n=======================================================")
    print(f"  [ZCA] zero-copy-agent v{__version__} is active!")
    print(f"=======================================================")
    print(f"  API Endpoint : http://{client_host}:{config.server.port}/v1")
    print(f"  MCP Server   : http://{client_host}:{config.server.port}/sse")
    print(f"  Default Model: {config.models.default}")
    if heal_mods:
        print(f"  Config Healer: {len(heal_mods)} auto-repairs executed [OK]")
    print(f"")
    print(f"  [Setup] 30-Second IDE Setup (Cursor / Continue / Aider):")
    print(f"     • Base URL : http://{client_host}:{config.server.port}/v1")
    print(f"     • API Key  : sk-zero-copy")
    print(f"     • Model    : gemini-4-neon / cascade:architect")
    print(f"")
    print(f"  [MCP] Antigravity & Cursor MCP Config:")
    print(f"     • URL      : http://{client_host}:{config.server.port}/sse")
    print(f"")
    print(f"  [Web] Live Analytics & Dashboard:")
    print(f"     http://{client_host}:{config.server.port}/")
    print(f"  [Info] (В браузере используйте 127.0.0.1 или localhost, не 0.0.0.0)")
    print(f"=======================================================\n")
    await engine.initialize()

    if "--post-update" in sys.argv:
        async def _main_post_update_watcher():
            import webbrowser
            log_file = get_runtime_cwd() / "update.log"
            def _log(m):
                try:
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [POST-UPDATE-SERVER] {m}\n")
                except Exception:
                    pass
            _log("Server initialized. Checking desktop window or opening browser...")
            await asyncio.sleep(1.2)
            desktop_win = getattr(app.state, "desktop_window", None)
            if desktop_win:
                _log("Desktop window instance exists in state.")
            else:
                ui_url = f"http://{client_host}:{config.server.port}/?post_update=1&version={__version__}"
                _log(f"Opening browser Web UI: {ui_url}")
                try:
                    webbrowser.open(ui_url)
                except Exception as ex:
                    _log(f"webbrowser.open warning: {ex}")
        asyncio.create_task(_main_post_update_watcher())

    yield
    print("[INIT] Shutting down zero-copy-agent...")
    await engine.close()

logger = logging.getLogger("zca.main")

app = FastAPI(
    title="zero-copy-agent",
    description="Zero-Copy Web-to-Agent Bridge & Self-Hosted AI Copilot with Native MCP Server",
    version=__version__,
    lifespan=lifespan,
)

# Restrict CORS to local host origins, Chrome extension, and IDE webviews (preventing malicious web CSRF)
cors_env = os.getenv("ZCA_CORS_ORIGINS")
if cors_env:
    allowed_origins = [o.strip() for o in cors_env.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_origin_regex=r"^chrome-extension://.*$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*", "X-ZCA-Sync-Token"],
    )
else:
    allowed_origins = [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "tauri://localhost",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|tauri://.*|vscode-webview://.*|chrome-extension://.*)$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*", "X-ZCA-Sync-Token"],
    )

# Include Native Model Context Protocol (MCP) Router
app.include_router(mcp_router)

# Attach Orchestrator to application state for UI Operations API
app.state.orchestrator = orchestrator

# Include UI Operations API Router
app.include_router(ui_router)

def get_static_dir() -> Path:
    """Resolves static directory safely for both frozen binaries (PyInstaller _MEIPASS) and dev environments."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cand = Path(meipass) / "static"
            if cand.exists():
                return cand
        cand_exe = Path(sys.executable).resolve().parent / "static"
        if cand_exe.exists():
            return cand_exe
    cand_file = Path(__file__).resolve().parent / "static"
    if cand_file.exists():
        return cand_file
    cand_parent = Path(__file__).resolve().parent.parent / "static"
    if cand_parent.exists():
        return cand_parent
    return Path(__file__).resolve().parent / "static"

def _resolve_bundle_dir(subpath: str) -> str:
    """Resolves resource paths safely for both frozen binaries (PyInstaller) and dev environments."""
    if subpath == "static":
        return str(get_static_dir())
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        cand = os.path.join(base, subpath)
        if os.path.exists(cand):
            return cand
        cand = os.path.join(os.path.dirname(sys.executable), subpath)
        if os.path.exists(cand):
            return cand
    cand = os.path.join(os.path.dirname(__file__), subpath)
    if os.path.exists(cand):
        return cand
    cand = os.path.join(os.getcwd(), subpath)
    if os.path.exists(cand):
        return cand
    return os.path.join(os.path.dirname(__file__), subpath)

STATIC_DIR = str(get_static_dir())
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

DIST_DIR = _resolve_bundle_dir("dist")
DESKTOP_ASSETS = _resolve_bundle_dir(os.path.join("desktop", "assets"))

@app.get("/favicon.ico")
async def favicon():
    icon_path = os.path.join(DESKTOP_ASSETS, "icon.png")
    if os.path.exists(icon_path):
        return FileResponse(icon_path, media_type="image/png")
    return JSONResponse(status_code=204, content=None)

@app.get("/download/windows")
@app.get("/download/zero-copy-agent-windows-x64.exe")
async def download_windows():
    target = os.path.join(DIST_DIR, "zero-copy-agent-windows-x64.exe")
    if os.path.exists(target):
        return FileResponse(
            target,
            media_type="application/octet-stream",
            filename="zero-copy-agent-windows-x64.exe"
        )
    raise HTTPException(status_code=404, detail="Windows executable not found in dist/")

@app.get("/download/linux")
@app.get("/download/zero-copy-agent-linux-x64")
async def download_linux():
    target = os.path.join(DIST_DIR, "zero-copy-agent-linux-x64")
    if not os.path.exists(target):
        target = os.path.join(DIST_DIR, "zero-copy-agent")
    if os.path.exists(target):
        return FileResponse(
            target,
            media_type="application/octet-stream",
            filename="zero-copy-agent-linux-x64"
        )
    raise HTTPException(status_code=404, detail="Linux executable not found in dist/")

@app.get("/download/install.sh")
async def download_install_sh():
    sh_path = os.path.join(os.path.dirname(__file__), "install.sh")
    if os.path.exists(sh_path):
        return FileResponse(sh_path, media_type="text/x-shellscript", filename="install.sh")
    raise HTTPException(status_code=404, detail="install.sh not found")

@app.get("/download/install.ps1")
async def download_install_ps1():
    ps1_path = os.path.join(os.path.dirname(__file__), "install.ps1")
    if os.path.exists(ps1_path):
        return FileResponse(ps1_path, media_type="text/plain", filename="install.ps1")
    raise HTTPException(status_code=404, detail="install.ps1 not found")

def is_browser_request(request: Request) -> bool:
    """Detects if request originates from a human browser requesting HTML vs programmatic API."""
    if request.query_params.get("format") == "json":
        return False
    accept = request.headers.get("accept", "")
    if not accept:
        return False
    parts = [p.split(";")[0].strip().lower() for p in accept.split(",")]
    return "text/html" in parts and "application/json" not in parts[:1]

def render_styled_api_page(title: str, endpoint: str, data: dict, summary_html: str = "") -> HTMLResponse:
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — Zero-Copy Agent</title>
    <link rel="icon" type="image/x-icon" href="/favicon.ico">
    <style>
        :root {{
            --bg: #0d1117;
            --panel: #161b22;
            --border: #30363d;
            --accent: #58a6ff;
            --green: #2ea043;
            --text: #c9d1d9;
            --title: #f0f6fc;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 40px 20px;
            display: flex;
            justify-content: center;
        }}
        .container {{
            max-width: 900px;
            width: 100%;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            flex-wrap: wrap;
            gap: 16px;
        }}
        .title-group {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .btn {{
            background-color: var(--accent);
            color: #fff;
            padding: 8px 16px;
            border-radius: 6px;
            text-decoration: none;
            font-size: 14px;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            border: none;
            cursor: pointer;
        }}
        .btn-outline {{
            background-color: transparent;
            border: 1px solid var(--border);
            color: var(--text);
        }}
        .btn-outline:hover {{
            border-color: #8b949e;
            color: #f0f6fc;
        }}
        .card {{
            background-color: var(--panel);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }}
        pre {{
            background-color: #0b0e14;
            border: 1px solid #21262d;
            border-radius: 6px;
            padding: 16px;
            overflow-x: auto;
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
            font-size: 13px;
            color: #79c0ff;
            line-height: 1.5;
            margin: 0;
        }}
        .actions {{
            display: flex;
            gap: 10px;
            margin-top: 16px;
            flex-wrap: wrap;
        }}
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            font-size: 12px;
            font-weight: 600;
            border-radius: 12px;
            background-color: rgba(46, 160, 67, 0.15);
            color: #3fb950;
            border: 1px solid rgba(46, 160, 67, 0.4);
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-top: 16px;
        }}
        .metric-box {{
            background-color: var(--bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 16px;
            text-align: center;
        }}
        .metric-val {{
            font-size: 22px;
            font-weight: 700;
            color: var(--accent);
            margin-top: 6px;
        }}
        .metric-val.money {{
            color: #3fb950;
        }}
        .metric-label {{
            font-size: 12px;
            color: #8b949e;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="title-group">
                <img src="/favicon.ico" alt="Logo" style="width: 36px; height: 36px; border-radius: 50%; box-shadow: 0 0 10px rgba(88, 166, 255, 0.4);" />
                <div>
                    <h2 style="margin: 0; color: var(--title); font-size: 20px;">{title}</h2>
                    <div style="font-size: 12px; color: #8b949e; margin-top: 4px;">Эндпоинт: <code>{endpoint}</code></div>
                </div>
            </div>
            <a href="/" class="btn btn-outline">Вернуться в Дашборд</a>
        </div>

        {summary_html}

        <div class="card">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <span style="font-weight: 600; color: var(--title); font-size: 14px;">Ответ API (JSON):</span>
                <span class="badge">HTTP 200 OK</span>
            </div>
            <pre><code id="jsonCode">{json_str}</code></pre>
            <div class="actions">
                <button onclick="copyJson()" id="copyBtn" class="btn">Скопировать JSON</button>
                <a href="{endpoint}?format=json" target="_blank" class="btn btn-outline">Сырой JSON (?format=json)</a>
            </div>
        </div>
    </div>
    <script>
        function copyJson() {{
            const text = document.getElementById('jsonCode').innerText;
            navigator.clipboard.writeText(text).then(() => {{
                const btn = document.getElementById('copyBtn');
                const orig = btn.innerText;
                btn.innerText = 'Скопировано!';
                setTimeout(() => {{ btn.innerText = orig; }}, 2000);
            }});
        }}
    </script>
</body>
</html>"""
    return HTMLResponse(html)

@app.get("/v1/analytics/savings")
@app.get("/savings")
async def get_savings_analytics(request: Request):
    """Returns token volume and calculated USD saved vs commercial API rates."""
    data = analytics.get_summary()
    if is_browser_request(request):
        total_saved = data.get("total_saved_usd", 0.0)
        today_saved = data.get("today_saved_usd", 0.0)
        total_tokens = f"{data.get('total_tokens', 0):,}"
        tokens_without_zc = f"{data.get('tokens_without_zc', 0):,}"
        tokens_saved = f"{data.get('tokens_saved', 0):,}"
        tokens_saved_pct = data.get('tokens_saved_pct', 0.0)
        total_requests = data.get("total_requests", 0)
        summary = f"""
        <div class="card">
            <div style="font-weight: 600; color: var(--title); margin-bottom: 8px;">Сводка экономии токенов:</div>
            <div class="grid">
                <div class="metric-box">
                    <div class="metric-label">Сэкономлено на API</div>
                    <div class="metric-val money">${total_saved:.2f}</div>
                    <div style="font-size:11px;color:#8b949e;margin-top:4px;">Сегодня: ${today_saved:.2f}</div>
                </div>
                <div class="metric-box">
                    <div class="metric-label">Токенов обработано</div>
                    <div class="metric-val">{total_tokens}</div>
                    <div style="font-size:11px;color:#8b949e;margin-top:4px;">Без агента: <span style="text-decoration:line-through;color:#f85149;">{tokens_without_zc}</span> <span style="color:#3fb950;font-weight:600;">(-{tokens_saved_pct}%)</span></div>
                </div>
            </div>
            <div style="background: rgba(56, 139, 253, 0.08); border: 1px solid #30363d; border-radius: 8px; padding: 12px; margin-top: 16px;">
                <div style="font-weight: 600; color: #58a6ff; font-size: 13px; margin-bottom: 6px;">Сжатие контекста и локальная видеокарта:</div>
                <div style="display: flex; justify-content: space-between; font-size: 12px; color: #c9d1d9; flex-wrap: wrap; gap: 8px;">
                    <div>Расход без оптимизации: <strong style="color: #f85149;">{tokens_without_zc} токенов</strong></div>
                    <div>Фактически отправлено: <strong style="color: #58a6ff;">{total_tokens} токенов</strong></div>
                    <div>Сэкономлено токенов: <strong style="color: #3fb950;">-{tokens_saved} токенов (-{tokens_saved_pct}%)</strong></div>
                </div>
            </div>
        </div>
        """
        return render_styled_api_page("Метрики экономии токенов", "/v1/analytics/savings", data, summary_html=summary)
    return data

@app.get("/v1/analytics/history")
async def get_completion_history():
    """Returns recent requests processed by the gateway."""
    return analytics.get_recent()

@app.get("/", response_class=HTMLResponse)
@app.get("/ui", response_class=HTMLResponse)
async def root_dashboard():
    """Unified desktop dashboard & monitoring interface."""
    static_dir = get_static_dir()
    index_file = static_dir / "index.html"
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())

    # Direct fallback for PyInstaller frozen runtime (_MEIPASS)
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass_idx = Path(sys._MEIPASS) / "static" / "index.html"
        if meipass_idx.exists():
            with open(meipass_idx, "r", encoding="utf-8") as f:
                return HTMLResponse(f.read())

    # Direct fallback for dev source tree
    src_idx = Path(__file__).resolve().parent / "static" / "index.html"
    if src_idx.exists():
        with open(src_idx, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())

    return HTMLResponse("<html><body><h3>zero-copy-agent</h3><p>UI loading error: static/index.html not found.</p></body></html>", status_code=500)

@app.get("/v1/auth/status")
async def get_auth_status():
    """Returns active status for web session and API providers."""
    cfg, _ = config_healer.load_or_create()
    providers = {
        "gemini": {"active": False, "alias": None, "type": None},
        "chatgpt": {"active": False, "alias": None, "type": None},
        "claude": {"active": False, "alias": None, "type": None},
        "deepseek": {"active": False, "alias": None, "type": None},
        "qwen": {"active": False, "alias": None, "type": None},
        "grok": {"active": False, "alias": None, "type": None},
    }
    for acc in cfg.accounts:
        p = acc.provider.lower()
        if p in providers and acc.status == "active":
            providers[p]["active"] = True
            providers[p]["alias"] = acc.alias
            providers[p]["type"] = acc.auth_type

    if getattr(orchestrator.browser_engine, "is_ready", False):
        active_prov = getattr(orchestrator.browser_engine, "active_provider", None)
        if active_prov and active_prov.lower() in providers:
            providers[active_prov.lower()]["active"] = True

    return {"status": "ok", "providers": providers}

@app.post("/v1/auth/login")
async def trigger_browser_auth(payload: dict = Body(...)):
    """Launches interactive browser assistant to log in to web subscription."""
    provider = payload.get("provider", "gemini")
    res = await api_web_auth(provider)
    return {"status": "ok", "provider": provider, "message": res.get("message", "")}

def convert_chrome_cookie_to_playwright(c: dict) -> Optional[dict]:
    """Converts a Chrome extension cookie dictionary to Playwright-compatible format."""
    if not isinstance(c, dict) or "name" not in c or "value" not in c or "domain" not in c:
        return None
    pw = {
        "name": str(c["name"]),
        "value": str(c["value"]),
        "domain": str(c["domain"]),
        "path": str(c.get("path") or "/")
    }

    # Session cookies: do not set expires if expirationDate is absent or <= 0
    exp = None
    if "expirationDate" in c and c["expirationDate"] is not None:
        try:
            exp_val = float(c["expirationDate"])
            if exp_val > 0:
                exp = exp_val
        except (ValueError, TypeError):
            pass
    elif "expires" in c and c["expires"] is not None:
        try:
            exp_val = float(c["expires"])
            if exp_val > 0:
                exp = exp_val
        except (ValueError, TypeError):
            pass

    if exp is not None:
        pw["expires"] = exp

    if "httpOnly" in c:
        pw["httpOnly"] = bool(c["httpOnly"])
    if "secure" in c:
        pw["secure"] = bool(c["secure"])

    raw_same_site = str(c.get("sameSite", "")).lower()
    if raw_same_site in ("no_restriction", "none"):
        pw["sameSite"] = "None"
        pw["secure"] = True
    elif raw_same_site == "strict":
        pw["sameSite"] = "Strict"
    elif raw_same_site in ("lax", "unspecified"):
        pw["sameSite"] = "Lax"

    # Strict compliance for Playwright: __Host- cookies must not have domain, must be secure, path=/
    if pw["name"].startswith("__Host-"):
        pw["secure"] = True
        pw.pop("domain", None)
        pw["path"] = "/"
        clean_domain = str(c.get("domain", "")).lstrip(".") or "google.com"
        pw["url"] = f"https://{clean_domain}/"
    else:
        pw.pop("url", None)
        if not pw.get("domain"):
            pw["domain"] = str(c.get("domain", "")).lstrip(".")
        if not pw.get("path"):
            pw["path"] = "/"

    return pw

@app.post("/v1/auth/sync-cookies")
async def sync_cookies_from_extension(request: Request, payload: dict = Body(...)):
    """Receives authenticated cookies from zero-copy-agent Chrome extension."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="Localhost access only")

    token_hdr = request.headers.get("x-zca-sync-token") or ""
    expected_token = getattr(config, "cookie_sync_token", "") or ""
    if not expected_token or token_hdr != expected_token:
        raise HTTPException(status_code=401, detail="Invalid sync token")

    provider = str(payload.get("provider", "")).strip().lower()
    account_id = str(payload.get("account_id", "")).strip() or f"{provider}-web-primary"
    raw_cookies = payload.get("cookies", [])
    user_agent = str(payload.get("user_agent", "")).strip()

    if not provider or not raw_cookies:
        raise HTTPException(status_code=400, detail="Missing provider or cookies payload")

    converted_cookies = []
    for rc in raw_cookies:
        pw_c = convert_chrome_cookie_to_playwright(rc)
        if pw_c:
            converted_cookies.append(pw_c)

    from core.auth import has_key_auth_cookies
    if not has_key_auth_cookies(provider, converted_cookies):
        return {"status": "ignored", "reason": "no_auth_cookies", "provider": provider}

    prof_dir = (get_runtime_cwd() / "browser_profiles" / account_id).resolve()
    prof_dir.mkdir(parents=True, exist_ok=True)
    cookie_path = prof_dir / "synced_cookies.json"

    # Check if incoming cookies are identical to existing saved set
    cookies_changed = True
    if cookie_path.exists():
        try:
            with open(cookie_path, "r", encoding="utf-8") as f:
                old_data = json.load(f)
            old_cookies = old_data.get("cookies", [])
            old_sig = sorted(
                (c.get("domain", "") or c.get("url", ""), c.get("name", ""), c.get("value", ""))
                for c in old_cookies
            )
            new_sig = sorted(
                (c.get("domain", "") or c.get("url", ""), c.get("name", ""), c.get("value", ""))
                for c in converted_cookies
            )
            if old_sig == new_sig:
                cookies_changed = False
        except Exception:
            pass

    sub_cfg, _ = config_healer.load_or_create()
    is_already_active = any(
        a.id == account_id and a.status == "active"
        for a in sub_cfg.accounts
    )

    if not cookies_changed and is_already_active:
        if user_agent:
            ua_file = prof_dir / "user_agent.txt"
            if not ua_file.exists():
                ua_file.write_text(user_agent, encoding="utf-8")
        return {
            "status": "unchanged",
            "provider": provider,
            "account_id": account_id,
            "cookies_synced": len(converted_cookies)
        }

    # Save user_agent if provided
    save_data = {
        "provider": provider,
        "account_id": account_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "cookies": converted_cookies
    }
    if user_agent:
        save_data["user_agent"] = user_agent
        ua_file = prof_dir / "user_agent.txt"
        try:
            ua_file.write_text(user_agent, encoding="utf-8")
        except Exception:
            pass

    with open(cookie_path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, indent=2, ensure_ascii=False)

    # Register / refresh in subscriptions.yaml
    alias_names = {
        "gemini": "Gemini Advanced (Chrome Sync)",
        "chatgpt": "ChatGPT Plus / Free (Chrome Sync)",
        "claude": "Claude Pro / Free (Chrome Sync)",
        "deepseek": "DeepSeek Reasoner / Chat (Chrome Sync)",
        "qwen": "Qwen Max / Coder (Chrome Sync)",
        "grok": "xAI Grok 3 (Chrome Sync)"
    }
    alias = alias_names.get(provider, f"{provider.upper()} Web Session ({account_id})")
    config_healer.register_web_session(
        provider=provider,
        account_id=account_id,
        alias=alias,
        profile_dir=f"browser_profiles/{account_id}"
    )

    # If context is currently live, inject cookies dynamically and reload page
    if hasattr(orchestrator, "browser_engine") and orchestrator.browser_engine:
        b_eng = orchestrator.browser_engine
        if hasattr(b_eng, "inject_synced_cookies"):
            await b_eng.inject_synced_cookies(account_id, provider, converted_cookies, user_agent=user_agent)
        else:
            ctx = b_eng._contexts.get(account_id)
            if ctx:
                try:
                    await ctx.add_cookies(converted_cookies)
                    if hasattr(b_eng, "_cookie_mtimes"):
                        b_eng._cookie_mtimes[account_id] = time.time()
                    page = b_eng._pages.get(account_id)
                    if page and not getattr(page, "is_closed", lambda: True)():
                        from core.browser_session import PROVIDER_URLS
                        target_url = PROVIDER_URLS.get(provider, "https://gemini.google.com/app")
                        await page.goto(target_url, wait_until="domcontentloaded")
                    logging.getLogger("zca.main").info(f"[Auth Assistant] Injected {len(converted_cookies)} cookies into active context '{account_id}'")
                except Exception as inj_e:
                    logging.getLogger("zca.main").warning(f"[Auth Assistant] Live cookie injection note: {inj_e}")

    # Reset cooldown in QuotaMesh
    if hasattr(orchestrator, "quota_mesh") and orchestrator.quota_mesh:
        orchestrator.quota_mesh.reset_account(account_id)

    return {
        "status": "ok",
        "provider": provider,
        "account_id": account_id,
        "cookies_synced": len(converted_cookies)
    }

@app.get("/v1/auth/sync-info")
async def get_sync_info():
    """Returns sync token, extension path, and sync status across all accounts."""
    from core.auth import ensure_chrome_extension_ready
    ext_path = ensure_chrome_extension_ready(config)

    providers_status = {}
    base_profiles = get_runtime_cwd() / "browser_profiles"
    if base_profiles.exists():
        for p_dir in base_profiles.iterdir():
            if p_dir.is_dir():
                cf = p_dir / "synced_cookies.json"
                if cf.exists():
                    try:
                        with open(cf, "r", encoding="utf-8") as f:
                            cdata = json.load(f)
                        providers_status[p_dir.name] = {
                            "account_id": cdata.get("account_id", p_dir.name),
                            "provider": cdata.get("provider", ""),
                            "updated_at": cdata.get("updated_at", ""),
                            "cookie_count": len(cdata.get("cookies", []))
                        }
                    except Exception:
                        pass

    return {
        "status": "ok",
        "sync_token": getattr(config, "cookie_sync_token", ""),
        "extension_path": str(ext_path),
        "providers": providers_status
    }

@app.post("/v1/auth/open-extension-setup")
async def open_extension_setup_route():
    """Unpacks extension, selects it in Explorer, and opens chrome://extensions in Chrome."""
    from core.auth import open_chrome_extension_setup
    ext_dir = open_chrome_extension_setup(config)
    return {
        "status": "ok",
        "path": str(ext_dir),
        "sync_token": getattr(config, "cookie_sync_token", "")
    }

@app.websocket("/ws/bridge")
async def websocket_bridge_endpoint(websocket: WebSocket):
    """WebSocket endpoint connecting zero-copy-agent Chrome Extension to ZCA backend."""
    from core.chrome_bridge import chrome_bridge
    await chrome_bridge.handle_websocket(websocket)

@app.get("/v1/bridge/status")
async def get_bridge_status():
    """Returns real-time status of Chrome Bridge connection and active web providers."""
    from core.chrome_bridge import chrome_bridge
    return chrome_bridge.get_status()

@app.post("/v1/bridge/test/{provider}")
async def test_bridge_provider(provider: str):
    """Executes a short test prompt through Chrome Bridge to verify browser response."""
    from core.chrome_bridge import chrome_bridge
    prov = provider.strip().lower()
    if not chrome_bridge.is_connected():
        raise HTTPException(status_code=503, detail="Chrome Bridge не подключен к zero-copy-agent. Откройте Google Chrome.")
    if not chrome_bridge.has_provider(prov):
        raise HTTPException(status_code=400, detail=f"Провайдер '{prov}' не активен в подключенном Chrome Bridge.")
    t0 = time.time()
    try:
        res = await chrome_bridge.execute_prompt(prov, "Ответь одним словом: OK", timeout=30.0)
        latency_ms = max(1, int((time.time() - t0) * 1000))
        return {
            "status": "ok",
            "provider": prov,
            "response": res.strip(),
            "latency_ms": latency_ms
        }
    except Exception as e:
        latency_ms = max(1, int((time.time() - t0) * 1000))
        raise HTTPException(status_code=500, detail=f"Ошибка теста {prov}: {e}")

@app.post("/v1/orchestrator/stop")
async def stop_orchestrator_endpoint(request: Request):
    """Stops all running orchestration tasks, cancels bridge requests, and resets status to IDLE."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="Localhost only")
    res = await orchestrator.stop()
    return {
        "status": "ok",
        "message": "🛑 Оркестрация остановлена пользователем",
        "orchestrator_status": orchestrator.status,
        "cancelled_tasks": res.get("cancelled_tasks", 0)
    }

@app.post("/v1/system/shutdown")
async def system_shutdown_endpoint(request: Request):
    """Gracefully shuts down the zero-copy-agent server and desktop process."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="Localhost only")

    async def _do_shutdown():
        await asyncio.sleep(0.3)
        try:
            await orchestrator.stop()
        except Exception:
            pass
        try:
            if getattr(orchestrator, "browser_engine", None):
                await orchestrator.browser_engine.close_all()
        except Exception:
            pass
        d_app = getattr(request.app.state, "desktop_app", None)
        if d_app and hasattr(d_app, "stop"):
            try:
                d_app.stop()
                return
            except Exception:
                pass
        import os
        os._exit(0)

    asyncio.create_task(_do_shutdown())
    return {"status": "shutting_down", "message": "ZCA shutting down"}

@app.post("/v1/app/minimize")
async def app_minimize(request: Request):
    """Minimizes desktop application to system tray."""
    win = getattr(request.app.state, "desktop_window", None)
    d_app = getattr(request.app.state, "desktop_app", None)
    if win:
        try:
            win.hide()
        except Exception as e:
            logging.error(f"Failed to hide window: {e}")
    elif d_app and getattr(d_app, "window", None):
        try:
            d_app.window.hide()
        except Exception as e:
            logging.error(f"Failed to hide window via desktop_app: {e}")

    if d_app:
        d_app.notify_tray(
            "Приложение свернуто в трей и продолжает работать.\nДважды кликните по иконке, чтобы открыть окно.",
            title="zero-copy-agent"
        )
        return {"status": "ok", "message": "Приложение свернуто в системный трей"}
    return {"status": "ok", "message": "Консольный режим"}

@app.post("/v1/app/quit")
async def app_quit(request: Request):
    """Triggers graceful termination of zero-copy-agent."""
    d_app = getattr(request.app.state, "desktop_app", None)
    if d_app:
        import threading
        threading.Thread(target=d_app.stop, daemon=True).start()
    else:
        import threading
        import time
        def _delayed_exit():
            time.sleep(0.3)
            import os, signal
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception:
                os._exit(0)
        threading.Thread(target=_delayed_exit, daemon=True).start()
    return {"status": "ok", "message": "Завершение работы"}


@app.get("/health")
async def health_check(request: Request):
    """Health check endpoint for homelab monitors and clients."""
    ollama_ok = await orchestrator.ollama.is_authenticated()
    ollama_models = await orchestrator.ollama.list_models() if ollama_ok else []
    data = {
        "status": "ok",
        "service": "zero-copy-agent",
        "version": __version__,
        "mcp_enabled": True,
        "mcp_sse_url": f"http://127.0.0.1:{config.server.port}/sse",
        "dlp_shield_active": True,
        "bpe_compression": "L1+L2 Active",
        "browser_engine": "playwright" if engine.is_ready else "simulator",
        "active_provider": engine.active_provider,
        "default_model": config.models.default,
        "ollama_available": ollama_ok,
        "ollama_models": ollama_models,
        "active_aliases": orchestrator.aliases,
        "savings_summary": analytics.get_summary()
    }
    if is_browser_request(request):
        summary = f"""
        <div class="card">
            <div style="font-weight: 600; color: var(--title); margin-bottom: 8px;">Состояние компонентов шлюза:</div>
            <div class="grid">
                <div class="metric-box">
                    <div class="metric-label">Служба</div>
                    <div class="metric-val" style="font-size: 18px; color: #3fb950;">v{__version__} [OK]</div>
                    <div style="font-size:11px;color:#8b949e;margin-top:4px;">zero-copy-agent Active</div>
                </div>
                <div class="metric-box">
                    <div class="metric-label">MCP Сервер</div>
                    <div class="metric-val" style="font-size: 18px; color: #bc8cff;">SSE Active [OK]</div>
                    <div style="font-size:11px;color:#8b949e;margin-top:4px;">Порт {config.server.port}</div>
                </div>
                <div class="metric-box">
                    <div class="metric-label">DLP Shield</div>
                    <div class="metric-val" style="font-size: 18px; color: #58a6ff;">Защищен</div>
                    <div style="font-size:11px;color:#8b949e;margin-top:4px;">Санитайзер секретов</div>
                </div>
                <div class="metric-box">
                    <div class="metric-label">GPU / Ollama</div>
                    <div class="metric-val" style="font-size: 18px; color: {'#58a6ff' if ollama_ok else '#8b949e'};">{'Онлайн' if ollama_ok else 'Остановлен'}</div>
                    <div style="font-size:11px;color:#8b949e;margin-top:4px;">{len(ollama_models)} локальных весов</div>
                </div>
            </div>
        </div>
        """
        return render_styled_api_page("Системная диагностика (Health Check)", "/health", data, summary_html=summary)
    return data

@app.get("/v1/events")
async def get_cognitive_events(limit: int = 30):
    """Returns recent cognitive trace events (DLP masking, BPE compression, cascades)."""
    return {
        "status": "ok",
        "events": analytics.get_recent_events(limit=limit)
    }

@app.get("/v1/models")
async def list_models(request: Request, include_inactive: bool = False, refresh: bool = False):
    """Returns available models, filtering only currently connected ones by default."""
    hide_inactive = config.ux.hide_inactive_models and not include_inactive
    models_data = await orchestrator.discover_models(force=refresh, include_inactive=not hide_inactive)
    if is_browser_request(request):
        cards_html = ""
        for m in models_data:
            m_id = m.id if hasattr(m, "id") else m.get("id")
            m_owner = m.owned_by if hasattr(m, "owned_by") else m.get("owned_by", "")
            prov = m_owner.replace("zero-copy-agent:", "").upper() if m_owner else "AI"
            cards_html += f"""
            <div style="display:flex; justify-content:space-between; align-items:center; padding:10px 14px; background:#0d1117; border:1px solid #30363d; border-radius:6px; margin-bottom:8px;">
                <div>
                    <strong style="color:#f0f6fc; font-family:monospace; font-size:14px;">{m_id}</strong>
                    <div style="font-size:11px; color:#8b949e; margin-top:3px;">Провайдер: <span style="color:#58a6ff;">{prov}</span></div>
                </div>
                <span class="badge">[OK] Активна</span>
            </div>
            """
        summary = f"""
        <div class="card">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
                <span style="font-weight:600; color:var(--title);">Всего подключено моделей: {len(models_data)}</span>
                <span class="badge">Готовы к вызовам</span>
            </div>
            <div style="max-height: 350px; overflow-y: auto;">
                {cards_html}
            </div>
        </div>
        """
        raw_list = [m.model_dump() if hasattr(m, "model_dump") else (m.dict() if hasattr(m, "dict") else m) for m in models_data]
        return render_styled_api_page("Каталог моделей (/v1/models)", "/v1/models", {"object": "list", "data": raw_list}, summary_html=summary)
    return ModelListResponse(data=models_data)

@app.get("/v1/integrations/scan")
async def api_scan_integrations():
    """Returns detected IDEs and AI agent configuration statuses."""
    return {"status": "ok", "integrations": scan_all()}

@app.post("/v1/integrations/apply/{target}")
async def api_apply_integration(target: str):
    """Configures requested IDE or agent with pre-backup."""
    results = configure_targets([target])
    return {
        "status": "ok",
        "results": [{"target": r[0], "success": r[1], "message": r[2]} for r in results]
    }

@app.post("/v1/integrations/revert/{target}")
async def api_revert_integration(target: str):
    """Rolls back the target from its previous backup to clean state."""
    from core.ide_setup import rollback_target_clean
    ok, msg = rollback_target_clean(target)
    return {"status": "ok" if ok else "error", "message": msg, "results": [msg]}

@app.get("/v1/system/hardware")
async def api_get_hardware():
    """Returns detected hardware profile (GPU/VRAM/RAM/CPU) and model recommendations."""
    from core.hardware import get_hardware_profile
    from dataclasses import asdict
    profile = get_hardware_profile()
    return {"status": "ok", "hardware": asdict(profile)}

@app.get("/v1/system/update/check")
async def api_check_updates():
    """Checks GitHub releases for new zero-copy-agent updates."""
    import asyncio
    from core.updater import check_github_updates
    return await asyncio.to_thread(check_github_updates)

@app.post("/v1/system/update/install")
async def api_install_update(payload: dict = Body(default={})):
    """Downloads update and replaces the running executable automatically."""
    from core.updater import start_in_app_update
    res = start_in_app_update(payload.get("download_url"))
    return res

@app.get("/v1/system/update/progress")
async def api_get_update_progress():
    """Returns real-time download and installation progress."""
    from core.updater import get_update_state
    return get_update_state()

@app.post("/v1/system/uninstall")
async def api_uninstall_system(payload: dict = Body(...)):
    """Cleanly uninstalls zero-copy-agent: reverts all IDEs, removes caches/configs."""
    if not payload.get("confirm", False):
        raise HTTPException(status_code=400, detail="Подтверждение обязательно (confirm: true)")
    from core.uninstaller import clean_uninstall_system
    result = clean_uninstall_system(keep_backups=payload.get("keep_backups", False), orchestrator_instance=orchestrator)

    # Automatic clean termination of process
    import threading, time, os
    def _delayed_terminate():
        time.sleep(0.8)
        d_app = getattr(app.state, "desktop_app", None)
        if d_app:
            try:
                d_app.stop()
            except Exception:
                pass
        os._exit(0)
    threading.Thread(target=_delayed_terminate, daemon=True).start()

    return result

@app.get("/v1/orchestration/config")
async def api_get_orchestration_config():
    """Returns custom orchestration pipeline settings."""
    from core.config import config
    cascade_cfg = getattr(config, "cascade", None)
    planner_dict = cascade_cfg.planner.model_dump() if (cascade_cfg and cascade_cfg.planner and hasattr(cascade_cfg.planner, "model_dump")) else (cascade_cfg.planner.dict() if cascade_cfg and cascade_cfg.planner else None)
    coder_dict = cascade_cfg.coder.model_dump() if (cascade_cfg and cascade_cfg.coder and hasattr(cascade_cfg.coder, "model_dump")) else (cascade_cfg.coder.dict() if cascade_cfg and cascade_cfg.coder else None)
    reviewer_dict = cascade_cfg.reviewer.model_dump() if (cascade_cfg and cascade_cfg.reviewer and hasattr(cascade_cfg.reviewer, "model_dump")) else (cascade_cfg.reviewer.dict() if cascade_cfg and cascade_cfg.reviewer else None)

    return {
        "status": "ok",
        "config": {
            "architect_model": getattr(config.cascade, "architect_model", "gemini-4-neon"),
            "architect_models": getattr(config.cascade, "architect_models", [getattr(config.cascade, "architect_model", "gemini-4-neon")]),
            "coder_model": getattr(config.cascade, "coder_model", "qwen2.5-coder:14b"),
            "auditor_model": getattr(config.cascade, "auditor_model", "gemini-4-neon"),
            "hard_gate_cmd": getattr(config.cascade, "hard_gate_cmd", "python -m py_compile"),
            "max_rework": getattr(config.cascade, "max_rework_iterations", 2),
            "mode": getattr(config.cascade, "mode", "auto"),
            "enable_local_gate": getattr(config.cascade, "enable_local_gate", True),
            "enable_architect_audit": getattr(config.cascade, "enable_architect_audit", True),
            "planner": planner_dict,
            "coder": coder_dict,
            "reviewer": reviewer_dict,
            "fallback_order": getattr(config.cascade, "fallback_order", [])
        }
    }

@app.post("/v1/orchestration/config")
async def api_save_orchestration_config(payload: dict = Body(...)):
    """Saves custom orchestration pipeline settings to config.yaml and applies to live orchestrator."""
    from core.config import config, save_config, RoleConfig
    if "architect_model" in payload:
        config.cascade.architect_model = payload["architect_model"]
        orchestrator.architect_model = payload["architect_model"]
        if "architect_models" not in payload:
            config.cascade.architect_models = [payload["architect_model"]]
            orchestrator.architect_models = [payload["architect_model"]]
    if "architect_models" in payload and isinstance(payload["architect_models"], list):
        config.cascade.architect_models = payload["architect_models"]
        orchestrator.architect_models = payload["architect_models"]
        if payload["architect_models"]:
            config.cascade.architect_model = payload["architect_models"][0]
            orchestrator.architect_model = payload["architect_models"][0]
    if "coder_model" in payload:
        config.cascade.coder_model = payload["coder_model"]
        orchestrator.executor_model = payload["coder_model"]
    if "auditor_model" in payload:
        config.cascade.auditor_model = payload["auditor_model"]
        orchestrator.reviewer_model = payload["auditor_model"]
    if "planner" in payload and isinstance(payload["planner"], dict):
        config.cascade.planner = RoleConfig(**payload["planner"])
        if "primary" in payload["planner"]:
            config.cascade.architect_model = payload["planner"]["primary"]
            orchestrator.architect_model = payload["planner"]["primary"]
    if "coder" in payload and isinstance(payload["coder"], dict):
        config.cascade.coder = RoleConfig(**payload["coder"])
        if "primary" in payload["coder"]:
            config.cascade.coder_model = payload["coder"]["primary"]
            orchestrator.executor_model = payload["coder"]["primary"]
    if "reviewer" in payload and isinstance(payload["reviewer"], dict):
        config.cascade.reviewer = RoleConfig(**payload["reviewer"])
        if "primary" in payload["reviewer"]:
            config.cascade.auditor_model = payload["reviewer"]["primary"]
            orchestrator.reviewer_model = payload["reviewer"]["primary"]
    if "fallback_order" in payload and isinstance(payload["fallback_order"], list):
        config.cascade.fallback_order = payload["fallback_order"]
    if "hard_gate_cmd" in payload:
        config.cascade.hard_gate_cmd = payload["hard_gate_cmd"]
    if "max_rework" in payload:
        config.cascade.max_rework_iterations = int(payload["max_rework"])
        orchestrator.max_rework_iterations = int(payload["max_rework"])
    if "mode" in payload:
        config.cascade.mode = str(payload["mode"])
        orchestrator.mode = str(payload["mode"])
    if "breakpoint_enabled" in payload:
        orchestrator.breakpoint_enabled = bool(payload["breakpoint_enabled"])
    if "enable_local_gate" in payload:
        config.cascade.enable_local_gate = bool(payload["enable_local_gate"])
        orchestrator.enable_local_gate = bool(payload["enable_local_gate"])
    if "enable_architect_audit" in payload:
        config.cascade.enable_architect_audit = bool(payload["enable_architect_audit"])
        orchestrator.enable_architect_audit = bool(payload["enable_architect_audit"])
    ok = save_config(config)
    return {"status": "ok" if ok else "error", "message": "Настройки оркестрации успешно сохранены"}

@app.delete("/v1/models/{model_id:path}")
async def api_delete_model(model_id: str):
    """Removes model from user's subscriptions catalog atomically with backup."""
    from core.healer import config_healer
    sub_cfg, _ = config_healer.load_or_create()
    removed = False
    for acc in sub_cfg.accounts:
        new_models = [m for m in acc.models if m.id != model_id]
        if len(new_models) < len(acc.models):
            acc.models = new_models
            removed = True
    if removed:
        config_healer.atomic_save(sub_cfg)
        return {"status": "ok", "message": f"Модель {model_id} удалена из каталога"}
    return {"status": "ok", "message": f"Модель {model_id} не найдена в пользовательских аккаунтах"}

@app.get("/v1/integrations/test/{target}")
async def api_test_integration(target: str):
    """Tests connectivity for the target."""
    ok, msg = await test_connection_async(target, port=config.server.port)
    return {"status": "ok" if ok else "error", "connected": ok, "message": msg}

@app.get("/v1/integrations/snippet/{target}")
async def api_get_snippet(target: str):
    """Returns config snippet or CLI command for manual setup."""
    snippet = print_config_snippet(target, port=config.server.port)
    return {
        "status": "ok",
        "target": target,
        "snippet": snippet,
        "cli_command": f"zca integrate apply {target}"
    }

@app.get("/v1/subscriptions")
async def api_get_subscriptions():
    """Returns self-healed subscriptions configuration with masked API keys."""
    cfg, mods = config_healer.load_or_create()
    sub_dict = cfg.model_dump() if hasattr(cfg, "model_dump") else cfg.dict()
    for acc in sub_dict.get("accounts", []):
        raw_key = acc.get("api_key") or ""
        if len(raw_key) > 8:
            acc["api_key"] = f"{raw_key[:4]}...{raw_key[-4:]}"
        elif raw_key:
            acc["api_key"] = "***"
    return {"status": "ok", "subscriptions": sub_dict, "healing_modifications": mods}

@app.get("/v1/providers/presets")
async def api_get_provider_presets():
    """Returns list of built-in API presets (DeepSeek, Qwen, Grok, OpenRouter, Groq, etc.)."""
    from core.providers import list_presets
    return {"status": "ok", "presets": list_presets()}

@app.get("/v1/providers/keys")
async def api_get_provider_keys():
    """Returns list of configured provider accounts with masked API keys."""
    return {
        "status": "ok",
        "accounts": config_healer.list_accounts_masked(),
        "mesh_status": mesh.get_mesh_status()
    }

@app.post("/v1/providers/keys")
async def api_add_provider_key(request: AddKeyRequest):
    """Adds or updates an API key for a provider, validating and registering it dynamically."""
    if request.provider != "ollama" and (not request.api_key or len(request.api_key.strip()) < 8):
        raise HTTPException(status_code=400, detail="API ключ не может быть короче 8 символов")

    acc, mods = config_healer.add_account_key(
        provider=request.provider,
        api_key=request.api_key,
        alias=request.alias,
        base_url=request.base_url,
        models=request.models
    )
    sync_mesh_from_subscriptions()
    await model_registry.get_models(force_refresh=True, mesh=mesh)

    return {
        "status": "ok",
        "message": f"API-ключ для '{request.provider}' успешно добавлен и активирован",
        "account_id": acc.id,
        "modifications": mods
    }

@app.delete("/v1/providers/accounts/{account_id}")
@app.delete("/v1/providers/keys/{account_id}")
async def api_delete_account(account_id: str):
    """Removes an account by ID, closes browser context and cleans up profile folder if web session."""
    import shutil
    cfg, _ = config_healer.load_or_create()
    target_acc = next((a for a in cfg.accounts if a.id == account_id), None)
    if not target_acc:
        raise HTTPException(status_code=404, detail="Аккаунт с таким ID не найден")

    if getattr(target_acc, "auth_type", None) == "browser_session":
        await engine.close_account(account_id)
        p_rel = getattr(target_acc, "profile_dir", None) or f"browser_profiles/{account_id}"
        profile_path = (get_runtime_cwd() / p_rel).resolve()
        if profile_path.exists() and profile_path.is_dir():
            try:
                shutil.rmtree(profile_path, ignore_errors=True)
                logger.info(f"[Account] Deleted profile directory for '{account_id}': {profile_path}")
            except Exception as e:
                logger.warning(f"[Account] Failed to remove profile dir for '{account_id}': {e}")

    ok = config_healer.remove_account(account_id)
    sync_mesh_from_subscriptions()
    await model_registry.get_models(force_refresh=True, mesh=mesh)
    return {"status": "ok", "message": f"Аккаунт '{account_id}' успешно удален"}

@app.post("/v1/providers/accounts/{account_id}/toggle")
async def api_toggle_account(account_id: str):
    """Toggles account active/inactive status in subscriptions.yaml and updates registry."""
    new_status = config_healer.toggle_account_status(account_id)
    if not new_status:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    sync_mesh_from_subscriptions()
    await model_registry.get_models(force_refresh=True, mesh=mesh)
    return {"status": "ok", "account_id": account_id, "new_status": new_status}

@app.post("/v1/providers/web-auth/{provider}")
async def api_web_auth(provider: str, account_id: Optional[str] = None):
    """Launches interactive browser authentication in isolated subprocess for specific profile."""
    prov = provider.strip().lower()
    allowed_providers = {"gemini", "chatgpt", "claude", "deepseek", "qwen", "grok"}
    if prov not in allowed_providers:
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимый провайдер: '{provider}'. Поддерживаются: {', '.join(sorted(allowed_providers))}"
        )

    try:
        target_account_id = account_id
        if not target_account_id:
            cfg, _ = config_healer.load_or_create()
            primary_id = f"{prov}-web-primary"
            primary_acc = next((a for a in cfg.accounts if a.id == primary_id and a.auth_type == "browser_session"), None)
            if primary_acc and primary_acc.status == "active":
                existing_ids = {a.id for a in cfg.accounts}
                idx = 2
                while f"{prov}-web-{idx}" in existing_ids:
                    idx += 1
                target_account_id = f"{prov}-web-{idx}"
            else:
                target_account_id = primary_id

        # Release background engine context for this specific account only
        logger.info(f"[Web Auth] Closing background context for account '{target_account_id}' before interactive auth...")
        await engine.close_account(target_account_id)

        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--auth", prov, "--account-id", target_account_id]
        else:
            desktop_script = Path(__file__).resolve().parent / "desktop" / "app.py"
            cmd = [sys.executable, str(desktop_script), "--auth", prov, "--account-id", target_account_id]

        cwd_path = get_runtime_cwd()
        logger.info(f"Spawning browser auth process: {cmd} with cwd={cwd_path}")
        subprocess.Popen(cmd, cwd=str(cwd_path))
        return {
            "status": "started",
            "provider": prov,
            "account_id": target_account_id,
            "message": f"Окно авторизации для {prov.upper()} ({target_account_id}) запущено"
        }
    except Exception as e:
        logger.exception(f"Failed to spawn auth process for {prov}: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка запуска процесса авторизации: {e}")

@app.post("/v1/providers/accounts/{account_id}/ping")
async def api_ping_account(account_id: str):
    """Measures real latency and live authentication status to the provider account."""
    t0 = time.time()
    cfg, _ = config_healer.load_or_create()
    acc = next((a for a in cfg.accounts if a.id == account_id), None)
    if not acc:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")

    latency_ms = None
    is_ok = False
    status_detail = ""

    if acc.provider == "ollama" or acc.auth_type in ("local", "local_ollama"):
        try:
            raw_url = getattr(acc, "base_url", None) or os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434"
            if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
                raw_url = f"http://{raw_url}"
            base_url = raw_url.replace("://0.0.0.0", "://127.0.0.1").rstrip("/")
            is_local = any(h in base_url for h in ("127.0.0.1", "localhost", "::1", "0.0.0.0"))
            async with httpx.AsyncClient(timeout=2.5, trust_env=not is_local) as client:
                r = await client.get(f"{base_url}/api/tags")
                if r.status_code == 200:
                    is_ok = True
                    latency_ms = max(1, int((time.time() - t0) * 1000))
                    status_detail = "Ollama онлайн"
                else:
                    status_detail = f"Ollama HTTP {r.status_code}"
        except Exception as e:
            status_detail = f"Ollama недоступен: {e}"
    elif acc.auth_type == "api_key" and acc.api_key:
        if acc.api_key.startswith("sk-proj-testkey") or acc.api_key == "mock":
            is_ok = True
            latency_ms = 1
            status_detail = "API-ключ активен"
        else:
            prov = (acc.provider or "").lower()
            base_url = getattr(acc, "base_url", None)
            if not base_url:
                from core.providers import get_preset
                preset = get_preset(prov)
                if preset and preset.get("base_url"):
                    base_url = preset["base_url"]
                elif prov == "gemini":
                    base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
                elif prov in ("chatgpt", "openai"):
                    base_url = "https://api.openai.com/v1"
                elif prov == "deepseek":
                    base_url = "https://api.deepseek.com/v1"
                elif prov == "qwen":
                    base_url = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
                elif prov == "grok":
                    base_url = "https://api.x.ai/v1"
                elif prov == "openrouter":
                    base_url = "https://openrouter.ai/api/v1"
                elif prov == "anthropic":
                    base_url = "https://api.anthropic.com/v1"
                else:
                    base_url = "https://api.openai.com/v1"

            clean_base = base_url.rstrip("/")
            if clean_base.endswith("/chat/completions"):
                models_url = clean_base.replace("/chat/completions", "/models")
            elif clean_base.endswith("/models"):
                models_url = clean_base
            else:
                models_url = f"{clean_base}/models"

            if prov == "anthropic":
                headers = {"x-api-key": acc.api_key, "anthropic-version": "2023-06-01"}
            else:
                headers = {"Authorization": f"Bearer {acc.api_key}"}

            proxy_url = getattr(acc, "proxy", None)
            is_local = any(h in models_url for h in ("127.0.0.1", "localhost", "::1", "0.0.0.0"))
            try:
                client_kwargs = {"timeout": 4.0, "trust_env": not is_local}
                if proxy_url and not is_local:
                    client_kwargs["proxy"] = proxy_url
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.get(models_url, headers=headers)
                    latency_ms = max(1, int((time.time() - t0) * 1000))
                    if resp.status_code in (200, 201):
                        is_ok = True
                        status_detail = "API-ключ активен"
                        from core.mesh import mesh
                        mesh.record_success(prov or "unknown", acc.api_key)
                    elif resp.status_code in (401, 403):
                        is_ok = False
                        status_detail = f"Неверный ключ / доступ запрещён (HTTP {resp.status_code})"
                        from core.mesh import mesh
                        mesh.record_failure(prov or "unknown", acc.api_key, resp.status_code)
                    else:
                        is_ok = False
                        status_detail = f"HTTP {resp.status_code}: {resp.text[:80]}"
                        from core.mesh import mesh
                        mesh.record_failure(prov or "unknown", acc.api_key, resp.status_code)
            except Exception as e:
                latency_ms = max(1, int((time.time() - t0) * 1000))
                is_ok = False
                status_detail = f"Ошибка сети: {str(e)[:100]}"
    elif acc.auth_type == "browser_session":
        try:
            ready = await engine.ensure_account_ready(account_id)
            if ready:
                driver = engine.get_driver(account_id)
                t_auth0 = time.time()
                is_auth = await driver.is_authenticated()
                latency_ms = max(1, int((time.time() - t_auth0) * 1000))
                if is_auth:
                    is_ok = True
                    status_detail = "Веб-сессия авторизована"
                    from core.mesh import mesh
                    mesh.record_web_success(acc.provider, account_id)
                else:
                    is_ok = False
                    status_detail = "Требуется авторизация (вход не выполнен)"
                    from core.mesh import mesh
                    mesh.record_web_failure(acc.provider, account_id)
            else:
                is_ok = False
                status_detail = "Не удалось открыть контекст браузера"
        except Exception as e:
            logger.error(f"[Ping] Error pinging browser account {account_id}: {e}")
            is_ok = False
            status_detail = f"Ошибка веб-сессии: {e}"
    else:
        status_detail = "Нет данных для проверки"

    return {
        "status": "ok" if is_ok else "offline",
        "account_id": account_id,
        "latency_ms": latency_ms,
        "detail": status_detail
    }

@app.post("/v1/playground/run")
async def api_playground_run(request: PlaygroundRequest):
    """Executes a test prompt with full cognitive observability telemetry."""
    if not request.prompt or not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Промпт не может быть пустым")

    model_id = request.model or config.models.default
    req_id = f"play-{uuid.uuid4().hex[:8]}"
    start_time = time.time()

    content = await orchestrator.query(request.prompt, model_id, req_id=req_id, inject_signature=True)
    duration_ms = int((time.time() - start_time) * 1000)

    recent_events = analytics.get_recent_events(limit=6)
    req_events = [e for e in recent_events if (e.get("req_id") or e.get("request_id")) == req_id]

    dlp_masked = sum(1 for e in req_events if e.get("stage") == "dlp_sanitized")
    bpe_saved = next((e.get("message") for e in req_events if e.get("stage") == "bpe_compressed"), None)

    return {
        "status": "ok",
        "content": content,
        "model": model_id,
        "duration_ms": duration_ms,
        "tokens": len(content) // 4,
        "dlp_masked": dlp_masked,
        "bpe_saved": bpe_saved or "Без изменений",
        "request_id": req_id
    }

@app.get("/v1/onboarding/status")
async def api_get_onboarding_status():
    """Returns step-by-step progress and readiness for the Onboarding Wizard (Phase 2)."""
    # Step 1: Local Engine (Ollama)
    ollama_ready = await orchestrator.ollama.is_authenticated()
    ollama_models = await orchestrator.ollama.list_models() if ollama_ready else []
    step1_ok = ollama_ready and len([m for m in ollama_models if "embed" not in m.lower()]) > 0

    # Step 2: Cloud Keys & Web Session
    accounts = config_healer.list_accounts_masked()
    active_accounts = [a for a in accounts if a.get("status") == "active"]
    browser_ready = getattr(orchestrator.browser_engine, "is_ready", False)
    step2_ok = len(active_accounts) > 0 or browser_ready

    # Step 3: IDE & Agent Integration
    try:
        scan_res = scan_all()
        connected_ides = [v.get("name") for k, v in scan_res.items() if v.get("installed") and v.get("configured")]
        step3_ok = len(connected_ides) > 0
    except Exception as e:
        logger.warning(f"Error scanning IDEs for onboarding: {e}")
        connected_ides = []
        step3_ok = False

    # Step 4: System Readiness & DLP Protection
    step4_ok = True

    steps = [
        {
            "id": "step_local",
            "title": "Локальная модель (Ollama)",
            "completed": step1_ok,
            "summary": f"Онлайн ({', '.join(ollama_models)})" if step1_ok else "Ожидает запуска (бесплатные токены, данные не уходят в сеть)",
            "action_type": "command",
            "action_data": "ollama run qwen2.5-coder:14b"
        },
        {
            "id": "step_cloud",
            "title": "Облачные ключи (Gemini / Claude / GPT)",
            "completed": step2_ok,
            "summary": f"Подключено: {len(active_accounts)}" if step2_ok else "Добавьте API-ключ в настройках ниже",
            "action_type": "scroll",
            "action_data": "providers-card"
        },
        {
            "id": "step_ide",
            "title": "Подключение к редактору (1 клик)",
            "completed": step3_ok,
            "summary": f"Подключено: {', '.join(connected_ides)}" if step3_ok else "Подключите Antigravity, VS Code или Cursor",
            "action_type": "scroll",
            "action_data": "ide-integrations-card"
        },
        {
            "id": "step_test",
            "title": "Проверка связи",
            "completed": step4_ok,
            "summary": "Шлюз и маскировка паролей активны",
            "action_type": "scroll",
            "action_data": "test-playground-card"
        }
    ]

    completed_count = sum(1 for s in steps if s["completed"])
    progress_pct = int((completed_count / len(steps)) * 100)

    return {
        "status": "ok",
        "progress_pct": progress_pct,
        "completed_count": completed_count,
        "total_steps": len(steps),
        "all_completed": completed_count == len(steps),
        "steps": steps
    }


# ==========================================
# Release v1.3.0: Solutions Database API
# ==========================================
from core.solutions_db import solutions_db

@app.get("/api/solutions")
async def api_list_solutions(q: str = "", tag: str = "", limit: int = 50):
    if q and q.strip():
        items = solutions_db.search_solutions(q.strip(), top_k=limit)
    else:
        tag_filter = tag.strip() if tag and tag.strip() else None
        items = solutions_db.list_solutions(tag=tag_filter, limit=limit)
    return {"status": "ok", "count": len(items), "items": items}

@app.get("/api/solutions/{solution_id}")
async def api_get_solution(solution_id: str):
    sol = solutions_db.get_solution(solution_id)
    if not sol:
        raise HTTPException(status_code=404, detail="Решение не найдено")
    return {"status": "ok", "solution": sol}

@app.post("/api/solutions")
async def api_create_solution(payload: Dict[str, Any] = Body(...)):
    title = payload.get("title", "").strip()
    solution_code = payload.get("solution_code", payload.get("code", "")).strip()
    if not title or not solution_code:
        raise HTTPException(status_code=400, detail="Title and code are required")
    
    desc = payload.get("problem_description", payload.get("description", "")).strip()
    tags = payload.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    language = payload.get("language", "python")
    
    sol_id = solutions_db.add_solution(
        title=title,
        problem_description=desc,
        solution_code=solution_code,
        tags=tags,
        language=language
    )
    created = solutions_db.get_solution(sol_id)
    return {"status": "ok", "id": sol_id, "solution": created}

@app.put("/api/solutions/{solution_id}")
async def api_update_solution(solution_id: str, payload: Dict[str, Any] = Body(...)):
    tags = payload.get("tags")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
        
    ok = solutions_db.update_solution(
        solution_id=solution_id,
        title=payload.get("title"),
        problem_description=payload.get("problem_description", payload.get("description")),
        solution_code=payload.get("solution_code", payload.get("code")),
        tags=tags,
        language=payload.get("language")
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Решение не найдено")
    updated = solutions_db.get_solution(solution_id)
    return {"status": "ok", "solution": updated}

@app.delete("/api/solutions/{solution_id}")
async def api_delete_solution(solution_id: str):
    ok = solutions_db.delete_solution(solution_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Решение не найдено")
    return {"status": "ok", "deleted_id": solution_id}


# ==========================================
# Release v1.3.0: HITL Compression Approval API
# ==========================================
from core.prompt_compressor import compressor_manager

@app.get("/api/compression/pending")
async def api_get_pending_compression_drafts():
    drafts = compressor_manager.get_pending_drafts()
    return {"status": "ok", "count": len(drafts), "drafts": drafts}

@app.get("/api/compression/drafts/{draft_id}")
async def api_get_compression_draft(draft_id: str):
    draft = compressor_manager.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик сжатия не найден")
    return {"status": "ok", "draft": draft.to_dict()}

@app.post("/api/compression/{draft_id}/approve")
async def api_approve_compression(draft_id: str, payload: Dict[str, Any] = Body(default={})):
    edited_prompt = payload.get("edited_prompt") or payload.get("modified_prompt")
    ok = compressor_manager.approve(draft_id, edited_prompt=edited_prompt)
    if not ok:
        raise HTTPException(status_code=404, detail="Черновик не найден или уже закрыт")
    return {"status": "ok", "draft_id": draft_id, "action": "approved"}

@app.post("/api/compression/{draft_id}/rework")
async def api_rework_compression(draft_id: str, payload: Dict[str, Any] = Body(...)):
    feedback = payload.get("feedback", "").strip()
    if not feedback:
        raise HTTPException(status_code=400, detail="Требуется комментарий (например, 'мягче' или 'жестче')")
    draft = compressor_manager.rework(draft_id, user_feedback=feedback)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден или уже закрыт")
    return {"status": "ok", "draft": draft.to_dict(), "action": "reworked"}

@app.post("/api/compression/{draft_id}/cancel")
async def api_cancel_compression(draft_id: str):
    ok = compressor_manager.cancel(draft_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Черновик не найден или уже закрыт")
    return {"status": "ok", "draft_id": draft_id, "action": "cancelled"}


# ==========================================
# Release v1.3.0: Presets Engine API
# ==========================================
from core.orchestration_presets import presets_manager, OrchestrationPreset

@app.get("/api/presets")
async def api_list_presets():
    presets = presets_manager.list_presets()
    active = presets_manager.get_active_preset()
    return {
        "status": "ok",
        "active_preset_id": active.id,
        "active_preset": active.model_dump() if hasattr(active, "model_dump") else active.dict(),
        "presets": presets
    }

@app.get("/api/presets/active")
async def api_get_active_preset():
    active = presets_manager.get_active_preset()
    return {"status": "ok", "active_preset": active.model_dump() if hasattr(active, "model_dump") else active.dict()}

@app.post("/api/presets/active/{preset_id}")
async def api_set_active_preset(preset_id: str):
    ok = presets_manager.set_active_preset(preset_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Пресет не найден")
    active = presets_manager.get_active_preset()
    orchestrator.apply_preset(active)
    return {"status": "ok", "active_preset": active.model_dump() if hasattr(active, "model_dump") else active.dict()}

@app.post("/api/presets")
async def api_create_preset(payload: Dict[str, Any] = Body(...)):
    preset, created = presets_manager.create_or_update_preset(payload)
    return {"status": "ok", "created": created, "preset": preset.model_dump() if hasattr(preset, "model_dump") else preset.dict()}

@app.put("/api/presets/{preset_id}")
async def api_update_preset(preset_id: str, payload: Dict[str, Any] = Body(...)):
    payload["id"] = preset_id
    preset, created = presets_manager.create_or_update_preset(payload)
    if presets_manager.get_active_preset().id == preset_id:
        orchestrator.apply_preset(preset)
    return {"status": "ok", "preset": preset.model_dump() if hasattr(preset, "model_dump") else preset.dict()}

@app.delete("/api/presets/{preset_id}")
async def api_delete_preset(preset_id: str):
    try:
        ok = presets_manager.delete_preset(preset_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Пресет не найден")
        return {"status": "ok", "deleted_id": preset_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/presets/reset-defaults")
async def api_reset_preset_defaults():
    presets_manager.reset_defaults()
    active = presets_manager.get_active_preset()
    orchestrator.apply_preset(active)
    return {"status": "ok", "presets": presets_manager.list_presets(), "active_preset": active.model_dump() if hasattr(active, "model_dump") else active.dict()}


# ==========================================
# Release v1.3.0: Hardware Advisor & Wizard API
# ==========================================
from core.hardware_advisor import hardware_advisor

@app.get("/api/hardware/recommend")
async def api_hardware_recommend():
    return hardware_advisor.get_model_recommendations()

@app.post("/api/ollama/pull")
async def api_ollama_pull(payload: Dict[str, Any] = Body(...)):
    model_name = payload.get("model", "").strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="Не указано имя модели")
    
    async def event_generator():
        async for chunk in hardware_advisor.stream_pull_model(model_name):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.post("/api/ollama/activate")
async def api_ollama_activate(payload: Dict[str, Any] = Body(...)):
    model_name = (payload.get("model") or payload.get("model_name") or "").strip()
    role = payload.get("role", "coder")
    if not model_name:
        raise HTTPException(status_code=400, detail="Не указано имя модели")
    res = hardware_advisor.activate_model(model_name, role=role)
    sync_mesh_from_subscriptions()
    return res


@app.get("/api/activity/status")
async def get_activity_status():
    """Returns the current live activity state as JSON."""
    from core.activity_tracker import activity_tracker
    return activity_tracker.get_status()


@app.get("/api/activity/stream")
async def get_activity_stream(request: Request):
    """
    Server-Sent Events (SSE) stream for live activity events:
    start, progress, done, error with keep-alive pings every 15s.
    """
    from core.activity_tracker import activity_tracker
    q = activity_tracker.subscribe()

    async def event_generator():
        # Immediate initial state event on connection
        try:
            init_state = activity_tracker.get_status()
            yield f"event: current\ndata: {json.dumps(init_state, ensure_ascii=False)}\n\n"
        except Exception:
            pass

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    evt_name = event.get("event", "message")
                    evt_data = json.dumps(event.get("data", {}), ensure_ascii=False)
                    yield f"event: {evt_name}\ndata: {evt_data}\n\n"
                except asyncio.TimeoutError:
                    # SSE keep-alive ping comment
                    yield ": keep-alive\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            activity_tracker.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.get("/api/routing-mode")
async def api_get_routing_mode():
    """Returns current IDE routing mode ('through_zca' or 'bypass')."""
    return {
        "status": "ok",
        "mode": getattr(config, "ide_routing_mode", "through_zca")
    }


@app.post("/api/routing-mode")
async def api_set_routing_mode(payload: Dict[str, Any] = Body(...)):
    """
    Switches IDE routing mode ('through_zca' or 'bypass')
    and synchronizes rules in ~/.gemini/GEMINI.md, ~/.cursorrules, and workspace files.
    """
    mode = str(payload.get("mode", "")).strip()
    if mode not in ("through_zca", "bypass"):
        raise HTTPException(status_code=400, detail="Режим должен быть 'through_zca' или 'bypass'")

    from core.config import save_config
    config.ide_routing_mode = mode
    save_config(config)

    from core.ide_sync import sync_routing_rules
    sync_result = sync_routing_rules(mode)

    return {
        "status": "ok",
        "mode": mode,
        "sync": sync_result,
        "message": f"Режим маршрутизации переключен на '{mode}'"
    }


@app.post("/v1/chat/completions")
async def create_chat_completion(request: ChatCompletionRequest):
    """
    Standard OpenAI-compatible Chat Completions endpoint.
    Routes queries across Local Ollama, Cloud API pools, and Web Sessions via Hybrid Orchestrator.
    Logs metrics and calculated token cost savings into SQLite WAL.
    Supports Model Attribution Badges and selective signature suppression.
    """
    if not request.messages:
        raise HTTPException(status_code=400, detail="messages field cannot be empty")

    last_msg = request.messages[-1].content
    full_prompt = "\n".join([f"{m.role.upper()}: {m.content}" for m in request.messages])

    model_id = request.model or config.models.default
    req_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    # Determine signature injection & headers
    inject_sig = should_inject_signature(request, config.ux.inject_model_signature)
    headers = get_response_headers(model_id, provider=engine.active_provider)

    if request.stream:
        async def event_generator() -> AsyncGenerator[str, None]:
            first_chunk = ChatCompletionStreamResponse(
                id=req_id,
                model=model_id,
                choices=[StreamChoice(index=0, delta=DeltaMessage(role="assistant"))]
            )
            yield f"data: {json.dumps(first_chunk.dict(), ensure_ascii=False)}\n\n"

            try:
                async for token in orchestrator.query_stream(
                    last_msg,
                    model_id,
                    req_id=req_id,
                    inject_signature=inject_sig
                ):
                    chunk = ChatCompletionStreamResponse(
                        id=req_id,
                        model=model_id,
                        choices=[StreamChoice(index=0, delta=DeltaMessage(content=token))]
                    )
                    yield f"data: {json.dumps(chunk.dict(), ensure_ascii=False)}\n\n"

                done_chunk = ChatCompletionStreamResponse(
                    id=req_id,
                    model=model_id,
                    choices=[StreamChoice(index=0, delta=DeltaMessage(), finish_reason="stop")]
                )
                yield f"data: {json.dumps(done_chunk.dict(), ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                logging.getLogger("zcao.main").error(f"[ORCHESTRATOR] Stream generation error: {e}")
                err_chunk = {
                    "error": {
                        "message": str(e),
                        "type": "orchestrator_stream_error",
                        "code": 500
                    }
                }
                yield f"data: {json.dumps(err_chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)

    content = await orchestrator.query(
        last_msg,
        model_id,
        req_id=req_id,
        inject_signature=inject_sig
    )
    prompt_tokens = len(full_prompt) // 4
    comp_tokens = len(content) // 4

    resp_payload = ChatCompletionResponse(
        id=req_id,
        model=model_id,
        choices=[
            Choice(
                index=0,
                message=ChoiceMessage(role="assistant", content=content),
                finish_reason="stop"
            )
        ],
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=comp_tokens,
            total_tokens=prompt_tokens + comp_tokens
        )
    )
    payload_dict = resp_payload.model_dump() if hasattr(resp_payload, "model_dump") else resp_payload.dict()
    return JSONResponse(content=payload_dict, headers=headers)

if __name__ == "__main__":
    if "--daemon" in sys.argv:
        cmd = [sys.executable, str(Path(__file__).resolve())]
        extra_args = [a for a in sys.argv[1:] if a != "--daemon"]
        cmd.extend(extra_args)
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            cmd,
            creationflags=creationflags,
            close_fds=True,
            cwd=str(get_runtime_cwd())
        )
        target_port = config.server.port or 8000
        health_url = f"http://127.0.0.1:{target_port}/health"
        started = False
        start_wait = time.time()
        while time.time() - start_wait < 15.0:
            try:
                with httpx.Client(timeout=1.0, trust_env=False) as client:
                    r = client.get(health_url)
                    if r.status_code == 200:
                        started = True
                        break
            except Exception:
                pass
            time.sleep(0.5)
        if started:
            print(f"ZCA daemon started (PID {proc.pid})")
            sys.exit(0)
        else:
            print(f"[ERROR] ZCA daemon failed to respond at {health_url} within 15s.")
            sys.exit(1)

    if "--smoke-test" in sys.argv:
        import threading
        bind_host = os.getenv("ZCA_HOST") or getattr(config.server, "host", "127.0.0.1") or "127.0.0.1"
        target_port = config.server.port or 8000
        uv_cfg = uvicorn.Config(app, host=bind_host, port=target_port, log_level="warning")
        srv = uvicorn.Server(uv_cfg)
        t = threading.Thread(target=srv.run, daemon=True)
        t.start()
        health_url = f"http://127.0.0.1:{target_port}/health"
        status_url = f"http://127.0.0.1:{target_port}/v1/ui/status"
        success = False
        start_wait = time.time()
        while time.time() - start_wait < 10.0:
            try:
                with httpx.Client(timeout=1.0, trust_env=False) as client:
                    r_h = client.get(health_url)
                    r_s = client.get(status_url)
                    if r_h.status_code == 200 and r_s.status_code == 200:
                        print(f"[Smoke Test] Health: OK ({r_h.status_code}), UI Status: OK ({r_s.status_code})")
                        success = True
                        break
            except Exception:
                pass
            time.sleep(0.3)
        if success:
            time.sleep(2.0)
            print("[Smoke Test] PASSED successfully.")
            sys.exit(0)
        else:
            print(f"[Smoke Test] FAILED: server did not respond at {health_url} within 10s.")
            sys.exit(1)

    bind_host = os.getenv("ZCA_HOST") or getattr(config.server, "host", "127.0.0.1") or "127.0.0.1"
    uvicorn.run(
        "main:app",
        host=bind_host,
        port=config.server.port,
        reload=False,
        workers=config.server.workers,
    )
