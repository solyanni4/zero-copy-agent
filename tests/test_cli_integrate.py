import pytest
from core.ide_setup import scan_all, print_config_snippet, test_connection as verify_connection

def test_scan_all_returns_all_targets():
    results = scan_all()
    expected_targets = {"antigravity", "vscode", "cursor", "windsurf", "claude", "aider"}
    assert expected_targets.issubset(set(results.keys()))

    for target, info in results.items():
        assert "name" in info
        assert "installed" in info
        assert "configured" in info
        assert "details" in info

def test_print_config_snippet():
    vscode_snippet = print_config_snippet("vscode", port=8000)
    assert "tier:local-fast" in vscode_snippet
    assert "http://127.0.0.1:8000/v1" in vscode_snippet

    antigravity_snippet = print_config_snippet("antigravity", port=8000)
    assert "http://127.0.0.1:8000/mcp" in antigravity_snippet

    aider_snippet = print_config_snippet("aider", port=8000)
    assert "OPENAI_API_BASE=http://127.0.0.1:8000/v1" in aider_snippet

def test_connection_tester():
    # Testing non-existent port should return clean False without throwing unhandled exceptions
    ok, msg = verify_connection("gateway", port=59999)
    assert ok is False
    assert "59999" in msg or "недоступен" in msg.lower()
