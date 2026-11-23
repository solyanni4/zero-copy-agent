"""
Unit tests for zero-copy-agent security, driver unification, and system robustness patches.
Covers:
1. DLPShield password syntax preservation (Python / YAML / JSON).
2. Per-account proxy resolution in BrowserSessionEngine and AccountSubscription.
3. Dev repository protection and backup in clean_uninstall_system.
4. ZCAP Native mandatory token authentication.
5. Web driver unification (ChatGPT, DeepSeek, Grok: reset_to_new_chat, smart wait, failure screenshot).
"""
import os
import sys
import asyncio
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

from core.shield import DLPShield
from core.healer import AccountSubscription, SubscriptionsConfig
from core.uninstaller import clean_uninstall_system
from core.protocol.types import ChannelType, ControlOp, MessageEnvelope, ZCAPFrame
from core.protocol.transport import ZCAPTransportServer, ZCAPTransportClient
from core.protocol.zcap_native import ZCAPNativeEngine
from core.drivers.base import BaseDriver, WebSessionAuthError
from core.drivers.chatgpt import ChatGPTDriver
from core.drivers.deepseek import DeepSeekDriver
from core.drivers.grok import GrokDriver


# 1. DLPShield Syntax Preservation Tests
def test_dlp_shield_password_syntax_preservation():
    shield = DLPShield()

    # Case A: Standard Python variable assignments
    code = 'db_pass = "MySecretPass_456"\nsecret = "super_auth_token_789"'
    sanitized, vault = shield.sanitize(code)

    # Prefix and quotes MUST be preserved in sanitized text
    assert 'db_pass = "' in sanitized
    assert 'secret = "' in sanitized
    assert "MySecretPass_456" not in sanitized
    assert "super_auth_token_789" not in sanitized

    # Restore must recreate exact original code with zero syntax changes
    restored = shield.restore(sanitized, vault)
    assert restored == code

    # Case B: Substring key collision (e.g. key is 'password' and value is 'pass123')
    collision_code = 'password="pass123"\npwd="pwd_val_999"'
    sanitized_col, vault_col = shield.sanitize(collision_code)
    # Ensure key name 'password' was not corrupted to '__SEC_xx_PASSWORD__word'
    assert sanitized_col.startswith('password="')
    assert 'pwd="' in sanitized_col
    restored_col = shield.restore(sanitized_col, vault_col)
    assert restored_col == collision_code

    # Case C: YAML syntax preservation
    yaml_text = 'database:\n  db_pass: "yaml_secret_pass_123"\n  auth_token: "tok_abc_789"'
    sanitized_yaml, vault_yaml = shield.sanitize(yaml_text)
    assert 'db_pass: "' in sanitized_yaml
    restored_yaml = shield.restore(sanitized_yaml, vault_yaml)
    assert restored_yaml == yaml_text


# 2. Per-Account Proxy Resolution Tests
def test_account_subscription_proxy_field():
    # Default is "auto"
    acc_default = AccountSubscription(id="test-1", provider="gemini")
    assert acc_default.proxy == "auto"

    # Explicit direct
    acc_direct = AccountSubscription(id="test-2", provider="chatgpt", proxy="direct")
    assert acc_direct.proxy == "direct"

    # Explicit URL
    custom_url = "http://127.0.0.1:7890"
    acc_custom = AccountSubscription(id="test-3", provider="deepseek", proxy=custom_url)
    assert acc_custom.proxy == custom_url


@pytest.mark.asyncio
async def test_browser_session_per_account_proxy_selection():
    from core.browser_session import BrowserSessionEngine

    engine = BrowserSessionEngine()
    engine.playwright = MagicMock()

    # Mock accounts
    acc_direct = AccountSubscription(id="acc-direct", provider="chatgpt", proxy="direct")
    acc_custom = AccountSubscription(id="acc-custom", provider="deepseek", proxy="http://192.168.1.100:8080")
    acc_auto = AccountSubscription(id="acc-auto", provider="gemini", proxy="auto")

    cfg_mock = SubscriptionsConfig(accounts=[acc_direct, acc_custom, acc_auto])

    with patch("core.healer.config_healer.load_or_create", return_value=(cfg_mock, [])):
        with patch("core.browser_session.detect_proxy", return_value="http://127.0.0.1:7890"):
            with patch("core.auth.kill_zombie_browser_processes"):
                # Test direct proxy
                launch_mock = AsyncMock()
                engine.playwright.chromium.launch_persistent_context = launch_mock

                # Launch direct
                await engine.ensure_account_ready("acc-direct")
                assert "proxy" not in launch_mock.call_args[1] or launch_mock.call_args[1].get("proxy") is None

                # Launch custom
                launch_mock.reset_mock()
                await engine.close_account("acc-custom")
                await engine.ensure_account_ready("acc-custom")
                assert launch_mock.call_args[1].get("proxy") == {"server": "http://192.168.1.100:8080"}

                # Launch auto
                launch_mock.reset_mock()
                await engine.close_account("acc-auto")
                await engine.ensure_account_ready("acc-auto")
                assert launch_mock.call_args[1].get("proxy") == {"server": "http://127.0.0.1:7890"}


