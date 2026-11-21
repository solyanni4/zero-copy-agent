"""
Tests for v1.0.7 Multi-Account Browser Architecture:
- browser_profiles/{account_id} isolation
- LRU context cache and eviction (max_active_contexts: 3)
- Explicit profile_dir in subscriptions.yaml
- Account deletion and profile cleanup
- QuotaMesh load balancing across web-session accounts
"""
import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.browser_session import BrowserSessionEngine
from core.config import get_runtime_cwd
from core.healer import ConfigHealer, SubscriptionsConfig, AccountSubscription
from core.mesh import QuotaMesh
from main import app

@pytest.fixture
def temp_healer():
    """Creates a temporary ConfigHealer instance."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / "subscriptions.yaml"
        healer = ConfigHealer(target_path=tmp_path)
        yield healer

def test_register_web_session_assigns_profile_dir(temp_healer):
    """Test that register_web_session sets explicit profile_dir: browser_profiles/{account_id}."""
    acc1, mods1 = temp_healer.register_web_session(provider="gemini")
    assert acc1.id == "gemini-web-primary"
    assert acc1.profile_dir == "browser_profiles/gemini-web-primary"
    assert acc1.auth_type == "browser_session"

    # Register second account for gemini
    acc2, mods2 = temp_healer.register_web_session(provider="gemini")
    assert acc2.id == "gemini-web-2"
    assert acc2.profile_dir == "browser_profiles/gemini-web-2"

    # Register third account with explicit account_id
    acc3, mods3 = temp_healer.register_web_session(provider="gemini", account_id="gemini-web-custom")
    assert acc3.id == "gemini-web-custom"
    assert acc3.profile_dir == "browser_profiles/gemini-web-custom"

    # Re-registering existing account preserves its profile_dir
    acc1_re, _ = temp_healer.register_web_session(provider="gemini", account_id="gemini-web-primary")
    assert acc1_re.id == "gemini-web-primary"
    assert acc1_re.profile_dir == "browser_profiles/gemini-web-primary"

def test_legacy_profile_migration(temp_healer):
    """Test one-time migration from legacy browser_profile to browser_profiles/{account_id}."""
    with tempfile.TemporaryDirectory() as tmp_runtime:
        runtime_path = Path(tmp_runtime)
        old_profile = runtime_path / "browser_profile"
        old_profile.mkdir()
        (old_profile / "Cookies.sqlite").write_text("mock-cookie-data")

        with patch("core.healer.get_runtime_cwd", return_value=runtime_path):
            acc = AccountSubscription(
                id="gemini-web-primary",
                provider="gemini",
                auth_type="browser_session",
                status="active"
            )
            fixes = temp_healer._migrate_legacy_browser_profile([acc])
            assert len(fixes) > 0

            target_migrated = runtime_path / "browser_profiles" / "gemini-web-primary"
            assert target_migrated.exists()
            assert (target_migrated / "Cookies.sqlite").exists()
            assert (target_migrated / "Cookies.sqlite").read_text() == "mock-cookie-data"

@pytest.mark.asyncio
async def test_lru_context_eviction():
    """Test that BrowserSessionEngine enforces max_active_contexts: 3 and evicts the LRU context."""
    engine = BrowserSessionEngine()
    engine.max_active_contexts = 3
    engine.playwright = MagicMock()

    mock_contexts = {}

    def create_mock_context(acc_id):
        ctx = AsyncMock()
        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://gemini.google.com/app"
        ctx.pages = [page]
        ctx.close = AsyncMock()
        mock_contexts[acc_id] = ctx
        return ctx

    # Mock launch_persistent_context to return unique mock contexts
    async def mock_launch(**kwargs):
        user_data = kwargs.get("user_data_dir", "")
        acc_id = Path(user_data).name
        return create_mock_context(acc_id)

    engine.playwright.chromium.launch_persistent_context = mock_launch

    # Step 1: Open 3 contexts
    ok1 = await engine.ensure_account_ready("acc-1")
    time.sleep(0.01)
    ok2 = await engine.ensure_account_ready("acc-2")
    time.sleep(0.01)
    ok3 = await engine.ensure_account_ready("acc-3")

    assert ok1 and ok2 and ok3
    assert len(engine._contexts) == 3
    assert set(engine._contexts.keys()) == {"acc-1", "acc-2", "acc-3"}

    # Step 2: Touch acc-1 so its last_used timestamp is renewed
    engine._last_used["acc-1"] = time.time() + 10

    # Step 3: Open 4th context - acc-2 should be evicted as LRU
    ok4 = await engine.ensure_account_ready("acc-4")
    assert ok4
    assert len(engine._contexts) == 3
    assert "acc-2" not in engine._contexts
    assert set(engine._contexts.keys()) == {"acc-1", "acc-3", "acc-4"}
    mock_contexts["acc-2"].close.assert_awaited_once()

    # Clean up
    await engine.close()
    assert len(engine._contexts) == 0

@pytest.mark.asyncio
async def test_close_account_isolation():
    """Test that close_account only terminates the targeted account without affecting others."""
    engine = BrowserSessionEngine()
    ctx1 = AsyncMock()
    ctx2 = AsyncMock()
    engine._contexts["gemini-web-1"] = ctx1
    engine._contexts["gemini-web-2"] = ctx2
    engine._last_used["gemini-web-1"] = 1.0
    engine._last_used["gemini-web-2"] = 2.0

    await engine.close_account("gemini-web-1")

    assert "gemini-web-1" not in engine._contexts
    assert "gemini-web-2" in engine._contexts
    ctx1.close.assert_awaited_once()
    ctx2.close.assert_not_awaited()

def test_web_auth_endpoint_account_id_param():
    """Test that POST /v1/providers/web-auth supports custom account_id parameter."""
    client = TestClient(app)
    with patch("subprocess.Popen") as mock_popen, \
         patch("core.browser_session.engine.close_account", new_callable=AsyncMock) as mock_close:
        
        resp = client.post("/v1/providers/web-auth/gemini?account_id=gemini-web-work")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["account_id"] == "gemini-web-work"

        # Verify close_account was called for the targeted profile
        mock_close.assert_awaited_once_with("gemini-web-work")

        # Verify CLI args passed to subprocess
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        assert "--auth" in cmd
        assert "gemini" in cmd
        assert "--account-id" in cmd
        assert "gemini-web-work" in cmd

def test_delete_account_removes_profile_directory():
    """Test that DELETE /v1/providers/accounts/{id} cleans up the on-disk profile folder."""
    client = TestClient(app)
    with tempfile.TemporaryDirectory() as tmp_runtime:
        runtime_path = Path(tmp_runtime)
        prof_dir = runtime_path / "browser_profiles" / "test-delete-acc"
        prof_dir.mkdir(parents=True)
        (prof_dir / "Preferences").write_text("test")

        with patch("main.get_runtime_cwd", return_value=runtime_path), \
             patch("core.healer.get_runtime_cwd", return_value=runtime_path), \
             patch("core.healer.config_healer.load_or_create") as mock_load, \
             patch("core.healer.config_healer.remove_account", return_value=True), \
             patch("core.browser_session.engine.close_account", new_callable=AsyncMock) as mock_close:

            acc = AccountSubscription(
                id="test-delete-acc",
                provider="gemini",
                auth_type="browser_session",
                status="active",
                profile_dir="browser_profiles/test-delete-acc"
            )
            mock_load.return_value = (SubscriptionsConfig(accounts=[acc]), [])

            resp = client.delete("/v1/providers/accounts/test-delete-acc")
            assert resp.status_code == 200
            mock_close.assert_awaited_once_with("test-delete-acc")
            assert not prof_dir.exists()

def test_quota_mesh_multi_web_account_balancing():
    """Test that QuotaMesh balances across multiple web accounts and respects circuit breaking."""
    mesh = QuotaMesh()
    acc1 = AccountSubscription(
        id="chatgpt-web-1",
        provider="chatgpt",
        auth_type="browser_session",
        status="active"
    )
    acc2 = AccountSubscription(
        id="chatgpt-web-2",
        provider="chatgpt",
        auth_type="browser_session",
        status="active"
    )

    mesh.sync_active_accounts([acc1, acc2])

    # Round-robin between two active accounts
    pick1 = mesh.get_active_web_account("chatgpt")
    pick2 = mesh.get_active_web_account("chatgpt")
    assert {pick1, pick2} == {"chatgpt-web-1", "chatgpt-web-2"}

    # Trip chatgpt-web-1
    mesh.record_web_failure("chatgpt", "chatgpt-web-1")
    mesh.record_web_failure("chatgpt", "chatgpt-web-1")
    mesh.record_web_failure("chatgpt", "chatgpt-web-1")

    # Should only return healthy account chatgpt-web-2
    assert mesh.get_active_web_account("chatgpt") == "chatgpt-web-2"
    assert mesh.get_active_web_account("chatgpt") == "chatgpt-web-2"

    # Reset/success on chatgpt-web-1 restores it
    mesh.record_web_success("chatgpt", "chatgpt-web-1")
    assert mesh.get_active_web_account("chatgpt") in {"chatgpt-web-1", "chatgpt-web-2"}
