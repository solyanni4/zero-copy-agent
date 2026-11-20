"""
Hybrid Orchestrator & Dynamic Aliases Engine for zero-copy-agent.
Routes requests across Local GPU/Ollama, Cloud API Pools, Web Sessions,
executes the 2-Step 'Architect' Cascade, and tracks Token Economics via SQLite WAL.
"""
import time
import asyncio
import logging
from typing import AsyncGenerator, Dict, List, Optional, Tuple, Any
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
from core.config import config

logger = logging.getLogger("zcao.orchestrator")

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
        self.architect_model: str = "gemini-advanced"
        self.executor_model: str = "tier:local-fast"
        self.reviewer_model: str = "gemini-advanced"

    def resolve_model_id(self, requested_id: str) -> str:
        """Resolves dynamic aliases like 'tier:local-fast' to target model IDs."""
        req = (requested_id or "").strip().lower()
        if req in self.aliases:
            resolved = self.aliases[req]
            logger.info(f"[Orchestrator] Resolved alias '{req}' -> '{resolved}'")
            return resolved
        return requested_id

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

        # Fetch models filtered by live provider connectivity
        cards = await model_registry.get_models(
            include_inactive=include_inactive,
            ollama_driver=self.ollama,
            browser_engine=self.browser_engine,
            aliases=self.aliases,
            force_refresh=force
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
                err_msg = f"🛡️ [Air-Gap Policy] Внешний сетевой вызов заблокирован для модели '{target}'. Разрешены только локальные модели (Ollama)."
                logger.warning(err_msg)
                if req_id:
                    analytics.record_event(req_id, "air_gap_blocked", target, err_msg)
                raise RuntimeError(err_msg)

        # 3. Cascade 'Architect' Mode
        if target == "cascade:architect":
            if req_id:
                analytics.record_event(req_id, "cascade_start", target, "Executing 2-Step Architect Cascade")
            content = await self._run_cascade_architect(compressed_prompt, req_id=req_id, inject_signature=inject_signature)
            routed = "cascade:architect"
        # 4. Ollama Local Model
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
            if inject_signature:
                if not is_browser_ready and target != "mock-test":
                    badge = format_badge("mock-test", role="Демо-симулятор (Сессия не подключена ⚪)")
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
                err_msg = f"🛡️ [Air-Gap Policy] Внешний сетевой вызов заблокирован для модели '{target}'. Разрешены только локальные модели (Ollama)."
                logger.warning(err_msg)
                if req_id:
                    analytics.record_event(req_id, "air_gap_blocked", target, err_msg)
                raise RuntimeError(err_msg)
        routed = target
        chunks = []
        stream_buffer = ""

        # Generator selector
        async def _get_stream():
            if target == "cascade:architect":
                async for tok in self._run_cascade_architect_stream(compressed_prompt, req_id=req_id, inject_signature=inject_signature):
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
                        yield format_badge("mock-test", role="Демо-симулятор (Сессия не подключена ⚪)")
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

        if existing_plan:
            plan = existing_plan
        else:
            logger.info(f"[Cascade] Step 1: Architectural Blueprint via {arch_model}...")
            if req_id:
                analytics.record_event(req_id, "cascade_step1", arch_model, "Generating Architectural Blueprint")
            plan_prompt = (
                f"Ты — ведущий системный архитектор. Сформируй краткий структурированный технический план "
                f"и архитектурный чертёж для задачи:\n\n{user_prompt}"
            )
            plan = await self.browser_engine.query(plan_prompt, arch_model)

        # Breakpoint verification
        if self.breakpoint_enabled:
            logger.info("[Cascade] Breakpoint active. Pausing after architectural plan.")
            if req_id:
                analytics.record_event(req_id, "cascade_breakpoint", arch_model, "Cascade paused at breakpoint after architectural plan")
            badge = format_cascade_badge(arch_model, "pending", status="PAUSED", iterations=0) if inject_signature else ""
            return (
                f"{badge}"
                f"### ⏸️ [Breakpoint] Архитектурный план сформирован (Ожидает подтверждения):\n{plan}\n\n"
                f"> **Точка останова активна.** Проверьте спецификацию архитектора перед переходом к генерации кода."
            )

        async def _call_executor(prompt_text: str) -> Tuple[str, str]:
            ollama_models = await self.ollama.list_models()
            chosen_coder = coder_target if (coder_target and coder_target in ollama_models) else next((m for m in ollama_models if "qwen2.5-coder" in m), None)

            if chosen_coder and await self.ollama.is_authenticated():
                resp = await self.ollama.send_prompt(prompt_text, model=chosen_coder)
                return resp, chosen_coder
            elif await self.ollama.is_authenticated():
                resp = await self.ollama.send_prompt(prompt_text)
                return resp, "ollama/default"
            else:
                resp = await self.browser_engine.query(prompt_text, arch_model)
                return resp, arch_model

        max_rework = getattr(self, "max_rework_iterations", 2)
        current_code = ""
        executor_id = arch_model
        iteration = 0
        final_status = "PASS"
        last_audit_issues: List[Dict[str, Any]] = []

        while iteration <= max_rework:
            iteration_num = iteration + 1
            if iteration == 0:
                logger.info(f"[Cascade] Step 2: Implementation via local/fast engine (attempt {iteration_num})...")
                if req_id:
                    analytics.record_event(req_id, "cascade_step2", "executor", f"Generating code implementation (attempt {iteration_num})")
                code_prompt = (
                    f"Используя архитектурный план:\n{plan}\n\n"
                    f"Напиши чистую, готовую к продакшену реализацию для задачи:\n{user_prompt}"
                )
            else:
                logger.info(f"[Cascade] Rework Iteration {iteration}/{max_rework}...")
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

            # --- 1. LOCAL HARD GATE (0 tokens) ---
            if self.enable_local_gate:
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
                logger.info(f"[Cascade] Step 3: Architect Audit (iteration {iteration_num}) via {rev_model}...")
                audit_prompt = self.auditor.build_audit_prompt(plan, current_code)
                raw_audit = await self.browser_engine.query(audit_prompt, rev_model)
                audit_res = self.auditor.parse_audit_response(raw_audit)
                verdict = audit_res.get("verdict", "PASS")
                audit_issues = audit_res.get("issues", [])
                critical_issues = [i for i in audit_issues if i.get("severity") == "critical"]

                if req_id:
                    analytics.record_event(req_id, "architect_audit", rev_model, f"Architect Audit verdict: {verdict} ({len(audit_issues)} issues)", audit_res)

                if verdict == "PASS" and not critical_issues:
                    logger.info(f"[Cascade] Architect Audit PASSED on iteration {iteration_num}.")
                    if req_id:
                        analytics.record_event(req_id, "cascade_pass", "cascade:architect", f"Cascade successfully completed in {iteration_num} iteration(s)")
                    final_status = "PASS"
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

        if final_status == "ESCALATED":
            if req_id:
                analytics.record_event(req_id, "cascade_escalated", "cascade:architect", f"Cascade exceeded max rework iterations ({max_rework}). Escalated.")
            badge = format_cascade_badge(arch_model, executor_id, status="ESCALATED", iterations=iteration) if inject_signature else ""
            escalation_report = "\n".join([
                f"- **{i.get('file', 'General')}** [{i.get('severity', 'warning')}]: {i.get('description', '')} (*Инструкция: {i.get('fix_instruction', '')}*)"
                for i in last_audit_issues
            ])
            return (
                f"{badge}"
                f"### ⚠️ Внимание: Требуется ручное подтверждение (Статус: ESCALATED)\n"
                f"Автоматический цикл доработки исчерпал лимит итераций ({max_rework}).\n\n"
                f"**Нерешённые замечания аудитора:**\n{escalation_report}\n\n"
                f"---\n### 📐 Исходный архитектурный план:\n{plan}\n\n"
                f"---\n### 💻 Последняя версия реализации:\n{current_code}"
            )

        badge = format_cascade_badge(arch_model, executor_id, status="PASS", iterations=iteration + 1) if inject_signature else ""
        return (
            f"{badge}"
            f"### 📐 Архитектурный план (Шаг 1):\n{plan}\n\n"
            f"---\n### 💻 Реализация (Шаг 2):\n{current_code}"
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
            yield "⚡ **[ZCAO Cascade: Архитектор (Шаг 1: Проектирование)]**\n\n"
        plan_prompt = (
            f"Ты — ведущий системный архитектор. Сформируй краткий структурированный технический план "
            f"и архитектурный чертёж для задачи:\n\n{user_prompt}"
        )
        plan_tokens = []
        async for token in self.browser_engine.query_stream(plan_prompt, arch_model):
            plan_tokens.append(token)
            yield token

        plan = "".join(plan_tokens)

        # Respect breakpoint_enabled
        if self.breakpoint_enabled:
            if req_id:
                analytics.record_event(req_id, "cascade_breakpoint", arch_model, "Cascade paused at breakpoint after architectural plan")
            yield "\n\n---\n⏸️ **[Breakpoint] Выполнение приостановлено:** Архитектурный план сформирован. Проверьте план перед запуском генерации кода."
            return

        if inject_signature:
            yield "\n\n---\n⚡ **[ZCAO Cascade: Запуск замкнутого цикла реализации и аудита...]**\n\n"

        # Execute full closed-loop cascade reusing the generated plan
        result_full = await self._run_cascade_architect(
            user_prompt,
            req_id=req_id,
            inject_signature=False,
            existing_plan=plan
        )
        if "### 💻 Реализация (Шаг 2):" in result_full:
            code_part = result_full.split("### 💻 Реализация (Шаг 2):", 1)[1]
            yield f"### 💻 Реализация (Шаг 2, проверено аудитором):{code_part}"
        elif "### ⚠️ Внимание: Требуется ручное подтверждение" in result_full:
            yield f"\n\n{result_full}"
        else:
            yield f"\n\n{result_full}"
