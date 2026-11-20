import os
import yaml
import pytest
from pathlib import Path
from core.healer import ConfigHealer, SubscriptionsConfig, AccountSubscription, SubscriptionModel

def test_healer_initializes_missing_file(tmp_path: Path):
    target_yaml = tmp_path / "subscriptions.yaml"
    healer = ConfigHealer(target_path=target_yaml)

    config, mods = healer.load_or_create()
    assert config.schema_version == 2
    assert len(config.accounts) >= 2
    assert any("Initialized default" in m for m in mods)

def test_healer_migrates_deprecated_model_ids(tmp_path: Path):
    target_yaml = tmp_path / "subscriptions.yaml"
    old_data = {
        "schema_version": 1,
        "accounts": [
            {
                "id": "acc-legacy",
                "provider": "gemini",
                "models": [
                    {"id": "gemini-1.5-pro"},
                    {"id": "gemini-1.5-flash"},
                    {"id": "gpt-4"}
                ]
            }
        ]
    }
    with open(target_yaml, "w", encoding="utf-8") as f:
        yaml.dump(old_data, f)

    healer = ConfigHealer(target_path=target_yaml)
    config, mods = healer.load_or_create()

    assert config.schema_version == 2
    acc = config.accounts[0]
    model_ids = [m.id for m in acc.models]

    assert "gemini-2.5-pro" in model_ids
    assert "gemini-2.5-flash" in model_ids
    assert "gpt-4o" in model_ids
    assert any("Migrated deprecated model ID" in m for m in mods)

def test_healer_deduplicates_accounts(tmp_path: Path):
    target_yaml = tmp_path / "subscriptions.yaml"
    duplicate_data = {
        "schema_version": 2,
        "accounts": [
            {"id": "primary-acc", "provider": "gemini", "models": ["gemini-advanced"]},
            {"id": "primary-acc", "provider": "gemini", "models": ["gemini-4-neon"]}
        ]
    }
    with open(target_yaml, "w", encoding="utf-8") as f:
        yaml.dump(duplicate_data, f)

    healer = ConfigHealer(target_path=target_yaml)
    config, mods = healer.load_or_create()

    assert len(config.accounts) == 1
    assert any("Removed duplicate account ID" in m for m in mods)

def test_healer_atomic_save_and_backup(tmp_path: Path):
    target_yaml = tmp_path / "subscriptions.yaml"
    healer = ConfigHealer(target_path=target_yaml)

    # Initial save
    cfg, _ = healer.load_or_create()
    healer.atomic_save(cfg)
    assert target_yaml.exists()

    # Second save should generate .bak
    cfg.user_aliases["tier:custom-test"] = "gemini-advanced"
    report = healer.atomic_save(cfg)

    assert report.healed is True
    bak_path = Path(report.backup_path)
    assert bak_path.exists()

    # Verify content in target
    with open(target_yaml, "r", encoding="utf-8") as f:
        reloaded = yaml.safe_load(f)
    assert reloaded["user_aliases"]["tier:custom-test"] == "gemini-advanced"

def test_healer_sync_discovered_models(tmp_path: Path):
    target_yaml = tmp_path / "subscriptions.yaml"
    healer = ConfigHealer(target_path=target_yaml)

    mods = healer.sync_discovered_models("ollama", ["qwen2.5-coder:14b", "deepseek-coder:6.7b"])
    assert any("Discovered and registered new model" in m for m in mods)

    cfg, _ = healer.load_or_create()
    ollama_acc = next(a for a in cfg.accounts if a.provider == "ollama")
    m_ids = [m.id for m in ollama_acc.models]
    assert "deepseek-coder:6.7b" in m_ids
