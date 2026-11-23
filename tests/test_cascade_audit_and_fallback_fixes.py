"""
Unit tests for cascade fallback, standby filtering, auditor JSON validation, and browser warmup fixes.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.audit import ArchitectAudit
from core.orchestrator import HybridOrchestrator, is_standby_response
from core.browser_session import BrowserSessionEngine


def test_is_standby_response():
    assert is_standby_response(None) is True
    assert is_standby_response("") is True
    assert is_standby_response("[ENGINE-STANDBY] Running mock") is True
    assert is_standby_response("[STANDBY] Ready") is True
    assert is_standby_response("Session not authenticated") is True
    assert is_standby_response("**IDE связь установлена успешно!**\n- Режим: Standby / Demo") is True
    assert is_standby_response("Режим: Standalone") is True
    assert is_standby_response("def hello():\n    return 'world'") is False
    assert is_standby_response('{"verdict": "PASS", "issues": []}') is False


def test_audit_parse_valid_json():
    auditor = ArchitectAudit()
    raw = '```json\n{"verdict": "PASS", "issues": []}\n```'
    parsed = auditor.parse_audit_response(raw)
    assert parsed["verdict"] == "PASS"
    assert parsed["issues"] == []
    assert auditor.is_valid_audit_response(raw) is True


def test_audit_parse_reject_json():
    auditor = ArchitectAudit()
    raw = '{"verdict": "REJECT", "issues": [{"file": "main.py", "severity": "critical", "description": "Missing function"}]}'
    parsed = auditor.parse_audit_response(raw)
    assert parsed["verdict"] == "REJECT"
    assert len(parsed["issues"]) == 1
    assert parsed["issues"][0]["severity"] == "critical"
    assert auditor.is_valid_audit_response(raw) is True


def test_audit_parse_invalid_json_raises_value_error():
    auditor = ArchitectAudit()
    raw = "Я думаю, что код написан отлично, всё выглядит хорошо и готово к работе!"
    with pytest.raises(ValueError):
        auditor.parse_audit_response(raw)
    assert auditor.is_valid_audit_response(raw) is False


def test_audit_parse_standby_raises_value_error():
    auditor = ArchitectAudit()
    raw = "**IDE связь установлена успешно!** Запрос получен и обработан через локальный OpenAI-совместимый шлюз."
    with pytest.raises(ValueError):
        auditor.parse_audit_response(raw)
    assert auditor.is_valid_audit_response(raw) is False


@pytest.mark.asyncio
async def test_browser_session_rejects_unconfigured_model():
    engine = BrowserSessionEngine()
    # Query with non-mock unconfigured model should raise RuntimeError
    with pytest.raises(RuntimeError) as exc_info:
        await engine.query("test prompt", "deepseek-reasoner")
    assert "нет настроенной или авторизованной веб-сессии" in str(exc_info.value)


@pytest.mark.asyncio
async def test_browser_session_allows_mock_test():
    engine = BrowserSessionEngine()
    # Explicit mock-test model is allowed to serve mock
    res = await engine.query("test prompt", "mock-test")
    assert "IDE связь установлена успешно" in res


@pytest.mark.asyncio
async def test_candidate_filtering_in_orchestrator():
    mock_browser = MagicMock()
    mock_browser.is_ready = False
    orchestrator = HybridOrchestrator(browser_engine=mock_browser)

    # Test candidate configuration check
    assert orchestrator._is_candidate_configured("qwen2.5-coder:14b") is True
    assert orchestrator._is_candidate_configured("ollama/llama3") is True
    assert orchestrator._is_candidate_configured("tier:local-fast") is True

    # With no credentials, deepseek-reasoner without mesh should be False
    with patch("core.orchestrator.mesh") as mock_mesh:
        mock_mesh.get_candidate_accounts.return_value = []
        mock_mesh.get_active_key.return_value = None
        with patch("core.healer.config_healer.load_or_create") as mock_cfg:
            mock_sub = MagicMock()
            mock_sub.accounts = []
            mock_cfg.return_value = (mock_sub, None)
            assert orchestrator._is_candidate_configured("deepseek-reasoner") is False


@pytest.mark.asyncio
async def test_query_role_with_fallback_validates_auditor():
    mock_browser = MagicMock()
    orchestrator = HybridOrchestrator(browser_engine=mock_browser)

    # Simulate reviewer returning non-JSON output on first candidate and valid JSON on second
    call_count = 0
    async def fake_query_cand(role, candidate, prompt, req_id=""):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "Неструктурированный текст без JSON", candidate
        return '{"verdict": "PASS", "issues": []}', candidate

    orchestrator._query_candidate_for_role = AsyncMock(side_effect=fake_query_cand)
    orchestrator._is_candidate_configured = MagicMock(return_value=True)

    with patch.object(orchestrator, "config", create=True) as mock_cfg:
        mock_cascade = MagicMock()
        mock_role = MagicMock()
        mock_role.primary = "gemini-4-neon"
        mock_role.fallback_order = ["qwen2.5-coder:14b"]
        mock_cascade.reviewer = mock_role
        mock_cascade.fallback_order = ["gemini", "qwen"]
        mock_cascade.candidate_timeout_sec = 10.0
        mock_cfg.cascade = mock_cascade

        res, used = await orchestrator.query_role_with_fallback(
            role="reviewer",
            prompt="audit this",
            validator=orchestrator.auditor.is_valid_audit_response
        )

        assert call_count == 2
        assert '{"verdict": "PASS"' in res
        assert used == "qwen2.5-coder:14b"


@pytest.mark.asyncio
async def test_browser_warmup_triggers_on_active_accounts():
    engine = BrowserSessionEngine()
    engine.ensure_account_ready = AsyncMock(return_value=True)

    mock_account = MagicMock()
    mock_account.id = "gemini-web-1"
    mock_account.provider = "gemini"
    mock_account.auth_type = "browser_session"
    mock_account.status = "active"

    mock_sub = MagicMock()
    mock_sub.accounts = [mock_account]

    with patch("core.healer.config_healer.load_or_create", return_value=(mock_sub, None)):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await engine.warmup_active_accounts()

    engine.ensure_account_ready.assert_awaited_once_with("gemini-web-1")
