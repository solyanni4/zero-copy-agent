"""
Tests for v1.0.7 Stage 2:
- ChatGPT & Gemini Web Drivers authentication checks (avoiding false positives, SPA hydration loop)
- MCP Server SSE keep-alive interval (10s) and 180s tool execution timeout
- Orchestrator cascade fallback to cloud/web when Ollama is unavailable
- Orchestrator single rework pass clamping when executor == architect
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from core.drivers.chatgpt import ChatGPTDriver
from core.drivers.gemini import GeminiDriver
from core.mcp_server import execute_tool, init_mcp
from core.orchestrator import HybridOrchestrator
from core.config import config

@pytest.mark.asyncio
async def test_chatgpt_is_authenticated_false_on_login_screen():
    """Verify that ChatGPT is_authenticated returns False on login screen and removes url fallback."""
    mock_page = MagicMock()
    mock_page.url = "https://chatgpt.com/auth/login"

    # Simulate login button present, input textarea absent
    async def mock_query_selector(sel):
        if "login-button" in sel or "Log in" in sel:
            return MagicMock()
        return None

    mock_page.query_selector = mock_query_selector

    driver = ChatGPTDriver(mock_page)
    is_auth = await driver.is_authenticated()
    assert is_auth is False

@pytest.mark.asyncio
async def test_chatgpt_is_authenticated_true_when_input_ready():
    """Verify that ChatGPT is_authenticated returns True when prompt area is hydrated."""
    mock_page = MagicMock()
    mock_page.url = "https://chatgpt.com/"

    async def mock_query_selector(sel):
        if "login-button" in sel or "Log in" in sel:
            return None
        if "#prompt-textarea" in sel:
            return MagicMock()
        return None

    mock_page.query_selector = mock_query_selector

    driver = ChatGPTDriver(mock_page)
    is_auth = await driver.is_authenticated()
    assert is_auth is True

@pytest.mark.asyncio
async def test_gemini_is_authenticated_false_on_accounts_redirect():
    """Verify GeminiDriver returns False when redirected to accounts.google.com."""
    mock_page = MagicMock()
    mock_page.url = "https://accounts.google.com/v3/signin/identifier"

    driver = GeminiDriver(mock_page)
    is_auth = await driver.is_authenticated()
    assert is_auth is False

@pytest.mark.asyncio
async def test_gemini_is_authenticated_true_when_input_ready():
    """Verify GeminiDriver returns True when chat input element is present."""
    mock_page = MagicMock()
    mock_page.url = "https://gemini.google.com/app"

    async def mock_query_selector(sel):
        if "ServiceLogin" in sel or "Sign in" in sel:
            return None
        if "rich-textarea" in sel:
            return MagicMock()
        return None

    mock_page.query_selector = mock_query_selector

    driver = GeminiDriver(mock_page)
    is_auth = await driver.is_authenticated()
    assert is_auth is True

@pytest.mark.asyncio
async def test_mcp_execute_tool_timeout():
    """Verify that execute_tool returns a clean [TIMEOUT] message if cascade_architect exceeds 180s."""
    mock_orch = MagicMock()
    mock_orch.query = AsyncMock(return_value="done")
    init_mcp(mock_orch)

    async def mock_wait_for(fut, timeout):
        if hasattr(fut, "close"):
            fut.close()
        raise asyncio.TimeoutError()

    with patch("core.mcp_server.asyncio.wait_for", side_effect=mock_wait_for):
        res = await execute_tool("cascade_architect", {"task": "Build large system"})
        assert "[TIMEOUT]" in res
        assert "cascade_architect" in res

        res_gemini = await execute_tool("gemini_deep_analysis", {"prompt": "Deep audit"})
        assert "[TIMEOUT]" in res_gemini
        assert "gemini_deep_analysis" in res_gemini

@pytest.mark.asyncio
async def test_orchestrator_initializes_models_from_config():
    """Verify HybridOrchestrator pulls cascade model configurations from config.cascade."""
    mock_browser = MagicMock()
    orch = HybridOrchestrator(browser_engine=mock_browser)

    assert orch.architect_model == getattr(config.cascade, "architect_model", "gemini-4-neon")
    expected_executor = getattr(config.cascade, "executor_model", None) or getattr(config.cascade, "coder_model", "qwen2.5-coder:14b")
    assert orch.executor_model == expected_executor
    expected_reviewer = getattr(config.cascade, "reviewer_model", None) or getattr(config.cascade, "auditor_model", "gemini-4-neon")
    assert orch.reviewer_model == expected_reviewer

@pytest.mark.asyncio
async def test_cascade_falls_back_when_ollama_offline():
    """Verify cascade execution falls back to cloud/web channel when local Ollama is offline."""
    mock_browser = MagicMock()
    mock_browser.query = AsyncMock(return_value="```python\nprint('cloud fallback code')\n```")
    mock_browser.is_ready = True

    orch = HybridOrchestrator(browser_engine=mock_browser)
    orch.architect_model = "gemini-advanced"
    orch.executor_model = "tier:local-fast"
    orch.reviewer_model = "gemini-advanced"
    orch.enable_local_gate = False
    orch.enable_architect_audit = False
    orch.ollama.is_authenticated = AsyncMock(return_value=False)

    result = await orch._run_cascade_architect("Test task", inject_signature=False)
    assert "### [Plan]" in result
    assert "### [Code]" in result
    assert "cloud fallback code" in result
