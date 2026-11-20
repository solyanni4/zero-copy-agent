"""
Auto-Healing Subscriptions & Configuration Engine for zero-copy-agent.
Validates Pydantic schemas, repairs deprecated model IDs, merges duplicate accounts,
populates newly discovered models, and performs atomic file operations with .bak rotation.
"""

import os
import sys
import yaml
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Set
from pydantic import BaseModel, Field

from core.attribution import get_display_name

logger = logging.getLogger("zcao.healer")

# Known deprecated model mappings to current replacements
MODEL_MIGRATIONS: Dict[str, str] = {
    "gemini-1.5-pro": "gemini-2.5-pro",
    "gemini-1.5-flash": "gemini-2.5-flash",
    "gemini-1.0-pro": "gemini-advanced",
    "gpt-4": "gpt-4o",
    "chatgpt-4": "chatgpt-plus",
    "gpt-3.5-turbo": "gpt-4o-mini",
    "claude-3-sonnet": "claude-3-7-sonnet",
    "claude-2.1": "claude-3-7-sonnet",
}

DEFAULT_PROVIDER_MODELS: Dict[str, List[str]] = {
    "gemini": ["gemini-advanced", "gemini-4-neon", "gemini-2.5-flash"],
    "chatgpt": ["gpt-4o", "gpt-4o-mini", "chatgpt-plus"],
    "claude": ["claude-3-7-sonnet", "claude-pro"],
    "ollama": ["qwen2.5-coder:14b"],
    "mock": ["mock-test"],
}

class SubscriptionModel(BaseModel):
    id: str
    display_name: Optional[str] = None
    status: str = "active"  # "active" | "expired" | "disabled"
    capabilities: List[str] = Field(default_factory=list)

class AccountSubscription(BaseModel):
    id: str
    provider: str  # "gemini" | "chatgpt" | "claude" | "ollama" | "mock"
    auth_type: str = "browser_session"  # "api_key" | "browser_session" | "local"
    status: str = "active"  # "active" | "expired" | "disabled"
    alias: Optional[str] = None
    api_key: Optional[str] = None
    models: List[SubscriptionModel] = Field(default_factory=list)

class SubscriptionsConfig(BaseModel):
    schema_version: int = 2
    updated_at: Optional[str] = None
    accounts: List[AccountSubscription] = Field(default_factory=list)
    user_aliases: Dict[str, str] = Field(default_factory=dict)

class HealingReport(BaseModel):
    healed: bool = False
    modifications: List[str] = Field(default_factory=list)
    backup_path: Optional[str] = None
    config: Optional[SubscriptionsConfig] = None

