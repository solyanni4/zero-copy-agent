"""
Native Model Context Protocol (MCP) Server for zero-copy-agent.
Provides an SSE-based and HTTP-based JSON-RPC 2.0 interface compliant with the MCP specification.
Enables Google Antigravity, Cursor IDE, Claude Desktop, and Cline to call zero-copy-agent tools.
"""
import asyncio
import json
import uuid
import logging
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
                }
            },
            "required": ["goal"]
        }
    }
]

async def execute_tool(name: str, arguments: Dict[str, Any]) -> str:
    """Executes the requested tool and returns the text response."""
    global _orchestrator
    if not _orchestrator:
        return "[ERROR] Ошибка: Оркестратор шлюза не инициализирован."

    if name == "cascade_architect":
        task = arguments.get("task", "")
        if not task:
            return "Ошибка: параметр 'task' обязателен."
        try:
            return await asyncio.wait_for(
                _orchestrator.query(task, "cascade:architect", req_id=f"mcp-{uuid.uuid4().hex[:8]}"),
                timeout=180.0
            )
        except asyncio.TimeoutError:
            logger.error(f"[MCP] Timeout executing tool '{name}' after 180s")
            return "[TIMEOUT] Превышено время ожидания ответа инструмента 'cascade_architect' (таймаут 180 секунд). Попробуйте разбить задачу на части или уменьшить число итераций каскада."

    elif name == "local_gpu_code":
        prompt = arguments.get("prompt", "")
        model = arguments.get("model", "qwen2.5-coder:14b")
        if not prompt:
            return "Ошибка: параметр 'prompt' обязателен."
        return await _orchestrator.query(prompt, f"ollama/{model}", req_id=f"mcp-{uuid.uuid4().hex[:8]}")

    elif name == "gemini_deep_analysis":
        prompt = arguments.get("prompt", "")
        if not prompt:
            return "Ошибка: параметр 'prompt' обязателен."
        try:
            return await asyncio.wait_for(
                _orchestrator.query(prompt, "gemini-4-neon", req_id=f"mcp-{uuid.uuid4().hex[:8]}"),
                timeout=180.0
            )
        except asyncio.TimeoutError:
            logger.error(f"[MCP] Timeout executing tool '{name}' after 180s")
            return "[TIMEOUT] Превышено время ожидания ответа инструмента 'gemini_deep_analysis' (таймаут 180 секунд). Попробуйте повторить запрос с более компактным промптом."

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
        return (
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
        return (
            f"**Результат L1-сжатия:**\n"
            f"- До: ~{orig_t} токенов | После: ~{comp_t} токенов | Сэкономлено: {saved} токенов ({pct:.1f}%)\n\n"
            f"```\n{compressed}\n```"
        )

    elif name == "plan_and_execute_task":
        goal = arguments.get("goal", "")
        workspace_dir = arguments.get("workspace_dir")
        resume = arguments.get("resume", True)
        if not goal:
            return "Ошибка: параметр 'goal' обязателен."
        try:
            return await asyncio.wait_for(
                _orchestrator.plan_and_execute_task(goal, workspace_dir=workspace_dir, resume=resume),
                timeout=300.0
            )
        except asyncio.TimeoutError:
            logger.error(f"[MCP] Timeout executing tool '{name}' after 300s")
            return "[TIMEOUT] Выполнение крупной задачи превысило лимит 300 секунд. Прогресс сохранен в .zca/task_plan.json. Вы можете продолжить вызовом с resume=True."

    return f"Неизвестный инструмент: '{name}'"

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
