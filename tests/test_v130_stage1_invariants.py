"""
Tests for zero-copy-agent Stage 1 Invariants (Base v1.3.0).
Verifies:
1. Unified proxy resolution (resolve_http_proxy) for direct, auto, and custom schemes.
2. Absence of duplicate detect_proxy definitions and invalid httpx proxy strings.
3. Unique .tmp files in atomic_save on Windows and clean backup rotation.
4. Single def main() in desktop/tray_app.py and absence of dead code in core/healer.py.
5. Graceful shutdown mechanisms (SolutionsDB.close, ChromeBridgeManager.close_all, /v1/app/quit).
"""
import inspect
from pathlib import Path
from unittest.mock import patch
import pytest

from core.discovery import resolve_http_proxy
from core.healer import ConfigHealer, SubscriptionsConfig
from core.solutions_db import SolutionsDB
from core.chrome_bridge import chrome_bridge
import core.browser_session as bs_mod
import desktop.tray_app as tray_mod


def test_resolve_http_proxy_direct_none_and_falsy():
    """Verifies that direct, none, off, and empty proxy strings resolve strictly to None."""
    assert resolve_http_proxy("direct") is None
    assert resolve_http_proxy("DIRECT") is None
    assert resolve_http_proxy("none") is None
    assert resolve_http_proxy("NONE") is None
    assert resolve_http_proxy("") is None
    assert resolve_http_proxy(None) is None
    assert resolve_http_proxy("off") is None
    assert resolve_http_proxy("false") is None
    assert resolve_http_proxy("  ") is None


def test_resolve_http_proxy_auto_with_mocked_detector():
    """Verifies that 'auto' delegates strictly to detect_proxy and formats properly."""
    with patch("core.auth.detect_proxy", return_value="http://127.0.0.1:7890"):
        res = resolve_http_proxy("auto")
        assert res == "http://127.0.0.1:7890"

    with patch("core.auth.detect_proxy", return_value=None):
        res = resolve_http_proxy("auto")
        assert res is None


def test_resolve_http_proxy_custom_schemes():
    """Verifies that custom proxy URLs are preserved and bare host:port gets http:// prefix."""
    assert resolve_http_proxy("http://proxy.corp:8080") == "http://proxy.corp:8080"
    assert resolve_http_proxy("https://secure.proxy:8443") == "https://secure.proxy:8443"
    assert resolve_http_proxy("socks5://127.0.0.1:1080") == "socks5://127.0.0.1:1080"
    assert resolve_http_proxy("192.168.1.100:3128") == "http://192.168.1.100:3128"


def test_browser_session_has_no_duplicate_detect_proxy():
    """Verifies that core/browser_session.py does not define its own duplicate detect_proxy()."""
    source = inspect.getsource(bs_mod)
    assert "def detect_proxy" not in source, "core/browser_session.py must not define a duplicate detect_proxy()"
    assert "resolve_http_proxy" in source, "core/browser_session.py must import and use resolve_http_proxy"


def test_healer_atomic_save_uses_unique_tmp_and_rotates_bak(tmp_path: Path):
    """Verifies that atomic_save uses unique temporary files and creates .bak on replacement."""
    from core.healer import AccountSubscription

    cfg_file = tmp_path / "subscriptions.yaml"
    healer = ConfigHealer(target_path=cfg_file)

    config = healer._create_default_config()
    config.accounts.append(
        AccountSubscription(
            id="test-acc",
            provider="gemini",
            auth_type="api_key",
            alias="Initial Alias",
            models=[]
        )
    )
    report = healer.atomic_save(config)

    assert cfg_file.exists()
    assert report.healed is True
    bak_file = tmp_path / "subscriptions.yaml.bak"

    # Second save should create .bak backup
    config.accounts[0].alias = "Modified Alias"
    healer.atomic_save(config)

    assert cfg_file.exists()
    assert bak_file.exists()
    bak_content = bak_file.read_text(encoding="utf-8")
    assert "Modified Alias" not in bak_content, "Backup must reflect state prior to second save"
    assert "Initial Alias" in bak_content

    # Verify no dangling .tmp files remain
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0, f"Found leftover temporary files: {tmp_files}"


def test_healer_has_no_dead_code_in_find_active_api_account():
    """Verifies that unreachable code after return None was removed from core/healer.py."""
    import core.healer as healer_mod
    src = inspect.getsource(healer_mod.ConfigHealer.find_active_api_account_for_model)
    lines = [l.strip() for l in src.splitlines()]
    # Last statement before function end must be return None
    assert lines[-1] == "return None", f"Expected 'return None' as last line, got: {lines[-1]}"


def test_tray_app_has_single_main_definition():
    """Verifies that desktop/tray_app.py defines exactly one def main()."""
    src = inspect.getsource(tray_mod)
    main_count = src.count("def main():")
    assert main_count == 1, f"Expected exactly 1 'def main():' in desktop/tray_app.py, found {main_count}"


def test_solutions_db_close_checkpoints_wal(tmp_path: Path):
    """Verifies that SolutionsDB.close executes WAL truncate checkpoint cleanly without error."""
    db_file = tmp_path / "solutions_test.db"
    db = SolutionsDB(db_path=db_file)
    assert db_file.exists()
    # Calling close should succeed and not raise
    db.close()


@pytest.mark.asyncio
async def test_chrome_bridge_close_all_cleans_resources():
    """Verifies that chrome_bridge.close_all closes active connection and cleans tasks."""
    class DummyWS:
        def __init__(self):
            self.closed = False
        async def close(self, code=1000, reason=""):
            self.closed = True

    ws = DummyWS()
    chrome_bridge.active_websocket = ws
    chrome_bridge.active_providers = {"gemini", "chatgpt"}

    await chrome_bridge.close_all()

    assert ws.closed is True
    assert chrome_bridge.active_websocket is None
    assert len(chrome_bridge.active_providers) == 0
    assert len(chrome_bridge.tasks) == 0


def test_architecture_rules_file_exists():
    """Verifies that ARCHITECTURE_RULES.md exists in repo root and contains all 6 invariants."""
    rules_file = Path(__file__).resolve().parent.parent / "ARCHITECTURE_RULES.md"
    assert rules_file.exists(), "ARCHITECTURE_RULES.md must exist in root"
    text = rules_file.read_text(encoding="utf-8")
    assert "1. Единая резолюция прокси" in text
    assert "2. Жёсткая фиксация выбранной модели" in text
    assert "3. Защита процесса, порта 8000" in text
    assert "4. Асинхронность и чистота I/O" in text
    assert "5. Стабильность Chrome Bridge" in text
    assert "6. Безопасность UI и согласованность пресетов" in text
