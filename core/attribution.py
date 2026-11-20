"""
Model Attribution & Display Registry Module for zero-copy-agent.
Provides human-readable display names, cascade attribution chains,
header metadata generation, and context-aware signature injection rules.
"""

from typing import Dict, Any, Optional, List

# Core dictionary mapping technical IDs to clean, human-readable display names
DISPLAY_NAME_REGISTRY: Dict[str, str] = {
    # Gemini
    "gemini-advanced": "Gemini Argon (Web 🟢)",
    "gemini-4-neon": "Gemini 4 Neon (Deeper Analysis 🧠)",
    "gemini-2.5-flash": "Gemini 2.5 Flash",
    "gemini-2.5-pro": "Gemini 2.5 Pro",
    "gemini-pro": "Gemini Pro",
    
    # OpenAI
    "gpt-4o": "GPT Sol (ChatGPT Plus)",
    "chatgpt-plus": "GPT Sol (ChatGPT Plus)",
    "gpt-4o-mini": "GPT Sol Mini",
    "o1": "GPT Sol Reasoning (o1)",
    "o3-mini": "GPT Sol Fast Reasoning (o3-mini)",
    
    # Anthropic
    "claude-3-7-sonnet": "Claude 3.7 Sonnet (Thinking)",
    "claude-3-5-sonnet": "Claude 3.5 Sonnet",
    "claude-pro": "Claude Pro",
    "claude-3-haiku": "Claude 3 Haiku",
    
    # Local GPU (Ollama)
    "qwen2.5-coder:14b": "Qwen2.5-Coder 14B (Local GPU 🦙)",
    "qwen2.5-coder:7b": "Qwen2.5-Coder 7B (Local GPU 🦙)",
    "qwen2.5-coder:1.5b": "Qwen2.5-Coder 1.5B (Local GPU 🦙)",
    "qwen2.5-coder": "Qwen2.5-Coder (Local GPU 🦙)",
    "qwen2.5:0.5b": "Qwen2.5 0.5B (Local GPU 🦙)",
    "nomic-embed-text:latest": "Nomic Embeddings (Local)",
    
    # Dynamic aliases
    "tier:local-fast": "Local GPU Fast (Qwen2.5-Coder 🦙)",
    "tier:coding-top": "Coding Top (Qwen2.5-Coder 14B 🦙)",
    "tier:reasoning-top": "Reasoning Top (Gemini 4 Neon 🧠)",
    
    # Cascades
    "cascade:architect": "Cascade «Архитектор» (Blueprint ➔ Code)",
    
    # Testing
    "mock-test": "Zero-Copy Mock Engine (Instant Test)",
}

def get_display_name(model_id: str, custom_overrides: Optional[Dict[str, str]] = None) -> str:
    """
    Returns clean human-readable name for any model ID.
    Supports exact registry lookup, user overrides, and heuristics for unlisted models.
    """
    if custom_overrides and model_id in custom_overrides:
        return custom_overrides[model_id]

    if model_id in DISPLAY_NAME_REGISTRY:
        return DISPLAY_NAME_REGISTRY[model_id]

    # Handle prefixed names (e.g. ollama/model, custom aliases)
    raw = model_id.lower()
    if raw.startswith("ollama/"):
        sub_name = model_id.split("/", 1)[1]
        return f"{sub_name} (Local GPU 🦙)"

    if "qwen" in raw:
        return f"{model_id} (Local GPU 🦙)"
    if "gemini" in raw:
        return f"{model_id} (Google)"
    if "claude" in raw:
        return f"{model_id} (Anthropic)"
    if "gpt" in raw:
        return f"{model_id} (OpenAI)"

    return model_id

def format_badge(
    model_id: str,
    role: Optional[str] = None,
    provider: Optional[str] = None,
    custom_overrides: Optional[Dict[str, str]] = None
) -> str:
    """Formats markdown badge for chat messages and notifications."""
    display = get_display_name(model_id, custom_overrides)
    if role:
        return f"⚡ **[zero-copy-agent | {role}: {display}]**\n\n"
    return f"⚡ **[zero-copy-agent | {display}]**\n\n"

