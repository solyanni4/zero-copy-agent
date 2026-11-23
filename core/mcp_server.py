"""
Native Model Context Protocol (MCP) Server for zero-copy-agent.
Provides an SSE-based and HTTP-based JSON-RPC 2.0 interface compliant with the MCP specification.
Enables Google Antigravity, Cursor IDE, Claude Desktop, and Cline to call zero-copy-agent tools.
"""
import asyncio
import json
import time
import uuid
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from core import __version__
from core.storage import analytics
from core.compression import compressor

logger = logging.getLogger("zcao.mcp")

mcp_router = APIRouter(tags=["mcp"])

# Active SSE client sessions: session_id -> asyncio.Queue
active_sessions: Dict[str, asyncio.Queue] = {}

# Reference to the orchestrator instance (injected from main.py)
_orchestrator = None

def init_mcp(orchestrator_instance):
    """Initializes the MCP router with the running HybridOrchestrator."""
    global _orchestrator
    _orchestrator = orchestrator_instance

TOOLS_MANIFEST = [
    {
        "name": "cascade_architect",
        "description": "Запускает замкнутый двухэтапный каскад 'Архитектор': Шаг 1 (проектирование системного плана через облачную модель/API-ключ) -> Шаг 2 (генерация чистого кода через локальную GPU Ollama или быструю модель).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Описание архитектурной задачи, сервиса, функции или рефакторинга."
                },
                "preset": {
                    "type": "string",
                    "description": "Оркестрационный пресет: 'auto', 'basic', 'thinker' или пользовательский ID."
                }
            },
            "required": ["task"]
        }
    },
    {
        "name": "local_gpu_code",
        "description": "Мгновенное локальное исполнение кода на GPU через Ollama (Qwen2.5-Coder 14B) с нулевой задержкой, 100% приватностью и без платы за токены.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Инструкция или задача по написанию, анализу или оптимизации кода."
                },
                "model": {
                    "type": "string",
                    "description": "Имя локальной модели (по умолчанию 'qwen2.5-coder:14b').",
                    "default": "qwen2.5-coder:14b"
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "gemini_deep_analysis",
        "description": "Отправляет запрос в модель Gemini Deep Analysis для углублённого логического анализа и аудита архитектуры.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Аналитический или архитектурный запрос."
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "get_token_savings",
        "description": "Возвращает текущие метрики экономии токенов и рассчитанную сумму в долларах ($), сэкономленную относительно официальных API OpenAI, Anthropic и Google.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "compress_code_context",
        "description": "Выполняет интеллектуальное L1-сжатие контекста кода (устранение избыточных пустых строк, хвостовых пробелов и мусора) с экономией до 35% токенов.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Исходный код или текстовый контекст для сжатия."
                }
            },
            "required": ["code"]
        }
    },
    {
        "name": "plan_and_execute_task",
        "description": "Автоматическая декомпозиция и пошаговое выполнение крупных многофайловых задач по модели DAG-Planner + Local Worker с AST/Test-гейтами и Final Integrator. Поддерживает контрольные точки (.zca/task_plan.json) и возобновление (resume).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Цель крупной задачи (например: 'Добавить систему аутентификации JWT и тесты к ней')."
                },
                "workspace_dir": {
                    "type": "string",
                    "description": "Абсолютный или относительный путь к рабочей директории проекта (по умолчанию текущая)."
                },
                "resume": {
                    "type": "boolean",
                    "description": "Возобновить выполнение с первого незавершенного шага из сохраненного чекпоинта .zca/task_plan.json (по умолчанию true).",
                    "default": True
                },
                "preset": {
                    "type": "string",
                    "description": "Оркестрационный пресет: 'auto', 'basic', 'thinker' или пользовательский ID."
                }
            },
            "required": ["goal"]
        }
    }
]

WORKING_TOOLS = {"cascade_architect", "local_gpu_code", "gemini_deep_analysis", "plan_and_execute_task"}

def _is_mcp_badge_enabled() -> bool:
    try:
        from core.config import config
        if hasattr(config, "ux") and hasattr(config.ux, "show_mcp_badge"):
            return bool(config.ux.show_mcp_badge)
        return bool(getattr(config, "show_mcp_badge", True))
    except Exception:
        return True

