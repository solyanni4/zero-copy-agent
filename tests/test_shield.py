import pytest
from core.shield import DLPShield, SessionVault

def test_shield_masks_api_keys():
    prompt = "Here is my key: AIzaSyD98f7gH6j5k4L3m2N1b0v9C8x7Z6y5W4v and github ghp_111122223333444455556666777788889999"
    sanitized, vault = DLPShield.sanitize(prompt, req_id="test-1")

    assert "AIzaSy" not in sanitized
    assert "ghp_" not in sanitized
    assert any("GEMINI_KEY" in k for k in vault.mappings)
    assert any("GH_TOKEN" in k for k in vault.mappings)
    assert len(vault) == 2

    # Test restoration
    restored = DLPShield.restore(sanitized, vault)
    assert restored == prompt

def test_shield_masks_private_ips_only():
    prompt = "Connect to gateway at 192.168.1.150 or cluster 10.10.1.5, but keep 127.0.0.1 and DNS 8.8.8.8"
    sanitized, vault = DLPShield.sanitize(prompt, req_id="test-2")

    assert "192.168.1.150" not in sanitized
    assert "10.10.1.5" not in sanitized
    assert "127.0.0.1" in sanitized
    assert "8.8.8.8" in sanitized
    assert "__SEC_01_IP_ADDR__" in sanitized
    assert "__SEC_02_IP_ADDR__" in sanitized

    restored = DLPShield.restore(sanitized, vault)
    assert restored == prompt

def test_shield_masks_passwords():
    prompt = 'DATABASE_URL="postgres://user:super_secret_password_123@db.internal:5432/prod"'
    sanitized, vault = DLPShield.sanitize(prompt, req_id="test-3")

    assert "super_secret_password_123" not in sanitized
    assert "db.internal" not in sanitized
    assert any("URI_CRED" in k for k in vault.mappings)
    assert any("INTERNAL_DOMAIN" in k for k in vault.mappings)

    restored = DLPShield.restore(sanitized, vault)
    assert restored == prompt

def test_shield_streaming_chunk_boundary():
    prompt = "Secret token is ghp_abcdefghijklmnopqrstuvwxyz1234567890"
    sanitized, vault = DLPShield.sanitize(prompt, req_id="test-4")
    placeholder = list(vault.mappings.keys())[0]

    # Simulate streaming where placeholder is split across chunks
    chunk1 = "The token " + placeholder[:6]
    chunk2 = placeholder[6:] + " was used."

    out1, buf1 = DLPShield.restore_stream_chunk(chunk1, vault)
    assert placeholder not in out1
    assert buf1 == placeholder[:6]

    out2, buf2 = DLPShield.restore_stream_chunk(chunk2, vault, buffer=buf1)
    assert "ghp_abcdefghijklmnopqrstuvwxyz1234567890" in out2
    assert buf2 == ""