def format_cascade_badge(
    architect_id: str,
    executor_id: str,
    status: str = "PASS",
    iterations: int = 1,
    custom_overrides: Optional[Dict[str, str]] = None
) -> str:
    """Formats attribution for cascade pipelines with audit status and iterations."""
    arch_display = get_display_name(architect_id, custom_overrides)
    exec_display = get_display_name(executor_id, custom_overrides)
    if status == "PASS":
        iter_str = f" ({iterations} итер.)" if iterations > 1 else ""
        return f"⚡ **[ZCAO Cascade: Архитектор ({arch_display}) ➔ Исполнитель ({exec_display}) | Аудит: PASS{iter_str}]**\n\n"
    elif status == "ESCALATED":
        return f"⚠️ **[ZCAO Cascade: Архитектор ({arch_display}) ➔ Исполнитель ({exec_display}) | Аудит: ESCALATED ({iterations} итер.)]**\n\n"
    return f"⚡ **[ZCAO Cascade: Архитектор ({arch_display}) ➔ Исполнитель ({exec_display})]**\n\n"

def format_meta_badge_html(model_id: str, role: Optional[str] = None) -> str:
    """HTML tag badge for Web UI cards and event viewer."""
    display = get_display_name(model_id)
    title = f"{role}: {display}" if role else display
    return f'<span class="badge badge-model">{title}</span>'

def get_response_headers(
    model_id: str,
    provider: Optional[str] = None,
    role: Optional[str] = None,
    custom_overrides: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    """Generates standard X-ZCA HTTP headers for OpenAI API responses (strictly Latin-1 safe)."""
    display = get_display_name(model_id, custom_overrides)

    def _safe_header(s: str) -> str:
        if not s:
            return ""
        try:
            s.encode("latin-1")
            return s
        except UnicodeEncodeError:
            clean = s.encode("ascii", "ignore").decode("ascii").strip()
            return clean or "AI Model"

    headers = {
        "X-ZCA-Model": _safe_header(display),
        "X-ZCA-Model-Id": _safe_header(model_id),
    }
    if provider:
        headers["X-ZCA-Provider"] = _safe_header(provider)
    if role:
        headers["X-ZCA-Role"] = _safe_header(role)
    return headers

def should_inject_signature(
    request: Any,
    global_inject_flag: bool = True
) -> bool:
    """
    Determines whether a text attribution badge should be injected into the response body.
    STRICTLY returns False if:
      - global_inject_flag is False
      - response_format is JSON / json_object (would break JSON parsing in client)
      - tools or tool_calls / functions are requested
      - request is inline code completion / FIM / ghost-text
    """
    if not global_inject_flag:
        return False

    # Check request object or dictionary
    if request is None:
        return True

    # 1. Structured JSON output check
    response_format = getattr(request, "response_format", None)
    if response_format:
        if isinstance(response_format, dict):
            fmt_type = response_format.get("type", "")
            if fmt_type in ("json_object", "json_schema"):
                return False
        elif hasattr(response_format, "type"):
            if getattr(response_format, "type") in ("json_object", "json_schema"):
                return False

    # 2. Tool calling check
    tools = getattr(request, "tools", None)
    if tools and len(tools) > 0:
        return False

    functions = getattr(request, "functions", None)
    if functions and len(functions) > 0:
        return False

    tool_choice = getattr(request, "tool_choice", None)
    if tool_choice and tool_choice != "none":
        return False

    # 3. Code completion / FIM check (ghost text)
    # Autocomplete requests often include specific stop sequences or prompt suffixes
    messages = getattr(request, "messages", [])
    if messages and len(messages) > 0:
        last_msg = messages[-1]
        content = getattr(last_msg, "content", "")
        if isinstance(content, str):
            # Typical FIM (Fill-In-the-Middle) tokens used by IDE extensions
            fim_tokens = ["<fim_prefix>", "<fim_suffix>", "<fim_middle>", "<|fim_prefix|>", "<PRE>", "<SUF>", "<MID>"]
            if any(token in content for token in fim_tokens):
                return False

    return True
