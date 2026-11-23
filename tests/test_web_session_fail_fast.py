import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.drivers.base import BaseDriver, WebSessionAuthError
from core.drivers.gemini import GeminiDriver
from core.drivers.qwen import QwenDriver
from core.mesh import QuotaMesh
from core.healer import ConfigHealer, SubscriptionsConfig, AccountSubscription
from core.browser_session import BrowserSessionEngine, capture_web_failure_screenshot


class DummyDriver(BaseDriver):
    async def is_authenticated(self) -> bool:
        return True

    async def send_prompt(self, prompt: str) -> str:
        return "response"

    async def send_prompt_stream(self, prompt: str):
        yield "response"


@pytest.mark.asyncio
async def test_fail_fast_google_login_redirect():
    mock_page = MagicMock()
    mock_page.url = "https://accounts.google.com/v3/signin/identifier"
    driver = GeminiDriver(mock_page)

    with pytest.raises(WebSessionAuthError) as exc_info:
        await driver.check_fast_auth_or_captcha("Gemini")

    assert "Требуется повторная авторизация" in str(exc_info.value)
    assert "accounts.google.com" in str(exc_info.value)


@pytest.mark.asyncio
async def test_fail_fast_qwen_login_redirect():
    mock_page = MagicMock()
    mock_page.url = "https://chat.qwen.ai/login?redirect=/"
    driver = QwenDriver(mock_page)

    with pytest.raises(WebSessionAuthError) as exc_info:
        await driver.check_fast_auth_or_captcha("Qwen")

    assert "Требуется повторная авторизация" in str(exc_info.value)


@pytest.mark.asyncio
async def test_fail_fast_captcha_detection():
    mock_page = MagicMock()
    mock_page.url = "https://chat.qwen.ai"
    # Return captcha match from evaluate
    mock_page.evaluate = AsyncMock(return_value="iframe https://challenges.cloudflare.com/turnstile")

    driver = DummyDriver(mock_page)
    with pytest.raises(WebSessionAuthError) as exc_info:
        await driver.check_fast_auth_or_captcha("Qwen")

    assert "CAPTCHA" in str(exc_info.value)


@pytest.mark.asyncio
async def test_smart_response_ttft_timeout():
    mock_page = MagicMock()
    driver = DummyDriver(mock_page)

    extract_fn = AsyncMock(return_value="")
    is_generating_fn = AsyncMock(return_value=False)

    with pytest.raises(TimeoutError) as exc_info:
        await driver.wait_for_smart_response(
            extract_fn=extract_fn,
            is_generating_fn=is_generating_fn,
            ttft_timeout=0.6,
            max_generation_time=5.0,
            stability_window=0.5
        )

    assert "TTFT timeout" in str(exc_info.value)


@pytest.mark.asyncio
async def test_smart_response_completes_on_stabilization():
    mock_page = MagicMock()
    driver = DummyDriver(mock_page)

    text_state = ["First token", "First token and more text", "First token and more text"]
    idx = 0

    async def mock_extract():
        nonlocal idx
        val = text_state[min(idx, len(text_state) - 1)]
        idx += 1
        return val

    is_generating_fn = AsyncMock(return_value=False)

    result = await driver.wait_for_smart_response(
        extract_fn=mock_extract,
        is_generating_fn=is_generating_fn,
        ttft_timeout=2.0,
        max_generation_time=5.0,
        stability_window=0.8
    )

    assert result == "First token and more text"


def test_healer_set_account_status(tmp_path):
    sub_file = tmp_path / "subscriptions.yaml"
    healer = ConfigHealer(target_path=sub_file)

    test_acc = AccountSubscription(
        id="gemini-web-test",
        provider="gemini",
        auth_type="browser_session",
        status="active"
    )
    cfg = SubscriptionsConfig(schema_version=2, accounts=[test_acc], user_aliases={})
    healer.atomic_save(cfg)

    # Change status to needs_auth
    updated = healer.set_account_status("gemini-web-test", "needs_auth")
    assert updated is True

    loaded_cfg, _ = healer.load_or_create()
    acc = next(a for a in loaded_cfg.accounts if a.id == "gemini-web-test")
    assert acc.status == "needs_auth"


def test_quota_mesh_trip_account():
    mesh = QuotaMesh()
    mesh.register_keys("gemini", ["key-1"])
    # Register web account
    mesh.sync_active_accounts([
        AccountSubscription(
            id="qwen-web-primary",
            provider="qwen",
            auth_type="browser_session",
            status="active"
        )
    ])

    assert mesh.is_available("qwen-web-primary") is True
    # Trip for 60 seconds
    mesh.trip_account("qwen-web-primary", cooldown_seconds=60.0)
    assert mesh.is_available("qwen-web-primary") is False


@pytest.mark.asyncio
async def test_browser_session_engine_handles_auth_error_and_trips(tmp_path):
    engine = BrowserSessionEngine()
    engine.is_ready = True

    mock_driver = MagicMock()
    mock_driver.is_authenticated = AsyncMock(side_effect=WebSessionAuthError("Session expired"))
    engine.get_driver = MagicMock(return_value=mock_driver)
    engine.resolve_target_account = MagicMock(return_value="gemini-web-test")
    engine.ensure_account_ready = AsyncMock(return_value=True)

    with patch("core.mesh.mesh.trip_account") as mock_trip:
        with patch("core.healer.config_healer.set_account_status") as mock_set_status:
            with pytest.raises(RuntimeError) as exc_info:
                await engine.query("Hello", "gemini-4-neon", "gemini-web-test")

            assert "требует повторной авторизации" in str(exc_info.value)
            mock_trip.assert_called_with("gemini-web-test", cooldown_seconds=300.0)
            mock_set_status.assert_called_with("gemini-web-test", "needs_auth")