def _get_active_preset_name() -> str:
    global _orchestrator
    try:
        if _orchestrator and hasattr(_orchestrator, "active_preset") and _orchestrator.active_preset:
            return getattr(_orchestrator.active_preset, "id", None) or getattr(_orchestrator.active_preset, "name", "auto")
        from core.config import config
        return getattr(config.cascade, "active_preset", "auto")
    except Exception:
        return "auto"

async def execute_tool(name: str, arguments: Dict[str, Any]) -> str:
    """Executes the requested tool and returns the text response."""
    global _orchestrator
    if not _orchestrator:
        return "[ERROR] Ошибка: Оркестратор шлюза не инициализирован."

    start_time = time.time()
    tool_model = "system"
    if name == "local_gpu_code":
        tool_model = f"ollama/{arguments.get('model', 'qwen2.5-coder:14b')}"
    elif name == "cascade_architect":
        tool_model = getattr(_orchestrator, "cascade_architect_model", "cascade:architect")
    elif name == "gemini_deep_analysis":
        tool_model = "gemini-4-neon"
    elif name == "plan_and_execute_task":
        tool_model = getattr(_orchestrator, "cascade_executor_model", "ollama/qwen2.5-coder:14b")

    initial_step = f"Executing {name}..."
    if name == "plan_and_execute_task":
        initial_step = "Planning and executing task..."
    elif name == "local_gpu_code":
        initial_step = "Generating code with local GPU..."
    elif name == "cascade_architect":
        initial_step = "Architecting solution..."

    try:
        from core.activity_tracker import activity_tracker
        await activity_tracker.start_activity(tool=name, model=tool_model, step=initial_step)
    except Exception as e:
        logger.debug(f"[MCP] Failed to start activity tracking: {e}")

    success = True
    error_msg = None
    result_text = ""
    local_tokens = 0

    try:
        if name == "cascade_architect":
            task = arguments.get("task", "")
            preset = arguments.get("preset")
            if not task:
                result_text = "Ошибка: параметр 'task' обязателен."
                success = False
            else:
                if preset and _orchestrator:
                    try:
                        from core.orchestration_presets import presets_manager
                        p_obj = presets_manager.get_preset(preset)
                        if p_obj:
                            _orchestrator.apply_preset(p_obj)
                    except Exception as e:
                        logger.debug(f"[MCP] Failed to apply preset '{preset}': {e}")
                try:
                    result_text = await asyncio.wait_for(
                        _orchestrator.query(task, "cascade:architect", req_id=f"mcp-{uuid.uuid4().hex[:8]}"),
                        timeout=180.0
                    )
                    if "ollama" in tool_model.lower() or "local" in tool_model.lower():
                        local_tokens = max(1, len(result_text) // 4)
                except asyncio.TimeoutError:
                    success = False
                    error_msg = "Timeout 180s"
                    logger.error(f"[MCP] Timeout executing tool '{name}' after 180s")
                    result_text = "[TIMEOUT] Превышено время ожидания ответа инструмента 'cascade_architect' (таймаут 180 секунд). Попробуйте разбить задачу на части или уменьшить число итераций каскада."

        elif name == "local_gpu_code":
            prompt = arguments.get("prompt", "")
            model = arguments.get("model", "qwen2.5-coder:14b")
            tool_model = f"ollama/{model}"
            if not prompt:
                result_text = "Ошибка: параметр 'prompt' обязателен."
                success = False
            else:
                result_text = await _orchestrator.query(prompt, tool_model, req_id=f"mcp-{uuid.uuid4().hex[:8]}")
                local_tokens = max(1, len(result_text) // 4)

        elif name == "gemini_deep_analysis":
            prompt = arguments.get("prompt", "")
            if not prompt:
                result_text = "Ошибка: параметр 'prompt' обязателен."
                success = False
            else:
                rag_prompt = prompt
                try:
                    from core.solutions_db import solutions_db
                    rels = solutions_db.search_solutions(prompt, top_k=3)
                    if rels:
                        ctx = solutions_db.format_context_prompt(rels)
                        rag_prompt = f"{ctx}\n\nЗапрос:\n{prompt}"
                        for r in rels:
                            solutions_db.increment_usage(r["id"])
                except Exception as e:
                    logger.debug(f"[MCP] Solutions RAG lookup warning: {e}")
                try:
                    result_text = await asyncio.wait_for(
                        _orchestrator.query(rag_prompt, "gemini-4-neon", req_id=f"mcp-{uuid.uuid4().hex[:8]}"),
                        timeout=180.0
                    )
                    local_tokens = 0
                except asyncio.TimeoutError:
                    success = False
                    error_msg = "Timeout 180s"
                    logger.error(f"[MCP] Timeout executing tool '{name}' after 180s")
                    result_text = "[TIMEOUT] Превышено время ожидания ответа инструмента 'gemini_deep_analysis' (таймаут 180 секунд). Попробуйте повторить запрос с более компактным промптом."

        elif name == "get_token_savings":
            summary = analytics.get_summary()
            local_status = "Офлайн [OFFLINE]"
            if _orchestrator and getattr(_orchestrator, "ollama", None):
                try:
                    if await _orchestrator.ollama.is_authenticated():
                        models = await _orchestrator.ollama.list_models()
                        if models:
                            local_status = f"{models[0]} (Онлайн [OK])"
                        else:
                            local_status = "Ollama активен (0 моделей) [OFFLINE]"
                except Exception:
                    pass
            result_text = (
                f"**Метрики экономии Zero-Copy Agent:**\n"
                f"- Сэкономлено на API: **${summary.get('total_saved_usd', 0.0):.2f}** (Сегодня: ${summary.get('today_saved_usd', 0.0):.2f})\n"
                f"- Всего токенов: **{summary.get('total_tokens', 0):,}**\n"
                f"- Запросов выполнено: **{summary.get('total_requests', 0)}**\n"
                f"- Локальный GPU: **{local_status}**"
            )

        elif name == "compress_code_context":
            code = arguments.get("code", "")
            compressed, orig_t, comp_t = compressor.compress_text(code)
            saved = orig_t - comp_t
            pct = (saved / orig_t * 100) if orig_t > 0 else 0
            result_text = (
                f"**Результат L1-сжатия:**\n"
                f"- До: ~{orig_t} токенов | После: ~{comp_t} токенов | Сэкономлено: {saved} токенов ({pct:.1f}%)\n\n"
                f"```\n{compressed}\n```"
            )

        elif name == "plan_and_execute_task":
            goal = arguments.get("goal", "")
            workspace_dir = arguments.get("workspace_dir")
            resume = arguments.get("resume", True)
            preset = arguments.get("preset")
            if not goal:
                result_text = "Ошибка: параметр 'goal' обязателен."
                success = False
            else:
                if preset and _orchestrator:
                    try:
                        from core.orchestration_presets import presets_manager
                        p_obj = presets_manager.get_preset(preset)
                        if p_obj:
                            _orchestrator.apply_preset(p_obj)
                    except Exception as e:
                        logger.debug(f"[MCP] Failed to apply preset '{preset}': {e}")
                try:
                    result_text = await asyncio.wait_for(
                        _orchestrator.plan_and_execute_task(goal, workspace_dir=workspace_dir, resume=resume),
                        timeout=300.0
                    )
                    local_tokens = max(1, len(result_text) // 4)
                except asyncio.TimeoutError:
                    success = False
                    error_msg = "Timeout 300s"
                    logger.error(f"[MCP] Timeout executing tool '{name}' after 300s")
                    result_text = "[TIMEOUT] Выполнение крупной задачи превысило лимит 300 секунд. Прогресс сохранен в .zca/task_plan.json. Вы можете продолжить вызовом с resume=True."

        else:
            result_text = f"Неизвестный инструмент: '{name}'"
            success = False

        # Add signature badge for working tools if enabled
        if name in WORKING_TOOLS and _is_mcp_badge_enabled():
            elapsed = max(0.01, round(time.time() - start_time, 2))
            active_preset = _get_active_preset_name()
            steps_info = ""
            if name == "plan_and_execute_task":
                try:
                    ws_dir = arguments.get("workspace_dir")
                    ws_path = Path(ws_dir).resolve() if ws_dir else Path.cwd()
                    plan_file = ws_path / ".zca" / "task_plan.json"
                    if plan_file.exists():
                        with open(plan_file, "r", encoding="utf-8") as pf:
                            pdata = json.load(pf)
                            steps = pdata.get("steps", [])
                            if steps:
                                comp_c = sum(1 for s in steps if s.get("status") == "completed")
                                steps_info = f" | Steps: {comp_c}/{len(steps)}"
                except Exception:
                    pass

            badge = f"[⚡ ZCA | Tool: {name} | Preset: {active_preset} | Model: {tool_model}{steps_info} | Local tokens: {local_tokens} | Time: {elapsed:.1f}s]"
            if not result_text.startswith("[⚡ ZCA"):
                result_text = f"{badge}\n\n{result_text}"

        return result_text

    except Exception as e:
        success = False
        error_msg = str(e)
        logger.exception(f"[MCP] Exception executing tool '{name}': {e}")
        return f"[ERROR] Ошибка выполнения инструмента '{name}': {e}"

    finally:
        try:
            from core.activity_tracker import activity_tracker
            elapsed_final = max(0.01, round(time.time() - start_time, 2))
            summary_txt = f"{name} завершен за {elapsed_final:.1f}s" if success else f"{name} завершился с ошибкой"
            await activity_tracker.finish_activity(
                success=success,
                summary=summary_txt,
                error=error_msg,
                tokens=local_tokens
            )
        except Exception as e:
            logger.debug(f"[MCP] Failed to finish activity tracking: {e}")

async def handle_jsonrpc(req: Dict[str, Any], session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Handles standard MCP JSON-RPC 2.0 messages."""
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params", {})

    logger.debug(f"[MCP] Handling method: {method} (id={req_id})")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "zero-copy-agent",
                    "version": __version__
                }
            }
        }

    elif method in ("notifications/initialized", "initialized"):
        logger.info("[MCP] Client initialized session successfully.")
        return None

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": TOOLS_MANIFEST
            }
        }

    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        try:
            result_text = await execute_tool(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": result_text
                        }
                    ]
                }
            }
        except Exception as e:
            logger.error(f"[MCP] Tool execution error ({tool_name}): {e}", exc_info=True)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32603,
                    "message": f"Ошибка исполнения инструмента: {str(e)}"
                }
            }

    elif method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {}
        }

    # Unknown method
    if req_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Метод не найден: {method}"
            }
        }
    return None

@mcp_router.get("/sse")
@mcp_router.get("/mcp/sse")
async def mcp_sse_endpoint(request: Request):
    """
    Standard Model Context Protocol (MCP) Server-Sent Events (SSE) connection endpoint.
    Used by Google Antigravity, Cursor, and Claude Desktop.
    """
    session_id = str(uuid.uuid4())
    queue = asyncio.Queue()
    active_sessions[session_id] = queue
    logger.info(f"[MCP] New SSE client connected: session {session_id}")

    async def event_generator():
        try:
            # 1. Send initial 'endpoint' event as mandated by MCP SSE specification
            endpoint_url = f"/messages?session_id={session_id}"
            yield f"event: endpoint\ndata: {endpoint_url}\n\n"

            # 2. Stream message events pushed to the queue
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=10.0)
                    yield f"event: message\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive comment ping
                    yield ": keep-alive\n\n"
        finally:
            active_sessions.pop(session_id, None)
            logger.info(f"[MCP] SSE client disconnected: session {session_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@mcp_router.post("/messages")
async def mcp_messages_endpoint(request: Request):
    """
    Receives JSON-RPC 2.0 messages from SSE-connected clients.
    """
    session_id = request.query_params.get("session_id") or request.query_params.get("sessionId")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    response_msg = await handle_jsonrpc(body, session_id)

    # If connected to SSE session, forward response to the SSE stream
    if session_id and session_id in active_sessions and response_msg:
        await active_sessions[session_id].put(response_msg)

    # Also return response in HTTP body for maximum client compatibility
    if response_msg:
        return JSONResponse(status_code=200, content=response_msg)
    return Response(status_code=202)

@mcp_router.post("/mcp")
async def mcp_direct_jsonrpc(request: Request):
    """
    Stateless HTTP JSON-RPC endpoint for modern MCP clients.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    response_msg = await handle_jsonrpc(body)
    if response_msg:
        return JSONResponse(status_code=200, content=response_msg)
    return Response(status_code=204)