class ConfigHealer:
    """Manages schema validation, self-healing, and atomic persistence for subscriptions."""

    def __init__(self, target_path: Optional[Path] = None):
        self.target_path = target_path or (Path.cwd() / "subscriptions.yaml")

    def load_or_create(self) -> Tuple[SubscriptionsConfig, List[str]]:
        """Loads subscriptions.yaml or creates default starter schema if missing."""
        modifications: List[str] = []
        if not self.target_path.exists():
            default_config = self._create_default_config()
            modifications.append("Initialized default subscriptions.yaml schema v2")
            return default_config, modifications

        try:
            with open(self.target_path, "r", encoding="utf-8") as f:
                raw_data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"[Healer] Failed to read {self.target_path}: {e}. Creating fallback.")
            raw_data = {}
            modifications.append(f"Repaired corrupted file syntax: {e}")

        config, fix_log = self._heal_dictionary(raw_data)
        modifications.extend(fix_log)
        return config, modifications

    def _create_default_config(self) -> SubscriptionsConfig:
        """Creates initial valid config structure."""
        return SubscriptionsConfig(
            schema_version=2,
            updated_at=datetime.now().isoformat(),
            accounts=[
                AccountSubscription(
                    id="google-web-primary",
                    provider="gemini",
                    auth_type="browser_session",
                    status="active",
                    alias="Gemini Advanced (Личный)",
                    models=[
                        SubscriptionModel(
                            id="gemini-advanced",
                            display_name=get_display_name("gemini-advanced"),
                            status="active"
                        ),
                        SubscriptionModel(
                            id="gemini-4-neon",
                            display_name=get_display_name("gemini-4-neon"),
                            status="active"
                        )
                    ]
                ),
                AccountSubscription(
                    id="local-ollama",
                    provider="ollama",
                    auth_type="local",
                    status="active",
                    alias="Локальный GPU (Ollama)",
                    models=[
                        SubscriptionModel(
                            id="qwen2.5-coder:14b",
                            display_name=get_display_name("qwen2.5-coder:14b"),
                            status="active"
                        )
                    ]
                )
            ],
            user_aliases={
                "tier:local-fast": "qwen2.5-coder:14b",
                "tier:coding-top": "qwen2.5-coder:14b",
                "tier:reasoning-top": "gemini-4-neon"
            }
        )

    def _heal_dictionary(self, raw_data: Dict[str, Any]) -> Tuple[SubscriptionsConfig, List[str]]:
        """Validates and heals dictionary fields before Pydantic parsing."""
        fixes: List[str] = []

        # 1. Check schema_version
        version = raw_data.get("schema_version", 1)
        if version < 2:
            raw_data["schema_version"] = 2
            fixes.append(f"Migrated schema_version from {version} to 2")

        # 2. Check accounts list
        raw_accounts = raw_data.get("accounts")
        if not isinstance(raw_accounts, list):
            raw_accounts = []
            raw_data["accounts"] = raw_accounts
            fixes.append("Initialized missing accounts list")

        # 3. Deduplicate accounts and heal models
        seen_account_ids: Set[str] = set()
        cleaned_accounts: List[Dict[str, Any]] = []

        for acc in raw_accounts:
            if not isinstance(acc, dict):
                continue

            acc_id = str(acc.get("id", "")).strip()
            if not acc_id:
                acc_id = f"acc-{len(cleaned_accounts) + 1}"
                acc["id"] = acc_id
                fixes.append(f"Assigned ID {acc_id} to unlabelled account")

            if acc_id in seen_account_ids:
                fixes.append(f"Removed duplicate account ID: {acc_id}")
                continue
            seen_account_ids.add(acc_id)

            provider = str(acc.get("provider", "mock")).lower()
            acc["provider"] = provider

            # Heal models inside account
            raw_models = acc.get("models")
            if not isinstance(raw_models, list):
                raw_models = []
                acc["models"] = raw_models

            seen_model_ids: Set[str] = set()
            cleaned_models: List[Dict[str, Any]] = []

            for m in raw_models:
                if isinstance(m, str):
                    m = {"id": m}
                if not isinstance(m, dict):
                    continue

                mid = str(m.get("id", "")).strip()
                if not mid:
                    continue

                # Migrate deprecated model IDs
                if mid in MODEL_MIGRATIONS:
                    new_id = MODEL_MIGRATIONS[mid]
                    fixes.append(f"Migrated deprecated model ID '{mid}' -> '{new_id}' in account '{acc_id}'")
                    mid = new_id
                    m["id"] = mid

                if mid in seen_model_ids:
                    continue
                seen_model_ids.add(mid)

                # Ensure display_name
                if not m.get("display_name"):
                    m["display_name"] = get_display_name(mid)

                if "status" not in m:
                    m["status"] = "active"

                cleaned_models.append(m)

            # If models list is empty, populate sensible defaults for provider
            if len(cleaned_models) == 0 and provider in DEFAULT_PROVIDER_MODELS:
                for def_m in DEFAULT_PROVIDER_MODELS[provider]:
                    cleaned_models.append({
                        "id": def_m,
                        "display_name": get_display_name(def_m),
                        "status": "active"
                    })
                fixes.append(f"Populated default models for provider '{provider}' in account '{acc_id}'")

            acc["models"] = cleaned_models
            cleaned_accounts.append(acc)

        raw_data["accounts"] = cleaned_accounts

        # Ensure user_aliases dictionary
        if not isinstance(raw_data.get("user_aliases"), dict):
            raw_data["user_aliases"] = {}
            fixes.append("Initialized missing user_aliases dictionary")

        raw_data["updated_at"] = datetime.now().isoformat()

        # Parse with Pydantic
        try:
            config = SubscriptionsConfig(**raw_data)
        except Exception as e:
            logger.error(f"[Healer] Pydantic parsing failed: {e}. Falling back to default.")
            config = self._create_default_config()
            fixes.append(f"Reset schema to safe defaults due to validation error: {e}")

        return config, fixes

    def sync_discovered_models(
        self,
        provider: str,
        active_model_ids: List[str]
    ) -> List[str]:
        """
        Dynamically synchronizes newly discovered models into the config:
        - Appends new models with status='active'
        - Marks removed models as status='expired' (never silently drops user models)
        """
        config, _ = self.load_or_create()
        modifications: List[str] = []

        # Find matching account
        acc = next((a for a in config.accounts if a.provider == provider), None)
        if not acc:
            # Create new account for discovered provider
            acc = AccountSubscription(
                id=f"{provider}-auto",
                provider=provider,
                auth_type="local" if provider == "ollama" else "api_key",
                status="active",
                alias=f"{provider.capitalize()} (Auto-Discovered)",
                models=[]
            )
            config.accounts.append(acc)
            modifications.append(f"Added new auto-discovered account for provider '{provider}'")

        existing_ids = {m.id: m for m in acc.models}
        current_active_set = set(active_model_ids)

        # 1. Add newly discovered
        for mid in active_model_ids:
            if mid not in existing_ids:
                acc.models.append(
                    SubscriptionModel(
                        id=mid,
                        display_name=get_display_name(mid),
                        status="active"
                    )
                )
                modifications.append(f"Discovered and registered new model '{mid}' in '{acc.id}'")
            elif existing_ids[mid].status != "active":
                existing_ids[mid].status = "active"
                modifications.append(f"Re-activated model '{mid}' in '{acc.id}'")

        # 2. Mark absent as expired (if ollama returned live list)
        if provider == "ollama" and len(active_model_ids) > 0:
            for mid, model_obj in existing_ids.items():
                if mid not in current_active_set and model_obj.status == "active":
                    model_obj.status = "expired"
                    modifications.append(f"Marked unmounted model '{mid}' as expired in '{acc.id}'")

        if len(modifications) > 0:
            self.atomic_save(config)

        return modifications

    def atomic_save(self, config: SubscriptionsConfig) -> HealingReport:
        """
        Saves SubscriptionsConfig atomically:
        1. Writes to temporary file (.tmp)
        2. Validates serialized YAML
        3. Creates timestamped backup (.yaml.bak)
        4. Replaces target file via os.replace
        """
        self.target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.target_path.with_suffix(".yaml.tmp")
        bak_path = self.target_path.with_suffix(".yaml.bak")

        data = config.model_dump() if hasattr(config, "model_dump") else config.dict()
        data["updated_at"] = datetime.now().isoformat()

        # Step 1: Write to .tmp
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        # Step 2: Validate temp file parses cleanly
        try:
            with open(tmp_path, "r", encoding="utf-8") as f:
                reparsed = yaml.safe_load(f)
            SubscriptionsConfig(**reparsed)
        except Exception as e:
            if tmp_path.exists():
                tmp_path.unlink()
            raise RuntimeError(f"Atomic validation failed for temp config: {e}")

        # Step 3: Backup existing target if present
        if self.target_path.exists():
            shutil.copy2(self.target_path, bak_path)

        # Step 4: Atomic replacement
        os.replace(tmp_path, self.target_path)
        logger.info(f"[Healer] Saved config atomically to {self.target_path} (backup: {bak_path.name})")

        return HealingReport(
            healed=True,
            modifications=["Saved atomically with schema validation"],
            backup_path=str(bak_path),
            config=config
        )

    def list_accounts_masked(self) -> List[Dict[str, Any]]:
        """Returns all configured accounts with masked sensitive API keys."""
        config, _ = self.load_or_create()
        results = []
        for acc in config.accounts:
            raw_key = acc.api_key or ""
            if len(raw_key) > 8:
                masked = f"{raw_key[:4]}...{raw_key[-4:]}"
            elif raw_key:
                masked = "***"
            else:
                masked = "— (Веб-сессия / Локально)"
            
            results.append({
                "id": acc.id,
                "provider": acc.provider,
                "auth_type": acc.auth_type,
                "status": acc.status,
                "alias": acc.alias or acc.id,
                "api_key_masked": masked,
                "models_count": len(acc.models),
                "models": [m.id for m in acc.models]
            })
        return results

    def add_account_key(
        self,
        provider: str,
        api_key: str,
        alias: Optional[str] = None
    ) -> Tuple[AccountSubscription, List[str]]:
        """Adds or updates an account with an API key and saves atomically."""
        config, modifications = self.load_or_create()
        prov = provider.strip().lower()
        key_clean = api_key.strip()

        # Check if an account for this key already exists
        existing = next((a for a in config.accounts if a.api_key == key_clean), None)
        if existing:
            existing.status = "active"
            if alias:
                existing.alias = alias
            modifications.append(f"Обновлен существующий аккаунт '{existing.id}'")
            target_acc = existing
        else:
            acc_id = f"{prov}-{len([a for a in config.accounts if a.provider == prov]) + 1}"
            models = []
            default_m_list = DEFAULT_PROVIDER_MODELS.get(prov, [f"{prov}-default"])
            for mid in default_m_list:
                models.append(
                    SubscriptionModel(
                        id=mid,
                        display_name=get_display_name(mid),
                        status="active"
                    )
                )
            target_acc = AccountSubscription(
                id=acc_id,
                provider=prov,
                auth_type="api_key",
                status="active",
                alias=alias or f"{prov.capitalize()} Key ({acc_id})",
                api_key=key_clean,
                models=models
            )
            config.accounts.append(target_acc)
            modifications.append(f"Добавлен новый аккаунт '{acc_id}' для провайдера '{prov}'")

        self.atomic_save(config)
        return target_acc, modifications

    def register_web_session(
        self,
        provider: str,
        alias: Optional[str] = None,
        models: Optional[List[str]] = None
    ) -> Tuple[AccountSubscription, List[str]]:
        """Registers or activates a web browser session account in subscriptions.yaml."""
        config, modifications = self.load_or_create()
        prov = provider.strip().lower()

        existing = next((a for a in config.accounts if a.provider == prov and a.auth_type == "browser_session"), None)
        if existing:
            existing.status = "active"
            if alias:
                existing.alias = alias
            modifications.append(f"Активирована веб-сессия для '{prov}' (аккаунт {existing.id})")
            target_acc = existing
        else:
            acc_id = f"{prov}-web-primary"
            m_list = models or DEFAULT_PROVIDER_MODELS.get(prov, [f"{prov}-default"])
            sub_models = [
                SubscriptionModel(
                    id=mid,
                    display_name=get_display_name(mid),
                    status="active"
                )
                for mid in m_list
            ]
            target_acc = AccountSubscription(
                id=acc_id,
                provider=prov,
                auth_type="browser_session",
                status="active",
                alias=alias or f"{prov.upper()} Web Session (Живая веб-сессия / Free)",
                api_key=None,
                models=sub_models
            )
            config.accounts.append(target_acc)
            modifications.append(f"Добавлен веб-аккаунт '{acc_id}' для '{prov}'")

        self.atomic_save(config)
        return target_acc, modifications

    def remove_account(self, account_id: str) -> bool:
        """Removes or disables an account by ID."""
        config, _ = self.load_or_create()
        initial_len = len(config.accounts)
        config.accounts = [a for a in config.accounts if a.id != account_id]
        if len(config.accounts) < initial_len:
            self.atomic_save(config)
            return True
        return False

# Global singleton
config_healer = ConfigHealer()
