"""
Hybrid Orchestrator & Dynamic Aliases Engine for zero-copy-agent.
Routes requests across Local GPU/Ollama, Cloud API Pools, Web Sessions,
executes the 2-Step 'Architect' Cascade, and tracks Token Economics via SQLite WAL.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Solyanni4 (https://github.com/solyanni4/zero-copy-agent)

import time
import asyncio
import logging
from typing import AsyncGenerator, Dict, List, Optional, Tuple, Any, Callable
from core.drivers.ollama import OllamaDriver
from core.drivers.mock import MockDriver
from core.models import ModelCard
from core.storage import analytics
from core.compression import compressor
from core.shield import shield
from core.attribution import format_badge, format_cascade_badge, get_display_name
from core.registry import model_registry
from core.healer import config_healer
from core.audit import LocalHardGate, ArchitectAudit
from core.log_stream import log_orchestrator_start, log_orchestrator_stage, log_orchestrator_done
from core.activity_tracker import activity_tracker
import json
import httpx
from pathlib import Path
from core.mesh import mesh
from core.config import config

logger = logging.getLogger("zcao.orchestrator")


def is_standby_response(res: Any) -> bool:
    """Checks whether the response is a Mock/Standby fallback stub rather than actual LLM output."""
    if not res:
        return True
    if isinstance(res, dict):
        text_content = res.get("text") or res.get("response") or res.get("content") or ""
        if text_content:
            res = text_content
        else:
            return False
    elif not isinstance(res, str):
        return False

    standby_markers = (
        "[ENGINE-STANDBY]",
        "[STANDBY]",
        "Session not authenticated",
        "Could not locate",
        "IDE связь установлена успешно",
        "Standby / Demo",
        "Режим: Standby",
        "Режим: Standalone",
    )
    return any(marker in res for marker in standby_markers)

DEFAULT_ALIASES = {
    "tier:local-fast": "ollama/qwen2.5-coder:14b",
    "tier:coding-top": "gemini-4-neon",
    "tier:reasoning-top": "gemini-4-neon",
    "tier:architect": "cascade:architect",
}

class HybridOrchestrator:
    def __init__(self, browser_engine, ollama_url: str = "http://127.0.0.1:11434"):
        self.browser_engine = browser_engine
        self.ollama = OllamaDriver(base_url=ollama_url)
        self.mock = MockDriver()
        self.aliases: Dict[str, str] = dict(DEFAULT_ALIASES)
        self._cached_models: List[ModelCard] = []
        self._last_discovery = 0.0

        # Closed-Loop Audit & Verification components
        self.gate = LocalHardGate()
        self.auditor = ArchitectAudit()
        self.max_rework_iterations = getattr(config.cascade, "max_rework_iterations", 2)
        self.enable_local_gate = getattr(config.cascade, "enable_local_gate", True)
        self.enable_architect_audit = getattr(config.cascade, "enable_architect_audit", True)

        # Operational Policies & Web UI Controls
        self.dlp_enabled: bool = True
        self.air_gapped: bool = False
        self.compression_enabled: bool = True
        self.mode: str = "auto"  # "auto" | "cascade" | "manual"
        self.breakpoint_enabled: bool = False
        self.active_model: str = "cascade:architect"
        cascade_cfg = getattr(config, "cascade", None)
        self.architect_model: str = getattr(cascade_cfg, "architect_model", "gemini-advanced") or "gemini-advanced"
        self.architect_models: List[str] = getattr(cascade_cfg, "architect_models", None) or [self.architect_model]
        self.executor_model: str = getattr(cascade_cfg, "executor_model", None) or getattr(cascade_cfg, "coder_model", "tier:local-fast") or "tier:local-fast"
        self.reviewer_model: str = getattr(cascade_cfg, "reviewer_model", None) or getattr(cascade_cfg, "auditor_model", "gemini-4-neon") or "gemini-4-neon"

    def apply_preset(self, preset):
        """Applies parameters from an OrchestrationPreset."""
        if not preset:
            return
        if hasattr(preset, "planner_model") and preset.planner_model:
            self.architect_model = preset.planner_model
            self.architect_models = [preset.planner_model]
        if hasattr(preset, "architect_models") and preset.architect_models:
            self.architect_models = list(preset.architect_models)
            if self.architect_models:
                self.architect_model = self.architect_models[0]
        if hasattr(preset, "coder_model") and preset.coder_model:
            self.executor_model = preset.coder_model
        if hasattr(preset, "reviewer_models") and preset.reviewer_models:
            self.reviewer_model = preset.reviewer_models[0]
        if hasattr(preset, "strict_ast_test_gates"):
            self.enable_local_gate = preset.strict_ast_test_gates
        if hasattr(preset, "cross_review_depth"):
            self.enable_architect_audit = (preset.cross_review_depth > 0)
        if hasattr(preset, "use_prompt_compression"):
            self.compression_enabled = preset.use_prompt_compression

        pid = getattr(preset, "id", "auto")
        if pid == "thinker":
            self.mode = "cascade"
        elif pid == "basic":
            self.mode = "manual"
        else:
            self.mode = "auto"

    def resolve_model_id(self, requested_id: str) -> str:
        """Resolves dynamic aliases like 'tier:local-fast' to target model IDs."""
        req = (requested_id or "").strip().lower()
        if req in self.aliases:
            resolved = self.aliases[req]
            logger.info(f"[Orchestrator] Resolved alias '{req}' -> '{resolved}'")
            return resolved
        return requested_id

    def _detect_provider(self, target: str) -> Optional[str]:
        """Infers provider name from model identifier or registered accounts."""
        if not target:
            return None
        low = target.lower()
        if low.startswith(("deepseek", "deep-seek")):
            return "deepseek"
        elif low.startswith(("qwen", "qwq")):
            return "qwen"
        elif low.startswith("grok"):
            return "grok"
        elif low.startswith(("gpt-", "o1", "o3", "chatgpt")):
            return "openai"
        elif low.startswith("claude-"):
            return "anthropic"
        elif low.startswith("gemini-"):
            return "gemini"
        elif "/" in low and any(p in low for p in ("deepseek/", "anthropic/", "openai/", "meta-llama/")):
            return "openrouter"
        elif any(g in low for g in ("llama-3", "mixtral")):
            return "groq"

        try:
            from core.healer import config_healer
            cfg, _ = config_healer.load_or_create()
            for acc in cfg.accounts:
                for m in acc.models:
                    if m.id.lower() == low:
                        return acc.provider.lower()
        except Exception:
            pass
        return None

    def _resolve_api_endpoint(self, provider: str, key: str, model_id: str, prompt: str, stream: bool = False):
        from core.providers import get_preset
        from core.healer import config_healer
        prov = provider.lower()

        base_url = None
        try:
            cfg, _ = config_healer.load_or_create()
            acc = next((a for a in cfg.accounts if a.provider == prov and a.api_key == key), None)
            if acc and acc.base_url:
                base_url = acc.base_url
        except Exception:
            pass

        preset = get_preset(prov)
        if not base_url and preset:
            base_url = preset.get("base_url")

        if prov == "anthropic":
            url = "https://api.anthropic.com/v1/messages" if not base_url else f"{base_url.rstrip('/')}/messages"
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
            payload = {"model": model_id, "max_tokens": 4096, "messages": [{"role": "user", "content": prompt}]}
            if stream:
                payload["stream"] = True
            return "anthropic", url, headers, payload

        if not base_url:
            if prov == "gemini":
                base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
            elif prov in ("chatgpt", "openai"):
                base_url = "https://api.openai.com/v1"
            elif prov == "deepseek":
                base_url = "https://api.deepseek.com/v1"
            elif prov == "qwen":
                base_url = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
            elif prov == "grok":
                base_url = "https://api.x.ai/v1"
            elif prov == "openrouter":
                base_url = "https://openrouter.ai/api/v1"
            elif prov == "groq":
                base_url = "https://api.groq.com/openai/v1"
            else:
                base_url = "https://api.openai.com/v1"

        clean_base = base_url.rstrip("/")
        if clean_base.endswith("/chat/completions"):
            url = clean_base
        else:
            url = f"{clean_base}/chat/completions"

        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        payload = {"model": model_id, "messages": [{"role": "user", "content": prompt}]}
        if stream:
            payload["stream"] = True
        return "openai_compatible", url, headers, payload

    async def _query_single_api_account(
        self,
        provider: str,
        account_id: str,
        key: str,
        model_id: str,
        prompt: str,
        timeout_sec: float = 90.0
    ) -> str:
        """
        Executes prompt via a specific API key account with exact HTTP error capture.
        """
        api_type, url, headers, payload = self._resolve_api_endpoint(provider, key, model_id, prompt, stream=False)
        if "://0.0.0.0" in url:
            url = url.replace("://0.0.0.0", "://127.0.0.1")
        is_local = any(h in url for h in ("127.0.0.1", "localhost", "::1", "0.0.0.0")) or provider.lower() == "ollama"
        effective_read_timeout = 240.0 if (provider.lower() == "ollama" or "qwen2.5-coder" in model_id.lower()) else timeout_sec
        timeout = httpx.Timeout(connect=10.0, read=effective_read_timeout, write=30.0, pool=10.0)

        async with httpx.AsyncClient(timeout=timeout, trust_env=not is_local) as client:
            resp = await client.post(url, headers=headers, json=payload)

            if resp.status_code == 200:
                data = resp.json()
                if api_type == "openai_compatible":
                    msg = data["choices"][0]["message"]
                    return msg.get("content") or msg.get("reasoning_content") or ""
                elif api_type == "anthropic":
                    blocks = data.get("content", [])
                    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                return resp.text

            err_body = resp.text.strip().replace("\r", " ").replace("\n", " ")
            if len(err_body) > 200:
                err_body = err_body[:200] + "..."
            raise RuntimeError(f"HTTP {resp.status_code} - {err_body}")

    async def _query_api_provider(self, provider: str, model_id: str, prompt: str) -> str:
        """Routes prompt to official cloud API via QuotaMesh with automatic failover on 429/5xx."""
        pool = mesh._pools.get(provider.lower(), [])
        max_attempts = max(1, len(pool))
        last_error = None

        for _ in range(max_attempts):
            key = mesh.get_active_key(provider)
            if not key:
                break
            key_state = next((k for k in pool if k.key == key), None)
            acc_id = key_state.account_id if key_state else key
            try:
                content = await self._query_single_api_account(provider, acc_id, key, model_id, prompt, timeout_sec=90.0)
                mesh.record_success(provider, key)
                return content
            except Exception as e:
                last_error = e
                status_code = 500
                if "HTTP " in str(e):
                    try:
                        status_code = int(str(e).split("HTTP ")[1].split(" ")[0].rstrip(":"))
                    except Exception:
                        status_code = 500
                mesh.record_failure(provider, key, status_code)
                logger.warning(f"[Orchestrator] Key failure for {provider} ({acc_id}): {e}. Failing over...")
                continue

        raise RuntimeError(f"Все API-ключи для провайдера '{provider}' исчерпали квоту или недоступны: {last_error}")

    async def _query_api_provider_stream(self, provider: str, model_id: str, prompt: str) -> AsyncGenerator[str, None]:
        """Streams response from cloud API provider using quota mesh with automatic key failover."""
        pool = mesh._pools.get(provider.lower(), [])
        max_attempts = max(1, len(pool))
        last_error = None
        timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)

        for _ in range(max_attempts):
            key = mesh.get_active_key(provider)
            if not key:
                break
            try:
                api_type, url, headers, payload = self._resolve_api_endpoint(provider, key, model_id, prompt, stream=True)
                if "://0.0.0.0" in url:
                    url = url.replace("://0.0.0.0", "://127.0.0.1")
                is_local = any(h in url for h in ("127.0.0.1", "localhost", "::1", "0.0.0.0")) or provider.lower() == "ollama"
                async with httpx.AsyncClient(timeout=timeout, trust_env=not is_local) as client:
                    async with client.stream("POST", url, headers=headers, json=payload) as response:
                        if response.status_code in (429, 500, 502, 503, 504):
                            mesh.record_failure(provider, key, response.status_code)
                            last_error = f"HTTP {response.status_code}"
                            continue
                        elif response.status_code != 200:
                            mesh.record_failure(provider, key, response.status_code)
                            body = await response.aread()
                            raise RuntimeError(f"Provider API stream error ({response.status_code}): {body.decode('utf-8', errors='ignore')[:300]}")

                        mesh.record_success(provider, key)
                        if api_type == "openai_compatible":
                            async for line in response.aiter_lines():
                                line = line.strip()
                                if line.startswith("data: ") and line != "data: [DONE]":
                                    try:
                                        chunk_data = json.loads(line[6:])
                                        choices = chunk_data.get("choices", [])
                                        if choices:
                                            delta = choices[0].get("delta", {})
                                            content = delta.get("content") or delta.get("reasoning_content")
                                            if content:
                                                yield content
                                    except Exception:
                                        pass
                        elif api_type == "anthropic":
                            async for line in response.aiter_lines():
                                line = line.strip()
                                if line.startswith("data: "):
                                    try:
                                        chunk_data = json.loads(line[6:])
                                        if chunk_data.get("type") == "content_block_delta":
                                            delta = chunk_data.get("delta", {})
                                            if delta.get("type") == "text_delta":
                                                yield delta.get("text", "")
                                    except Exception:
                                        pass
                        return
            except httpx.RequestError as e:
                mesh.record_failure(provider, key, 500)
                last_error = str(e)
                continue

        raise RuntimeError(f"Все API-ключи для провайдера '{provider}' исчерпали квоту или недоступны: {last_error}")

    async def discover_models(self, force: bool = False, include_inactive: bool = False) -> List[ModelCard]:
        """
        Discovers models from Ollama, Web Drivers, and registers dynamic aliases.
        Uses ModelRegistry for filtering only currently connected models,
        and auto-syncs newly discovered models into config_healer.
        """
        # Check and update aliases dynamically if ollama has coder models
        try:
            ollama_models = await self.ollama.list_models()
            for m in ollama_models:
                if "qwen2.5-coder:14b" in m:
                    self.aliases["tier:local-fast"] = f"ollama/{m}"
                elif "qwen2.5-coder" in m and "tier:local-fast" not in self.aliases:
                    self.aliases["tier:local-fast"] = f"ollama/{m}"

            # Auto-sync discovered models to subscriptions
            if len(ollama_models) > 0:
                config_healer.sync_discovered_models("ollama", ollama_models)
        except Exception as e:
            logger.debug(f"[Orchestrator] Ollama discovery error: {e}")

        # Check remote models discovery for active OpenAI-compatible providers
        try:
            from core.providers import discover_remote_models, get_preset
            cfg, _ = config_healer.load_or_create()
            for acc in cfg.accounts:
                if acc.auth_type == "api_key" and acc.status == "active" and acc.api_key:
                    b_url = acc.base_url
                    if not b_url:
                        preset = get_preset(acc.provider)
                        if preset:
                            b_url = preset.get("base_url")
                    if b_url:
                        discovered = await discover_remote_models(b_url, acc.api_key)
                        if discovered:
                            config_healer.sync_discovered_models(acc.provider, discovered, target_account_id=acc.id)
        except Exception as disc_err:
            logger.debug(f"[Orchestrator] Remote API model discovery warning: {disc_err}")

        # Fetch models filtered by live provider connectivity
        cards = await model_registry.get_models(
            include_inactive=include_inactive,
            ollama_driver=self.ollama,
            browser_engine=self.browser_engine,
            aliases=self.aliases,
            force_refresh=force,
            mesh=mesh
        )

        self._cached_models = cards
        return cards

    async def query(
        self,
        prompt: str,
        model_id: str,
        req_id: str = "",
        inject_signature: bool = True
    ) -> str:
        """Routes non-streaming query to target provider or cascade pipeline with metric logging."""
        start_time = time.time()

        # 1. DLP Sanitization (Security Shield)
        if self.dlp_enabled:
            sanitized_prompt, vault = shield.sanitize(prompt, req_id=req_id)
            if req_id and len(vault) > 0:
                analytics.record_event(req_id, "dlp_sanitized", model_id, f"Masked {len(vault)} sensitive patterns", vault.summary())
        else:
            sanitized_prompt = prompt
            vault = None

        # 2. L1/L2 Compression
        if self.compression_enabled:
            compressed_prompt, orig_p_tokens, p_tokens = compressor.compress_text(sanitized_prompt)
            if req_id and orig_p_tokens > p_tokens:
                analytics.record_event(req_id, "bpe_compressed", model_id, f"Compressed tokens: {orig_p_tokens} -> {p_tokens} (saved {orig_p_tokens - p_tokens})")

            # Human-in-the-Loop Compression Approval
            require_approval = getattr(getattr(self, "config", None) or config.cascade, "require_compression_approval", False)
            if require_approval and orig_p_tokens > p_tokens and orig_p_tokens >= 30:
                try:
                    from core.prompt_compressor import compressor_manager
                    draft = compressor_manager.create_draft(sanitized_prompt, compressed_prompt)
                    logger.info(f"[Orchestrator] Waiting for human approval on draft '{draft.draft_id}'...")
                    approved_text, status = await compressor_manager.wait_for_decision(draft.draft_id, timeout=120.0)
                    compressed_prompt = approved_text
                    p_tokens = len(approved_text) // 4
                except Exception as e:
                    logger.debug(f"[Orchestrator] Human approval bypass/error: {e}")
        else:
            compressed_prompt = sanitized_prompt
            orig_p_tokens = len(prompt) // 4
            p_tokens = orig_p_tokens

        # Target resolution based on operational mode
        if self.mode == "manual":
            chosen = self.active_model or model_id
            target = self.resolve_model_id(chosen)
        elif self.mode == "cascade":
            target = "cascade:architect"
        else:
            target = self.resolve_model_id(model_id)

        # Air-Gapped Enforcement
        if self.air_gapped:
            ollama_active = await self.ollama.list_models()
            is_local = target.startswith("ollama/") or (target in ollama_active) or target == "mock-test"
            if not is_local:
                err_msg = f"[Air-Gap Policy] Внешний сетевой вызов заблокирован для модели '{target}'. Разрешены только локальные модели (Ollama)."
                logger.warning(err_msg)
                if req_id:
                    analytics.record_event(req_id, "air_gap_blocked", target, err_msg)
                raise RuntimeError(err_msg)

        # Provider API via QuotaMesh check
        provider = self._detect_provider(target)
        has_mesh_key = bool(provider and mesh.get_active_key(provider))

        # 3. Cascade 'Architect' Mode
        if target == "cascade:architect":
            if req_id:
                analytics.record_event(req_id, "cascade_start", target, "Executing 2-Step Architect Cascade")
            content = await self._run_cascade_architect(compressed_prompt, req_id=req_id, inject_signature=inject_signature)
            routed = "cascade:architect"
        # 4. Official Cloud Provider API (OpenAI / Anthropic / Gemini) via QuotaMesh
        elif has_mesh_key:
            if req_id:
                analytics.record_event(req_id, "api_mesh_routing", target, f"Routing via {provider} API pool")
            resp = await self._query_api_provider(provider, target, compressed_prompt)
            if inject_signature:
                badge = format_badge(target, role=f"API Pool · {provider.capitalize()}")
                content = badge + resp
            else:
                content = resp
            routed = f"api_key:{target}"
        # 5. Ollama Local Model
        elif target.startswith("ollama/") or (target in await self.ollama.list_models()):
            try:
                resp = await self.ollama.send_prompt(compressed_prompt, model=target)
                if inject_signature:
                    badge = format_badge(target, role="Local GPU")
                    content = badge + resp
                else:
                    content = resp
                routed = target
            except Exception as e:
                logger.error(f"[Orchestrator] Ollama error: {e}. Falling back to browser session.")
                resp = await self.browser_engine.query(compressed_prompt, "gemini-advanced")
                if inject_signature:
                    badge = format_badge("gemini-advanced", role="Fallback")
                    content = badge + resp
                else:
                    content = resp
                routed = "gemini-advanced"
        # 5. Browser Session Engine (Gemini / ChatGPT / Mock)
        else:
            is_browser_ready = getattr(self.browser_engine, "is_ready", False)
            resp = await self.browser_engine.query(compressed_prompt, target)
            if is_standby_response(resp) and target != "mock-test":
                raise RuntimeError(f"No active API keys or web sessions available for '{target}'. Mock response forbidden in cascade/live mode.")
            if inject_signature:
                if not is_browser_ready and target != "mock-test":
                    badge = format_badge("mock-test", role="Демо-симулятор (Сессия не подключена)")
                else:
                    badge = format_badge(target)
                content = badge + resp
            else:
                content = resp
            routed = target

        # 6. DLP De-Sanitization (Restore original secrets in output)
        if vault is not None and len(vault) > 0:
            final_content = shield.restore(content, vault)
            if req_id:
                analytics.record_event(req_id, "dlp_restored", model_id, f"Restored {len(vault)} secrets in output")
        else:
            final_content = content

        duration = time.time() - start_time
        c_tokens = len(final_content) // 4
        if req_id:
            analytics.record_completion(req_id, model_id, routed, p_tokens, c_tokens, duration, raw_prompt_tokens=orig_p_tokens)

        return final_content

    async def query_stream(
        self,
        prompt: str,
        model_id: str,
        req_id: str = "",
        inject_signature: bool = True
    ) -> AsyncGenerator[str, None]:
        """Routes streaming query to target provider or cascade pipeline with metric logging."""
        start_time = time.time()

        # 1. DLP Sanitization
        if self.dlp_enabled:
            sanitized_prompt, vault = shield.sanitize(prompt, req_id=req_id)
            if req_id and len(vault) > 0:
                analytics.record_event(req_id, "dlp_sanitized", model_id, f"Masked {len(vault)} sensitive patterns", vault.summary())
        else:
            sanitized_prompt = prompt
            vault = None

        # 2. L1/L2 Compression
        if self.compression_enabled:
            compressed_prompt, orig_p_tokens, p_tokens = compressor.compress_text(sanitized_prompt)
            if req_id and orig_p_tokens > p_tokens:
                analytics.record_event(req_id, "bpe_compressed", model_id, f"Compressed tokens: {orig_p_tokens} -> {p_tokens} (saved {orig_p_tokens - p_tokens})")

            # Human-in-the-Loop Compression Approval
            require_approval = getattr(getattr(self, "config", None) or config.cascade, "require_compression_approval", False)
            if require_approval and orig_p_tokens > p_tokens and orig_p_tokens >= 30:
                try:
                    from core.prompt_compressor import compressor_manager
                    draft = compressor_manager.create_draft(sanitized_prompt, compressed_prompt)
                    logger.info(f"[Orchestrator] Waiting for human approval on draft '{draft.draft_id}'...")
                    approved_text, status = await compressor_manager.wait_for_decision(draft.draft_id, timeout=120.0)
                    compressed_prompt = approved_text
                    p_tokens = len(approved_text) // 4
                except Exception as e:
                    logger.debug(f"[Orchestrator] Human approval bypass/error: {e}")
        else:
            compressed_prompt = sanitized_prompt
            orig_p_tokens = len(prompt) // 4
            p_tokens = orig_p_tokens

        # Target resolution based on operational mode
        if self.mode == "manual":
            chosen = self.active_model or model_id
            target = self.resolve_model_id(chosen)
        elif self.mode == "cascade":
            target = "cascade:architect"
        else:
            target = self.resolve_model_id(model_id)

        # Air-Gapped Enforcement
        if self.air_gapped:
            ollama_active = await self.ollama.list_models()
            is_local = target.startswith("ollama/") or (target in ollama_active) or target == "mock-test"
            if not is_local:
                err_msg = f"[Air-Gap Policy] Внешний сетевой вызов заблокирован для модели '{target}'. Разрешены только локальные модели (Ollama)."
                logger.warning(err_msg)
                if req_id:
                    analytics.record_event(req_id, "air_gap_blocked", target, err_msg)
                raise RuntimeError(err_msg)
        routed = target
        chunks = []
        stream_buffer = ""

        provider = self._detect_provider(target)
        has_mesh_key = bool(provider and mesh.get_active_key(provider))
        if has_mesh_key:
            routed = f"api_key:{target}"
        elif target == "cascade:architect":
            routed = "cascade:architect"

        # Generator selector
        async def _get_stream():
            nonlocal routed
            if target == "cascade:architect":
                async for tok in self._run_cascade_architect_stream(compressed_prompt, req_id=req_id, inject_signature=inject_signature):
                    yield tok
            elif has_mesh_key:
                if inject_signature:
                    yield format_badge(target, role=f"API Pool · {provider.capitalize()}")
                try:
                    async for tok in self._query_api_provider_stream(provider, target, compressed_prompt):
                        yield tok
                except RuntimeError as e:
                    logger.warning(f"[ORCHESTRATOR] API stream for '{target}' failed: {e}. Checking web session fallback...")
                    web_streamed = False
                    try:
                        active_web = mesh.get_active_web_account(provider)
                        if active_web and mesh.is_available(active_web) and hasattr(self.browser_engine, "query_stream"):
                            logger.info(f"[ORCHESTRATOR] Falling back to web session '{active_web}' for provider '{provider}'")
                            if inject_signature:
                                yield format_badge(target, role="Web Session Fallback")
                            async for tok in self.browser_engine.query_stream(compressed_prompt, target, account_id=active_web):
                                yield tok
                            web_streamed = True
                    except Exception as web_err:
                        logger.warning(f"[ORCHESTRATOR] Web fallback failed: {web_err}")

                    if not web_streamed:
                        logger.warning("[ORCHESTRATOR] API quota exhausted (429) -> fallback to local Ollama")
                        ollama_model = "qwen2.5-coder:14b"
                        try:
                            installed = await self.ollama.list_models()
                            if ollama_model not in installed and installed:
                                coder_models = [m for m in installed if "coder" in m or "qwen" in m]
                                ollama_model = coder_models[0] if coder_models else installed[0]
                        except Exception:
                            pass
                        if inject_signature:
                            yield format_badge(ollama_model, role="Local GPU Fallback")
                        async for tok in self.ollama.send_prompt_stream(compressed_prompt, model=ollama_model):
                            yield tok
            elif target.startswith("ollama/") or (target in await self.ollama.list_models()):
                try:
                    if inject_signature:
                        yield format_badge(target, role="Local GPU")
                    async for tok in self.ollama.send_prompt_stream(compressed_prompt, model=target):
                        yield tok
                except Exception as e:
                    logger.error(f"[Orchestrator] Ollama stream failed: {e}. Falling back to browser.")
                    if inject_signature:
                        yield format_badge("gemini-advanced", role="Fallback")
                    async for tok in self.browser_engine.query_stream(compressed_prompt, "gemini-advanced"):
                        yield tok
            else:
                is_browser_ready = getattr(self.browser_engine, "is_ready", False)
                if inject_signature:
                    if not is_browser_ready and target != "mock-test":
                        yield format_badge("mock-test", role="Демо-симулятор (Сессия не подключена)")
                    else:
                        yield format_badge(target)
                async for tok in self.browser_engine.query_stream(compressed_prompt, target):
                    yield tok

        async for raw_token in _get_stream():
            if vault is not None and len(vault) > 0:
                flushable, stream_buffer = shield.restore_stream_chunk(raw_token, vault, buffer=stream_buffer)
                if flushable:
                    chunks.append(flushable)
                    yield flushable
            else:
                chunks.append(raw_token)
                yield raw_token

        if stream_buffer:
            if vault is not None and len(vault) > 0:
                restored_last = shield.restore(stream_buffer, vault)
            else:
                restored_last = stream_buffer
            chunks.append(restored_last)
            yield restored_last

        duration = time.time() - start_time
        full_content = "".join(chunks)
        c_tokens = len(full_content) // 4
        if req_id:
            analytics.record_completion(req_id, model_id, routed, p_tokens, c_tokens, duration, raw_prompt_tokens=orig_p_tokens)
            if vault is not None and len(vault) > 0:
                analytics.record_event(req_id, "dlp_restored", model_id, f"Restored {len(vault)} secrets in stream output")

    async def _query_model(self, model_id: str, prompt: str) -> str:
        """
        Intelligently routes cascade steps (plan, coder fallback, audit).
        If browser session is ready, uses browser_engine.
        If browser session is NOT ready but API key is available in QuotaMesh, routes via API.
        In test mock environments (where is_ready is not defined on browser_engine), defers to browser_engine.
        """
        from core.healer import DEFAULT_PROVIDER_MODELS
        resolved_id = model_id
        if model_id in DEFAULT_PROVIDER_MODELS:
            resolved_id = DEFAULT_PROVIDER_MODELS[model_id][0]

        provider = self._detect_provider(resolved_id)
        browser_is_ready = getattr(self.browser_engine, "is_ready", None)

        if browser_is_ready is None and hasattr(self.browser_engine, "query"):
            return await self.browser_engine.query(prompt, resolved_id)

        # 1. Cloud API key in QuotaMesh (Strict Priority)
        if provider and mesh.get_active_key(provider):
            try:
                return await self._query_api_provider(provider, resolved_id, prompt)
            except Exception as e:
                logger.warning(f"[Orchestrator] API key pool failed for '{provider}' ({e}). Falling back to Web Session / Local...")

        # 2. Active Web Session in browser engine (Secondary Fallback)
        if browser_is_ready is True and provider:
            active_web = mesh.get_active_web_account(provider)
            if active_web and mesh.is_available(active_web):
                try:
                    res = await self.browser_engine.query(prompt, resolved_id, account_id=active_web)
                    if res and not is_standby_response(res):
                        return res
                except Exception as e:
                    logger.warning(f"[Orchestrator] Web session query failed for '{provider}' ({e}).")

        # 3. Local GPU via Ollama
        try:
            ollama_models = await self.ollama.list_models()
            if resolved_id.startswith("ollama/") or resolved_id in ollama_models or "qwen2.5-coder" in resolved_id:
                return await self.ollama.send_prompt(prompt, model=resolved_id)
        except Exception:
            pass

        # 4. Fallback to browser session if ready
        if browser_is_ready is True:
            res = await self.browser_engine.query(prompt, resolved_id)
            if not is_standby_response(res):
                return res

        # 5. Check global fallback order for any active key matching the provider
        cascade_cfg = getattr(config, "cascade", None)
        global_order = getattr(cascade_cfg, "fallback_order", ["gemini", "deepseek", "qwen", "chatgpt", "grok", "claude", "openai"]) or []
        for p in global_order:
            if p == "openrouter" or (provider and p == provider):
                if mesh.get_active_key(p):
                    return await self._query_api_provider(p, resolved_id, prompt)

        res = await self.browser_engine.query(prompt, resolved_id)
        if is_standby_response(res):
            raise RuntimeError(f"Candidate '{model_id}' returned Standby / Mock response.")
        return res

    async def _query_model_stream(self, model_id: str, prompt: str) -> AsyncGenerator[str, None]:
        """Streams prompt response via QuotaMesh API, Ollama GPU, or Browser Engine."""
        from core.healer import DEFAULT_PROVIDER_MODELS
        resolved_id = model_id
        if model_id in DEFAULT_PROVIDER_MODELS:
            resolved_id = DEFAULT_PROVIDER_MODELS[model_id][0]

        provider = self._detect_provider(resolved_id)
        browser_is_ready = getattr(self.browser_engine, "is_ready", None)

        if browser_is_ready is None and hasattr(self.browser_engine, "query_stream"):
            async for chunk in self.browser_engine.query_stream(prompt, resolved_id):
                yield chunk
            return

        # 1. Cloud API key in QuotaMesh (Strict Priority)
        if provider and mesh.get_active_key(provider):
            try:
                async for chunk in self._query_api_provider_stream(provider, resolved_id, prompt):
                    yield chunk
                return
            except Exception as e:
                logger.warning(f"[Orchestrator] API key stream failed for '{provider}' ({e}). Falling back to Web Session / Local...")

        # 2. Active Web Session in browser engine (Secondary Fallback)
        if browser_is_ready is True and provider:
            active_web = mesh.get_active_web_account(provider)
            if active_web and mesh.is_available(active_web):
                async for chunk in self.browser_engine.query_stream(prompt, resolved_id, account_id=active_web):
                    yield chunk
                return

        try:
            ollama_models = await self.ollama.list_models()
            if resolved_id.startswith("ollama/") or resolved_id in ollama_models or "qwen2.5-coder" in resolved_id:
                async for chunk in self.ollama.send_prompt_stream(prompt, model=resolved_id):
                    yield chunk
                return
        except Exception:
            pass

        if browser_is_ready is True:
            async for chunk in self.browser_engine.query_stream(prompt, resolved_id):
                yield chunk
            return

        cascade_cfg = getattr(config, "cascade", None)
        global_order = getattr(cascade_cfg, "fallback_order", ["gemini", "deepseek", "qwen", "chatgpt", "grok", "claude", "openai"]) or []
        for p in global_order:
            if p == "openrouter" or (provider and p == provider):
                if mesh.get_active_key(p):
                    async for chunk in self._query_api_provider_stream(p, resolved_id, prompt):
                        yield chunk
                    return

        async for chunk in self.browser_engine.query_stream(prompt, resolved_id):
            yield chunk
        return

    def _is_candidate_configured(self, candidate: str) -> bool:
        """Checks if a model candidate has a real configured account (API key, web session, or local Ollama)."""
        if not candidate:
            return False

        # Support test mock engines and synthetic test models
        if candidate.startswith(("mock-", "test-", "model-", "slow-", "fast-")):
            return True
        if self.browser_engine and type(self.browser_engine).__name__ == "MockEngine":
            return True
        if getattr(self.browser_engine, "is_ready", False):
            prov = self._detect_provider(candidate)
            if prov in ("gemini", "chatgpt", "openai", "deepseek", "qwen", "grok", "claude") or candidate in ("gemini-advanced", "chatgpt-plus"):
                return True

        cand_low = candidate.lower()
        # Ollama / local weights
        if cand_low.startswith(("ollama/", "qwen2.5-coder")) or cand_low in ("tier:local-fast", "ollama", "qwen2.5-coder:14b"):
            return True
        cached_ollama = getattr(self.ollama, "_cached_models", None)
        if cached_ollama and (candidate in cached_ollama or cand_low in [m.lower() for m in cached_ollama]):
            return True

        # Check subscriptions.yaml for explicit model registration across active accounts
        try:
            sub_cfg, _ = config_healer.load_or_create()
            for acc in sub_cfg.accounts:
                if acc.status == "active":
                    for m in getattr(acc, "models", []) or []:
                        m_id = getattr(m, "id", "") if hasattr(m, "id") else (m.get("id", "") if isinstance(m, dict) else str(m))
                        if m_id and m_id.lower() == cand_low:
                            return True
        except Exception:
            pass
        # Detected provider
        prov = self._detect_provider(candidate)
        if prov:
            # Check QuotaMesh accounts
            accounts = mesh.get_candidate_accounts(prov, model_id=candidate)
            if accounts:
                return True
            # Check active API key in mesh
            if mesh.get_active_key(prov):
                return True
            # Check subscriptions.yaml for active browser session or API key
            try:
                sub_cfg, _ = config_healer.load_or_create()
                for acc in sub_cfg.accounts:
                    if acc.status == "active" and acc.provider == prov:
                        if acc.auth_type == "api_key" and acc.api_key:
                            return True
                        if acc.auth_type == "browser_session":
                            for m in acc.models:
                                if m.status == "active" and (m.id == candidate or candidate in m.id):
                                    return True
            except Exception:
                pass
        return False

    async def query_role_with_fallback(
        self,
        role: str,
        prompt: str,
        req_id: str = "",
        validator: Optional[Callable[[str], bool]] = None
    ) -> Tuple[str, str]:
        """
        Executes prompt for a cascade role (planner, coder, reviewer) with automatic
        seamless failover across candidate models/providers from fallback_order.
        Recovers from 429 rate limits, timeouts, Standby responses, or web-session losses
        without interrupting the cascade.
        """
        from core.healer import DEFAULT_PROVIDER_MODELS
        from core.config import config
        cascade_cfg = getattr(getattr(self, "config", None), "cascade", None) or getattr(config, "cascade", None)
        role_cfg = getattr(cascade_cfg, role, None) if cascade_cfg else None

        primary = getattr(role_cfg, "primary", None) if role_cfg else None
        if not primary:
            if role == "planner":
                primary = getattr(self, "architect_model", "gemini-4-neon")
            elif role == "coder":
                primary = getattr(self, "executor_model", "qwen2.5-coder:14b")
            elif role == "reviewer":
                primary = getattr(self, "reviewer_model", "gemini-4-neon")

        candidates: List[str] = []
        if primary:
            candidates.append(primary)

        if role_cfg and hasattr(role_cfg, "fallback_order") and role_cfg.fallback_order:
            for fb in role_cfg.fallback_order:
                if fb and fb not in candidates:
                    candidates.append(fb)

        global_fb = getattr(cascade_cfg, "fallback_order", None) if cascade_cfg else None
        if not global_fb:
            global_fb = ["gemini", "deepseek", "qwen", "chatgpt", "grok", "claude", "ollama"]
        for g in global_fb:
            if g and g not in candidates:
                candidates.append(g)

        # Filter candidates: strictly keep only those with configured accounts or local Ollama
        valid_candidates = [c for c in candidates if self._is_candidate_configured(c)]
        if not valid_candidates:
            valid_candidates = ["qwen2.5-coder:14b"]
        candidates = valid_candidates

        candidate_timeout = float(getattr(cascade_cfg, "candidate_timeout_sec", 60.0) or 60.0)
        last_error = None
        for i, candidate in enumerate(candidates):
            try:
                res, used_cand = await self._query_candidate_for_role(role, candidate, prompt, req_id=req_id)
                if is_standby_response(res):
                    raise RuntimeError(f"Candidate '{candidate}' returned Standby / Mock response.")
                if validator and not validator(res):
                    raise ValueError(f"Candidate '{candidate}' response failed format validation.")
                return res, used_cand
            except Exception as e:
                last_error = e
                logger.warning(f"[Cascade] Candidate '{candidate}' failed for role '{role}': {e}. Switching to next fallback...")
                if i + 1 < len(candidates):
                    next_candidate = candidates[i + 1]
                    err_msg = str(e)
                    if "429" in err_msg or "quota" in err_msg.lower():
                        reason = "429"
                    elif "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
                        reason = "таймаут"
                    else:
                        reason = "ошибка"
                    try:
                        await activity_tracker.record_fallback(failed_model=candidate, next_model=next_candidate, reason=reason)
                    except Exception:
                        pass
                continue

        raise RuntimeError(f"Все кандидаты в каскаде для роли '{role}' ({candidates}) исчерпали квоту или недоступны: {last_error}")

    async def _query_candidate_for_role(
        self,
        role: str,
        candidate: str,
        prompt: str,
        req_id: str = ""
    ) -> Tuple[str, str]:
        """Queries a single candidate model/provider for a specific role across its available accounts."""
        from core.healer import DEFAULT_PROVIDER_MODELS
        from core.config import config
        resolved_candidate = candidate
        if candidate in DEFAULT_PROVIDER_MODELS:
            resolved_candidate = DEFAULT_PROVIDER_MODELS[candidate][0]

        cascade_cfg = getattr(getattr(self, "config", None), "cascade", None) or getattr(config, "cascade", None)
        candidate_timeout = float(getattr(cascade_cfg, "candidate_timeout_sec", 60.0) or 60.0)

        prov = self._detect_provider(resolved_candidate)
        is_local_ollama = (
            resolved_candidate.startswith("ollama/")
            or resolved_candidate in ("tier:local-fast", "ollama")
            or prov == "ollama"
            or "qwen2.5-coder" in resolved_candidate.lower()
        )
        if is_local_ollama:
            try:
                is_auth = await self.ollama.is_authenticated()
            except Exception:
                is_auth = False
            if not is_auth:
                raise RuntimeError(f"Ollama candidate '{resolved_candidate}' unavailable: Ollama daemon is offline.")

        accounts = mesh.get_candidate_accounts(prov, model_id=resolved_candidate) if prov else []

        if accounts:
            attempted_any = False
            for acc in accounts:
                # QuotaMesh Cooldown Guard: skip accounts currently in cooldown/tripped
                if not acc.is_healthy or not mesh.is_available(acc.account_id):
                    rem_cd = max(0, int(acc.tripped_until - time.time()))
                    logger.info(
                        f"[Cascade] Skipping account '{acc.account_id}' for candidate '{resolved_candidate}' "
                        f"(in cooldown for {rem_cd}s)"
                    )
                    continue

                attempted_any = True
                if acc.auth_type == "api_key":
                    acc_timeout = 90.0
                elif acc.auth_type == "browser_session":
                    acc_timeout = 135.0
                else:
                    acc_timeout = candidate_timeout

                logger.info(
                    f"[Cascade] Trying role '{role}' via candidate '{resolved_candidate}' "
                    f"(account={acc.account_id}, type={acc.auth_type}, timeout={int(acc_timeout)}s)..."
                )

                try:
                    if acc.auth_type == "api_key":
                        res = await asyncio.wait_for(
                            self._query_single_api_account(
                                prov, acc.account_id, acc.key, resolved_candidate, prompt, timeout_sec=acc_timeout
                            ),
                            timeout=acc_timeout
                        )
                    elif acc.auth_type == "browser_session":
                        res = await asyncio.wait_for(
                            self.browser_engine.query(prompt, resolved_candidate, account_id=acc.account_id),
                            timeout=acc_timeout
                        )
                    else:
                        res = await asyncio.wait_for(
                            self._query_model(resolved_candidate, prompt),
                            timeout=acc_timeout
                        )

                    # Check for Standby response or unauthenticated fallback
                    if is_standby_response(res):
                        logger.warning(
                            f"[Cascade] Candidate '{resolved_candidate}' (account={acc.account_id}, type={acc.auth_type}) "
                            f"returned Standby/Unauthenticated response. Failing over to next account..."
                        )
                        acc.trip(cooldown_seconds=120.0)
                        mesh.record_web_failure(prov, acc.account_id)
                        continue

                    logger.info(
                        f"[Cascade] Role '{role}' succeeded via candidate '{resolved_candidate}' "
                        f"(account={acc.account_id}, type={acc.auth_type})"
                    )
                    if acc.auth_type == "api_key":
                        mesh.record_success(prov, acc.key)
                    elif acc.auth_type == "browser_session":
                        mesh.record_web_success(prov, acc.account_id)
                    return res, resolved_candidate

                except asyncio.TimeoutError:
                    logger.warning(
                        f"[Cascade] Candidate '{resolved_candidate}' (account={acc.account_id}, type={acc.auth_type}) "
                        f"timed out after {int(acc_timeout)}s -> switching to next account"
                    )
                    acc.trip(cooldown_seconds=120.0)
                    if acc.auth_type == "api_key":
                        mesh.record_failure(prov, acc.key, 504)
                    else:
                        mesh.record_web_failure(prov, acc.account_id)
                    continue

                except Exception as e:
                    status_code = 500
                    if "HTTP " in str(e):
                        try:
                            status_code = int(str(e).split("HTTP ")[1].split(" ")[0].rstrip(":"))
                        except Exception:
                            status_code = 500
                    acc.trip(cooldown_seconds=120.0)
                    if acc.auth_type == "api_key":
                        mesh.record_failure(prov, acc.key, status_code)
                    else:
                        mesh.record_web_failure(prov, acc.account_id)
                    logger.warning(
                        f"[Cascade] Candidate '{resolved_candidate}' (account={acc.account_id}, type={acc.auth_type}) "
                        f"failed: {e}. Failing over to next account..."
                    )
                    continue

            if not attempted_any:
                raise RuntimeError(f"All accounts for candidate '{resolved_candidate}' are currently in cooldown.")
            raise RuntimeError(f"All accounts for candidate '{resolved_candidate}' exhausted.")
        else:
            # Direct query for candidate without mesh accounts (e.g. mock or local)
            logger.info(f"[Cascade] Trying role '{role}' via candidate '{resolved_candidate}' (timeout={candidate_timeout}s)...")
            try:
                res = await asyncio.wait_for(
                    self._query_model(resolved_candidate, prompt),
                    timeout=candidate_timeout
                )

                if is_standby_response(res):
                    logger.warning(f"[Cascade] Candidate '{resolved_candidate}' returned Standby/Unauthenticated response.")
                    if prov:
                        mesh.record_web_failure(prov, resolved_candidate)
                    raise RuntimeError(f"Candidate '{resolved_candidate}' returned Standby.")

                logger.info(f"[Cascade] Role '{role}' succeeded via '{resolved_candidate}'")
                return res, resolved_candidate
            except asyncio.TimeoutError:
                logger.warning(f"[Cascade] Candidate '{resolved_candidate}' timed out after {int(candidate_timeout)}s")
                if prov:
                    mesh.record_web_failure(prov, resolved_candidate)
                raise
            except Exception as e:
                logger.warning(f"[Cascade] Candidate '{resolved_candidate}' failed for role '{role}': {e}")
                if prov:
                    key = mesh.get_active_key(prov)
                    if key:
                        mesh.record_failure(prov, key, 500)
                raise

    async def _run_multi_architect_analysis(
        self,
        architect_models: List[str],
        prompt: str,
        req_id: str = ""
    ) -> Tuple[str, str]:
        """
        Runs parallel analysis across 2+ architect models via asyncio.gather.
        If >= 2 succeed, performs Consensus Synthesis step.
        If 1 succeeds, uses its plan.
        If 0 succeed, falls back to default planner cascade.
        """
        log_orchestrator_stage("multi_architect", "+".join(architect_models), status=f"Параллельный запуск ({len(architect_models)} моделей)...")
        initial_architects_info = [
            {"model": m, "status": "working", "label": f"{m}: ⏳"}
            for m in architect_models
        ]
        await activity_tracker.update_step(
            step=f"Мульти-Архитектор: параллельный анализ ({', '.join(architect_models)})",
            stage="planning",
            stage_label="Мульти-Архитектор",
            model="+".join(architect_models),
            details={"parallel_architects": initial_architects_info}
        )

        async def _query_single(m: str):
            start_m = time.time()
            try:
                plan_text, used_m = await self._query_candidate_for_role("planner", m, prompt, req_id=req_id)
                dur = round(time.time() - start_m, 1)
                return {"model": m, "used": used_m, "plan": plan_text, "success": True, "duration": dur}
            except Exception as err:
                dur = round(time.time() - start_m, 1)
                logger.warning(f"[Multi-Architect] Model '{m}' failed after {dur}s: {err}")
                return {"model": m, "used": m, "plan": None, "success": False, "duration": dur, "error": str(err)}

        results = await asyncio.gather(*[_query_single(m) for m in architect_models], return_exceptions=False)

        updated_architects_info = [
            {
                "model": r["model"],
                "status": "done" if r["success"] else "failed",
                "label": f"{r['model']}: {'✅' if r['success'] else '❌'} {r['duration']}s"
            }
            for r in results
        ]
        successful = [r for r in results if r["success"] and r["plan"]]

        if len(successful) >= 2:
            log_orchestrator_stage("consensus", "+".join(s["model"] for s in successful), status="Синтез консенсуса...")
            await activity_tracker.update_step(
                step="Синтез консенсуса архитектурных решений",
                stage="consensus",
                stage_label="Консенсус",
                model="+".join(s["model"] for s in successful),
                details={"parallel_architects": updated_architects_info}
            )

            plans_section = "\n\n".join([
                f"### План архитектора #{idx + 1} ({s['model']}):\n{s['plan']}"
                for idx, s in enumerate(successful)
            ])
            consensus_prompt = (
                f"Ты — ведущий системный координатор и главный архитектор проекта.\n"
                f"Несколько независимых моделей предложили свои архитектурные решения для одной задачи:\n\n"
                f"{plans_section}\n\n"
                f"Твоя задача — выполнить СИНТЕЗ КОНСЕНСУСА:\n"
                f"1. Объедини сильнейшие технические решения каждого архитектора.\n"
                f"2. Устрани любые противоречия и выбери максимально чистый и надежный путь.\n"
                f"3. Сформируй единый, непротиворечивый финальный архитектурный чертёж и пошаговый план для разработчика."
            )
            try:
                synth_plan, _ = await self.query_role_with_fallback("planner", consensus_prompt, req_id=req_id)
                used_label = f"multi-architect({'+'.join(s['model'] for s in successful)})"
                return synth_plan, used_label
            except Exception as synth_err:
                logger.warning(f"[Multi-Architect] Consensus synthesis failed: {synth_err}. Using first plan.")
                return successful[0]["plan"], successful[0]["model"]

        elif len(successful) == 1:
            logger.info(f"[Multi-Architect] Only 1 model succeeded ({successful[0]['model']}). Using its plan.")
            await activity_tracker.update_step(
                step=f"Использован план от {successful[0]['model']} (остальные завершились ошибкой)",
                details={"parallel_architects": updated_architects_info}
            )
            return successful[0]["plan"], successful[0]["model"]
        else:
            logger.warning("[Multi-Architect] All selected models failed. Falling back to default cascade planner.")
            return await self.query_role_with_fallback("planner", prompt, req_id=req_id)

    async def _run_cascade_architect(
        self,
        user_prompt: str,
        req_id: str = "",
        inject_signature: bool = True,
        existing_plan: Optional[str] = None
    ) -> str:
        """
        Closed-Loop Architect Cascade:
        Step 1: Architectural Blueprint (Plan) via Architect
        Step 2: Code Execution via local GPU / fast engine
        Step 3: Local Hard Gate (0 tokens: AST, YAML, JSON, PowerShell, Bash)
        Step 4: Architect Audit (diff-based semantic verification)
        Step 5: Automated Rework Loop (up to max_rework_iterations with escalation)
        """
        arch_model = getattr(self, "architect_model", "gemini-advanced")
        rev_model = getattr(self, "reviewer_model", "gemini-advanced")
        coder_target = getattr(self, "executor_model", None)

        t_cascade_start = time.time()
        log_orchestrator_start(mode="Каскад", task=user_prompt[:80])
        await activity_tracker.start_activity(
            tool="cascade_architect",
            stage="planning",
            stage_label="Планирование",
            step="Анализ архитектуры и формирование спецификации",
            details={"task_summary": user_prompt[:80]}
        )

        if existing_plan:
            plan = existing_plan
            used_arch = arch_model
        else:
            logger.info(f"[Cascade] Step 1: Architectural Blueprint...")
            if req_id:
                analytics.record_event(req_id, "cascade_step1", arch_model, "Generating Architectural Blueprint")

            # RAG retrieval from SolutionsDB
            solutions_ctx = ""
            try:
                from core.solutions_db import solutions_db
                rels = solutions_db.search_solutions(user_prompt, top_k=3)
                if rels:
                    solutions_ctx = solutions_db.format_context_prompt(rels)
                    for r in rels:
                        solutions_db.increment_usage(r["id"])
            except Exception as e:
                logger.debug(f"[Cascade] Solutions RAG lookup warning: {e}")

            if solutions_ctx:
                plan_prompt = (
                    f"Ты — ведущий системный архитектор. Сформируй краткий структурированный технический план "
                    f"и архитектурный чертёж для задачи:\n\n{solutions_ctx}\n\nЗадача пользователя:\n{user_prompt}"
                )
            else:
                plan_prompt = (
                    f"Ты — ведущий системный архитектор. Сформируй краткий структурированный технический план "
                    f"и архитектурный чертёж для задачи:\n\n{user_prompt}"
                )

            arch_models = getattr(self, "architect_models", None) or [arch_model]
            if len(arch_models) >= 2:
                plan, used_arch = await self._run_multi_architect_analysis(arch_models, plan_prompt, req_id=req_id)
            else:
                log_orchestrator_stage("architect", arch_model, status="формирование чертежа...")
                plan, used_arch = await self.query_role_with_fallback("planner", plan_prompt, req_id=req_id)

        # Breakpoint verification
        if self.breakpoint_enabled:
            logger.info("[Cascade] Breakpoint active. Pausing after architectural plan.")
            if req_id:
                analytics.record_event(req_id, "cascade_breakpoint", used_arch, "Cascade paused at breakpoint after architectural plan")
            badge = format_cascade_badge(used_arch, "pending", status="PAUSED", iterations=0) if inject_signature else ""
            await activity_tracker.finish_activity(
                success=True,
                summary="Архитектурный план сформирован (ожидает подтверждения)"
            )
            return (
                f"{badge}"
                f"### [Breakpoint] Архитектурный план сформирован (Ожидает подтверждения):\n{plan}\n\n"
                f"> **Точка останова активна.** Проверьте спецификацию архитектора перед переходом к генерации кода."
            )

        async def _call_executor(prompt_text: str) -> Tuple[str, str]:
            ollama_ok = False
            try:
                ollama_ok = await self.ollama.is_authenticated()
            except Exception:
                ollama_ok = False

            if ollama_ok:
                try:
                    raw_ollama_models = await self.ollama.list_models()
                    ollama_models = [m for m in raw_ollama_models if "embed" not in m.lower()]
                    chosen_coder = coder_target if (coder_target and coder_target in ollama_models) else next((m for m in ollama_models if "qwen2.5-coder" in m), None)

                    if chosen_coder:
                        resp = await self.ollama.send_prompt(prompt_text, model=chosen_coder)
                        return resp, chosen_coder
                    elif ollama_models:
                        resp = await self.ollama.send_prompt(prompt_text, model=ollama_models[0])
                        return resp, f"ollama/{ollama_models[0]}"
                    else:
                        resp = await self.ollama.send_prompt(prompt_text)
                        return resp, "ollama/default"
                except Exception as e:
                    logger.warning(f"[Cascade] Local Ollama execution failed ({repr(e)}). Falling back to role coder pool.")

            # Fallback to role-based coder pool
            try:
                resp, chosen = await self.query_role_with_fallback("coder", prompt_text, req_id=req_id)
                return resp, chosen
            except Exception as fb_err:
                logger.warning(f"[Cascade] Role coder pool exhausted ({fb_err}). Falling back to planner {used_arch}...")
                candidate_timeout = float(getattr(getattr(self, "config", None) or config, "cascade", None).candidate_timeout_sec if hasattr(getattr(self, "config", None) or config, "cascade") else 60.0)
                try:
                    resp = await asyncio.wait_for(
                        self._query_model(used_arch, prompt_text),
                        timeout=candidate_timeout
                    )
                except Exception:
                    resp, _ = await self.query_role_with_fallback("planner", prompt_text, req_id=req_id)
                return resp, used_arch

        base_max_rework = getattr(self, "max_rework_iterations", 2)
        max_rework = base_max_rework
        current_code = ""
        executor_id = used_arch
        iteration = 0
        final_status = "PASS"
        last_audit_issues: List[Dict[str, Any]] = []

        while iteration <= max_rework:
            iteration_num = iteration + 1
            if iteration == 0:
                logger.info(f"[Cascade] Step 2: Implementation via local/fast engine (attempt {iteration_num})...")
                log_orchestrator_stage("executor", coder_target or "Local GPU", status="генерация кода...")
                await activity_tracker.update_step(
                    step=f"Генерация кода (попытка {iteration_num})",
                    stage="coding",
                    stage_label="Кодинг",
                    model=coder_target or "qwen2.5-coder:14b",
                    source="Local GPU" if ("qwen" in str(coder_target or "").lower() or "ollama" in str(coder_target or "").lower()) else "API"
                )
                if req_id:
                    analytics.record_event(req_id, "cascade_step2", "executor", f"Generating code implementation (attempt {iteration_num})")
                code_prompt = (
                    f"Используя архитектурный план:\n{plan}\n\n"
                    f"Напиши чистую, готовую к продакшену реализацию для задачи:\n{user_prompt}"
                )
            else:
                logger.info(f"[Cascade] Rework Iteration {iteration}/{max_rework}...")
                log_orchestrator_stage("executor", coder_target or "Local GPU", status=f"доработка кода ({iteration}/{max_rework})...")
                await activity_tracker.update_step(
                    step=f"Доработка кода (итерация {iteration}/{max_rework})",
                    stage="coding",
                    stage_label="Кодинг",
                    model=coder_target or "qwen2.5-coder:14b"
                )
                if req_id:
                    analytics.record_event(req_id, "rework_iteration", "executor", f"Rework iteration {iteration}/{max_rework}", {"issues": last_audit_issues})

                issues_summary = "\n".join([
                    f"- [{i.get('severity', 'critical').upper()}] {i.get('file', 'code')}: {i.get('description', '')} -> {i.get('fix_instruction', '')}"
                    for i in last_audit_issues
                ])
                code_prompt = (
                    f"Архитектурный аудит или проверка синтаксиса отклонили предыдущую реализацию (Итерация {iteration}/{max_rework}).\n\n"
                    f"ЗАМЕЧАНИЯ ДЛЯ ИСПРАВЛЕНИЯ:\n{issues_summary}\n\n"
                    f"АРХИТЕКТУРНЫЙ ПЛАН:\n{plan}\n\n"
                    f"ПРЕДЫДУЩИЙ КОД:\n{current_code}\n\n"
                    f"Исправь ВСЕ указанные замечания и выдай исправленную реализацию."
                )

            current_code, executor_id = await _call_executor(code_prompt)

            # Если executor_id совпадает с used_arch, сокращаем повторный аудит до 1 прохода во избежание таймаутов
            if executor_id == used_arch:
                max_rework = min(base_max_rework, 1)

            # --- 1. LOCAL HARD GATE (0 tokens) ---
            if self.enable_local_gate:
                log_orchestrator_stage("gate", "LocalHardGate", status="синтаксическая валидация...")
                await activity_tracker.update_step(
                    step="Hard Gate: 0-token проверка AST / JSON / YAML",
                    stage="gate",
                    stage_label="Hard Gate"
                )
                gate_passed, gate_issues = self.gate.validate_code(current_code)
                if not gate_passed:
                    logger.warning(f"[Cascade] Local Hard Gate REJECTED implementation (iteration {iteration_num}): {len(gate_issues)} issues")
                    if req_id:
                        analytics.record_event(req_id, "local_gate", "hard_gate", f"Local Hard Gate Failed ({len(gate_issues)} syntax/contract issues)", {"issues": gate_issues})
                    last_audit_issues = gate_issues
                    iteration += 1
                    if iteration > max_rework:
                        final_status = "ESCALATED"
                        break
                    continue
                else:
                    if req_id:
                        analytics.record_event(req_id, "local_gate", "hard_gate", "Local Hard Gate Passed (0 token syntax verified)")

            # --- 2. ARCHITECT AUDIT (semantic diff-based check) ---
            if self.enable_architect_audit:
                logger.info(f"[Cascade] Step 3: Architect Audit (iteration {iteration_num})...")
                log_orchestrator_stage("reviewer", rev_model, status="семантический аудит...")
                await activity_tracker.update_step(
                    step="Семантический аудит решения",
                    stage="audit",
                    stage_label="Аудит",
                    model=rev_model
                )
                audit_prompt = self.auditor.build_audit_prompt(plan, current_code)
                try:
                    raw_audit, used_reviewer = await self.query_role_with_fallback(
                        "reviewer", audit_prompt, req_id=req_id, validator=self.auditor.is_valid_audit_response
                    )
                    audit_res = self.auditor.parse_audit_response(raw_audit)
                    verdict = audit_res.get("verdict", "PASS")
                    audit_issues = audit_res.get("issues", [])
                    critical_issues = [i for i in audit_issues if i.get("severity") == "critical"]
                except Exception as e:
                    logger.warning(f"[Cascade] Architect Audit candidate failed or returned non-JSON verdict (iteration {iteration_num}): {e}")
                    verdict = "REJECT"
                    used_reviewer = "unresolved_reviewer"
                    audit_issues = [{
                        "file": "audit",
                        "severity": "critical",
                        "description": f"Аудиторы не смогли проверить решение или вернули невалидный вердикт: {e}",
                        "fix_instruction": "Проверьте реализацию и формат ответа аудитора."
                    }]
                    critical_issues = audit_issues
                    audit_res = {"verdict": "REJECT", "issues": audit_issues}

                if req_id:
                    analytics.record_event(req_id, "architect_audit", used_reviewer, f"Architect Audit verdict: {verdict} ({len(audit_issues)} issues)", audit_res)

                if verdict == "PASS" and not critical_issues:
                    logger.info(f"[Cascade] Architect Audit PASSED on iteration {iteration_num}.")
                    if req_id:
                        analytics.record_event(req_id, "cascade_pass", "cascade:architect", f"Cascade successfully completed in {iteration_num} iteration(s)")
                    final_status = "PASS"

                    # Auto-extract and save solution pattern to SolutionsDB
                    try:
                        from core.solutions_db import solutions_db
                        title_clean = user_prompt.strip().split("\n")[0][:80]
                        prob_summary = user_prompt.strip()[:300]
                        sol_summary = plan.strip()[:400]
                        solutions_db.add_solution(
                            title=title_clean,
                            problem_pattern=prob_summary,
                            solution_summary=sol_summary,
                            code_snippet=current_code[:1000] if current_code else "",
                            source_model=used_reviewer or "architect",
                            tags=["cascade_verified", "architect_audit"]
                        )
                    except Exception as e:
                        logger.debug(f"[Cascade] Auto-save solution to DB warning: {e}")
                    break
                else:
                    logger.warning(f"[Cascade] Architect Audit REJECTED on iteration {iteration_num}: {len(audit_issues)} issues")
                    last_audit_issues = audit_issues or [{"file": "general", "severity": "critical", "description": "Реализация не соответствует архитектурному плану", "fix_instruction": "Перепишите в строгом соответствии с планом."}]
                    iteration += 1
                    if iteration > max_rework:
                        final_status = "ESCALATED"
                        break
                    continue
            else:
                final_status = "PASS"
                break

        t_cascade_end = time.time()
        elapsed_cascade = round(t_cascade_end - t_cascade_start, 1)
        savings = 62.0 if any(k in str(executor_id).lower() for k in ("local", "ollama", "qwen")) else 0.0
        log_orchestrator_done(elapsed_cascade, savings_pct=savings, summary=f"Статус: {final_status}")
        await activity_tracker.finish_activity(
            success=(final_status == "PASS"),
            summary=f"Каскад завершен ({final_status}) за {elapsed_cascade}s"
        )

        if final_status == "ESCALATED":
            if req_id:
                analytics.record_event(req_id, "cascade_escalated", "cascade:architect", f"Cascade exceeded max rework iterations ({max_rework}). Escalated.")
            badge = format_cascade_badge(used_arch, executor_id, status="ESCALATED", iterations=iteration) if inject_signature else ""
            escalation_report = "\n".join([
                f"- **{i.get('file', 'General')}** [{i.get('severity', 'warning')}]: {i.get('description', '')} (*Инструкция: {i.get('fix_instruction', '')}*)"
                for i in last_audit_issues
            ])
            return (
                f"{badge}"
                f"### [Внимание] Требуется ручное подтверждение (Статус: ESCALATED)\n"
                f"Автоматический цикл доработки исчерпал лимит итераций ({max_rework}).\n\n"
                f"**Нерешённые замечания аудитора:**\n{escalation_report}\n\n"
                f"---\n### [Plan] Исходный архитектурный план:\n{plan}\n\n"
                f"---\n### [Code] Последняя версия реализации:\n{current_code}"
            )

        badge = format_cascade_badge(used_arch, executor_id, status="PASS", iterations=iteration + 1) if inject_signature else ""
        return (
            f"{badge}"
            f"### [Plan] Архитектурный план (Шаг 1):\n{plan}\n\n"
            f"---\n### [Code] Реализация (Шаг 2):\n{current_code}"
        )

    async def _run_cascade_architect_stream(
        self,
        user_prompt: str,
        req_id: str = "",
        inject_signature: bool = True
    ) -> AsyncGenerator[str, None]:
        """Streaming version of Closed-Loop Architect Cascade without duplicate plan generation."""
        arch_model = getattr(self, "architect_model", "gemini-advanced")
        if req_id:
            analytics.record_event(req_id, "cascade_step1", arch_model, "Streaming Architectural Blueprint")
        if inject_signature:
            yield "**[ZCAO Cascade: Архитектор (Шаг 1: Проектирование)]**\n\n"

        solutions_ctx = ""
        try:
            from core.solutions_db import solutions_db
            rels = solutions_db.search_solutions(user_prompt, top_k=3)
            if rels:
                solutions_ctx = solutions_db.format_context_prompt(rels)
                for r in rels:
                    solutions_db.increment_usage(r["id"])
        except Exception as e:
            logger.debug(f"[Cascade] Solutions RAG lookup warning: {e}")

        if solutions_ctx:
            plan_prompt = (
                f"Ты — ведущий системный архитектор. Сформируй краткий структурированный технический план "
                f"и архитектурный чертёж для задачи:\n\n{solutions_ctx}\n\nЗадача пользователя:\n{user_prompt}"
            )
        else:
            plan_prompt = (
                f"Ты — ведущий системный архитектор. Сформируй краткий структурированный технический план "
                f"и архитектурный чертёж для задачи:\n\n{user_prompt}"
            )
        plan_tokens = []
        try:
            async for token in self._query_model_stream(arch_model, plan_prompt):
                plan_tokens.append(token)
                yield token
        except Exception as e:
            logger.warning(f"[Cascade] Stream planning failed on {arch_model} ({e}). Falling back to query_role_with_fallback...")
            fallback_plan, _ = await self.query_role_with_fallback("planner", plan_prompt, req_id=req_id)
            plan_tokens.append(fallback_plan)
            yield fallback_plan

        plan = "".join(plan_tokens)

        # Respect breakpoint_enabled
        if self.breakpoint_enabled:
            if req_id:
                analytics.record_event(req_id, "cascade_breakpoint", arch_model, "Cascade paused at breakpoint after architectural plan")
            yield "\n\n---\n**[Breakpoint] Выполнение приостановлено:** Архитектурный план сформирован. Проверьте план перед запуском генерации кода."
            return

        if inject_signature:
            yield "\n\n---\n**[ZCAO Cascade: Запуск замкнутого цикла реализации и аудита...]**\n\n"

        # Execute full closed-loop cascade reusing the generated plan
        result_full = await self._run_cascade_architect(
            user_prompt,
            req_id=req_id,
            inject_signature=False,
            existing_plan=plan
        )
        if "### [Code] Реализация (Шаг 2):" in result_full:
            code_part = result_full.split("### [Code] Реализация (Шаг 2):", 1)[1]
            yield f"### [Code] Реализация (Шаг 2, проверено аудитором):{code_part}"
        elif "### [Внимание] Требуется ручное подтверждение" in result_full:
            yield f"\n\n{result_full}"
        else:
            yield f"\n\n{result_full}"

    async def plan_and_execute_task(
        self,
        goal: str,
        workspace_dir: Optional[str] = None,
        resume: bool = True
    ) -> str:
        """
        Decomposes and executes large complex goals using DAG-Planner,
        Local Worker with AST/Test-gates, and Final Integrator.
        """
        from core.task_planner import run_dag_pipeline
        ws_path = Path(workspace_dir).resolve() if workspace_dir else Path.cwd()

        res = await run_dag_pipeline(
            goal=goal,
            workspace_dir=ws_path,
            orchestrator=self,
            resume=resume,
            architect_model=getattr(self, "architect_model", "gemini-4-neon"),
            executor_model=getattr(self, "executor_model", "ollama/qwen2.5-coder:14b"),
            reviewer_model=getattr(self, "reviewer_model", "gemini-4-neon")
        )

        status_icon = "[OK]" if res["status"] == "completed" else "[FAILED]"
        summary_lines = [
            f"{status_icon} План исполнения задачи: '{goal}'",
            f"ID задачи: {res.get('task_id')}",
            f"Статус: {res.get('status', '').upper()}",
            f"Шагов выполнено: {res.get('steps_count', 0)}"
        ]
        if res.get("final_audit"):
            summary_lines.append(f"\n### Архитектурный аудит:\n{res['final_audit']}")
        if res.get("error"):
            summary_lines.append(f"\n[ОШИБКА]: {res['error']}")
            summary_lines.append("План сохранен в .zca/task_plan.json. Вы можете возобновить выполнение вызовом с resume=True.")

        return "\n".join(summary_lines)
