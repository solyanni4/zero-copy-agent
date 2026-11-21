import pytest
from core.attribution import (
    get_display_name,
    format_badge,
    format_cascade_badge,
    should_inject_signature,
    get_response_headers,
    DISPLAY_NAME_REGISTRY
)

def test_get_display_name():
    # Known registry names
    assert "Gemini Argon" in get_display_name("gemini-advanced")
    assert "GPT Sol" in get_display_name("gpt-4o")
    assert "Claude 3.7 Sonnet" in get_display_name("claude-3-7-sonnet")
    assert "Qwen2.5-Coder" in get_display_name("qwen2.5-coder:14b")

    # Custom override
    override = {"gemini-advanced": "My Custom Gemini"}
    assert get_display_name("gemini-advanced", custom_overrides=override) == "My Custom Gemini"

    # Prefixed / heuristic names
    assert "Local GPU" in get_display_name("ollama/custom-llama")
    assert "Google" in get_display_name("gemini-custom-model")

def test_format_badge():
    badge = format_badge("gemini-advanced")
    assert badge.startswith("**[zero-copy-agent |")
    assert "Gemini Argon" in badge
    assert badge.endswith("]**\n\n")

    role_badge = format_badge("qwen2.5-coder:14b", role="Local GPU")
    assert "Local GPU: Qwen2.5-Coder 14B" in role_badge

def test_format_cascade_badge():
    cascade_badge = format_cascade_badge("gemini-advanced", "qwen2.5-coder:14b")
    assert "ZCAO Cascade: Архитектор" in cascade_badge
    assert "Gemini Argon" in cascade_badge
    assert "Исполнитель" in cascade_badge
    assert "Qwen2.5-Coder 14B" in cascade_badge

def test_should_inject_signature_json_suppression():
    class DummyJsonRequest:
        response_format = {"type": "json_object"}
        tools = None
        messages = []

    assert should_inject_signature(DummyJsonRequest(), global_inject_flag=True) is False

def test_should_inject_signature_tools_suppression():
    class DummyToolsRequest:
        response_format = None
        tools = [{"type": "function", "function": {"name": "test_tool"}}]
        messages = []

    assert should_inject_signature(DummyToolsRequest(), global_inject_flag=True) is False

def test_should_inject_signature_code_completion_suppression():
    class DummyMessage:
        content = "def calculate_sum(a, b):\n<fim_suffix>\n    return result<fim_middle>"

    class DummyCompletionRequest:
        response_format = None
        tools = None
        messages = [DummyMessage()]

    assert should_inject_signature(DummyCompletionRequest(), global_inject_flag=True) is False

def test_should_inject_signature_normal_chat():
    class DummyChatMessage:
        content = "Привет! Как настроить кэширование в Python?"

    class DummyChatRequest:
        response_format = None
        tools = None
        messages = [DummyChatMessage()]

    assert should_inject_signature(DummyChatRequest(), global_inject_flag=True) is True
    # If global flag is false, should be False
    assert should_inject_signature(DummyChatRequest(), global_inject_flag=False) is False

def test_get_response_headers():
    headers = get_response_headers("gemini-advanced", provider="gemini", role="architect")
    expected_safe = DISPLAY_NAME_REGISTRY["gemini-advanced"].encode("ascii", "ignore").decode("ascii").strip()
    assert headers["X-ZCA-Model"] == expected_safe
    assert headers["X-ZCA-Model-Id"] == "gemini-advanced"
    assert headers["X-ZCA-Provider"] == "gemini"
    assert headers["X-ZCA-Role"] == "architect"
    # Ensure all header values can be encoded in Latin-1 (RFC 7230 / Starlette compliance)
    for k, v in headers.items():
        v.encode("latin-1")