# 3. Dev Repository Protection and Backup in Uninstaller
def test_uninstaller_dev_repository_protection_and_backup(tmp_path):
    # Simulate dev workspace
    sub_file = tmp_path / "subscriptions.yaml"
    cfg_file = tmp_path / "config.yaml"
    git_dir = tmp_path / ".git"

    sub_file.write_text("schema_version: 2\naccounts: []\n", encoding="utf-8")
    cfg_file.write_text("version: 1.5.1\n", encoding="utf-8")
    git_dir.mkdir()

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir(parents=True)
    fake_localappdata = tmp_path / "fake_localappdata"
    fake_localappdata.mkdir(parents=True)

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        with patch("pathlib.Path.home", return_value=fake_home):
            with patch.dict(os.environ, {"LOCALAPPDATA": str(fake_localappdata)}):
                res = clean_uninstall_system(keep_backups=False)

                # File in cwd MUST be preserved because .git marker is present
                assert sub_file.exists()
                assert cfg_file.exists()
                assert any("Обнаружен репозиторий исходного кода" in s for s in res.get("steps", []))

                # Backup file MUST be created in temp directory
                backup_path = Path(tempfile.gettempdir()) / "zca_backup_subscriptions.yaml"
                assert backup_path.exists()


# 4. ZCAP Native Mandatory Token Authentication
@pytest.mark.asyncio
async def test_zcap_native_token_authentication(tmp_path):
    test_sock = str(tmp_path / "test_zcap.sock") if sys.platform != "win32" else "127.0.0.1"
    test_port = 18999

    engine = ZCAPNativeEngine(socket_path=test_sock)
    engine.transport.port = test_port
    await engine.start()

    try:
        # Case A: Valid authentication token
        client_valid = ZCAPTransportClient(socket_path=test_sock, port=test_port)
        await client_valid.connect(auth_token="sk-zero-copy")

        # Test Ping/Pong
        ping_frame = ZCAPFrame(channel=ChannelType.CONTROL, payload=ControlOp.PING.encode("utf-8"))
        await client_valid.send(ping_frame)
        pong = await client_valid.receive()
        assert pong.channel == ChannelType.CONTROL
        assert pong.payload.decode("utf-8") == ControlOp.PONG
        await client_valid.close()

        # Case B: Invalid authentication token
        client_invalid = ZCAPTransportClient(socket_path=test_sock, port=test_port)
        with pytest.raises(ConnectionError) as exc_info:
            await client_invalid.connect(auth_token="invalid-secret-token")
        assert "ZCAP authentication failed" in str(exc_info.value)
        await client_invalid.close()

    finally:
        await engine.stop()


# 5. Web Drivers Unified Mechanisms Tests
@pytest.mark.asyncio
async def test_web_drivers_unified_mechanisms():
    mock_page = AsyncMock()
    mock_page.url = "https://chatgpt.com"
    mock_page.is_closed = MagicMock(return_value=False)

    # 1. ChatGPTDriver
    chatgpt = ChatGPTDriver(mock_page)
    assert hasattr(chatgpt, "reset_to_new_chat")
    assert hasattr(chatgpt, "save_failure_screenshot")
    assert callable(chatgpt.reset_to_new_chat)

    # 2. DeepSeekDriver
    mock_page.url = "https://chat.deepseek.com"
    deepseek = DeepSeekDriver(mock_page)
    assert hasattr(deepseek, "reset_to_new_chat")
    assert hasattr(deepseek, "save_failure_screenshot")
    assert callable(deepseek.reset_to_new_chat)

    # 3. GrokDriver
    mock_page.url = "https://grok.com"
    grok = GrokDriver(mock_page)
    assert hasattr(grok, "reset_to_new_chat")
    assert hasattr(grok, "save_failure_screenshot")
    assert callable(grok.reset_to_new_chat)

    # Test reset_to_new_chat invocation doesn't raise
    await chatgpt.reset_to_new_chat()
    await deepseek.reset_to_new_chat()
    await grok.reset_to_new_chat()
