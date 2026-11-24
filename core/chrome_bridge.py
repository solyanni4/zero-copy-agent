"""
Chrome Bridge WebSocket Manager for zero-copy-agent.
Provides a low-latency live communication bridge between zero-copy-agent and Google Chrome.
Executes web-based LLM prompts directly inside existing Chrome user sessions
without headless browser overhead or cookie duplication.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Solyanni4 (https://github.com/solyanni4/zero-copy-agent)

import asyncio
import json
import logging
import secrets
import time
from typing import AsyncIterator, Dict, List, Optional, Set, Any
from starlette.websockets import WebSocket, WebSocketDisconnect

from core.config import config
from core.healer import config_healer
from core.mesh import mesh

logger = logging.getLogger("zca.chrome_bridge")

PROVIDER_NAMES = {
    "gemini": "Google Gemini (Chrome Bridge)",
    "chatgpt": "OpenAI ChatGPT (Chrome Bridge)",
    "claude": "Anthropic Claude (Chrome Bridge)",
    "deepseek": "DeepSeek (Chrome Bridge)",
    "qwen": "Alibaba Qwen (Chrome Bridge)",
    "grok": "xAI Grok 3 (Chrome Bridge)"
}


class BridgeCancelledError(RuntimeError):
    """Raised when in-flight prompt task is cancelled by user request."""
    pass


class ChromeBridgeManager:
    """
    Singleton manager maintaining WebSocket connection with zero-copy-agent Chrome extension.
    """

    def __init__(self):
        self.active_websocket: Optional[WebSocket] = None
        self.active_providers: Set[str] = set()
        self.user_agent: str = ""
        self.extension_version: str = ""
        self.last_seen: float = 0.0
        self.tasks: Dict[str, asyncio.Queue] = {}
        self._lock = asyncio.Lock()
        self._ping_task: Optional[asyncio.Task] = None

    def is_connected(self) -> bool:
        """Checks if a valid WebSocket connection to Chrome extension is alive."""
        return self.active_websocket is not None

    def has_provider(self, provider: str) -> bool:
        """Checks whether the specified provider is active and supported via the bridge."""
        if not self.is_connected():
            return False
        clean_prov = (provider or "").strip().lower()
        if not clean_prov:
            return False
        if self.active_providers:
            return clean_prov in self.active_providers
        return clean_prov in PROVIDER_NAMES

    def get_status(self) -> Dict[str, Any]:
        """Returns diagnostic status of the bridge."""
        return {
            "connected": self.is_connected(),
            "providers": sorted(list(self.active_providers)),
            "active_tasks": len(self.tasks),
            "last_seen": self.last_seen,
            "user_agent": self.user_agent,
            "version": self.extension_version
        }

    async def handle_websocket(self, websocket: WebSocket):
        """
        Handles incoming WebSocket lifecycle from zero-copy-agent Chrome extension.
        Enforces 127.0.0.1 origin and token authentication.
        """
        # Validate client IP
        client_host = websocket.client.host if websocket.client else ""
        if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            logger.warning(f"[ChromeBridge] Rejected non-localhost connection from {client_host}")
            await websocket.close(code=4403, reason="Localhost only")
            return

        # Validate token
        query_token = websocket.query_params.get("token") or ""
        hdr_token = websocket.headers.get("x-zca-sync-token") or ""
        expected_token = getattr(config, "cookie_sync_token", "") or ""

        token_to_verify = query_token or hdr_token
        if not expected_token or token_to_verify != expected_token:
            logger.warning("[ChromeBridge] Authentication failed: invalid or missing sync token")
            await websocket.close(code=4401, reason="Unauthorized: invalid sync token")
            return

        await websocket.accept()
        ws_id = id(websocket)
        logger.info(f"[ChromeBridge] WS connected (ws_id={ws_id})")

        async with self._lock:
            if self.active_websocket:
                try:
                    await self.active_websocket.close(code=1000, reason="Superseded by new connection")
                except Exception:
                    pass
            self.active_websocket = websocket
            self.last_seen = time.time()
            if self._ping_task and not self._ping_task.done():
                self._ping_task.cancel()
            self._ping_task = asyncio.create_task(self._pinger_loop(websocket))

        try:
            while True:
                msg_text = await websocket.receive_text()
                self.last_seen = time.time()
                try:
                    data = json.loads(msg_text)
                except Exception as parse_err:
                    logger.warning(f"[ChromeBridge] Received invalid JSON on WS (ws_id={ws_id}): {parse_err}")
                    continue

                msg_type = data.get("type", "unknown")
                task_id = data.get("task_id", "-")
                logger.info(f"[ChromeBridge][{task_id}] <- WS recv type={msg_type} (ws_id={ws_id})")

                if msg_type == "hello":
                    await self._handle_hello(data)
                elif msg_type == "ping":
                    try:
                        await websocket.send_text(json.dumps({"type": "pong", "ts": time.time()}))
                    except Exception:
                        pass
                elif msg_type == "pong":
                    self.last_seen = time.time()
                elif msg_type == "status":
                    stage = data.get("stage", "")
                    detail = data.get("detail", "")
                    logger.info(f"[ChromeBridge][{task_id}] stage={stage} ({detail})")
                    if task_id in self.tasks:
                        self.tasks[task_id].put_nowait({
                            "type": "status",
                            "stage": stage,
                            "detail": detail
                        })
                elif msg_type == "chunk":
                    if task_id in self.tasks:
                        self.tasks[task_id].put_nowait({
                            "type": "chunk",
                            "delta": data.get("delta", ""),
                            "full_text": data.get("full_text", "")
                        })
                elif msg_type == "done":
                    if task_id in self.tasks:
                        self.tasks[task_id].put_nowait({
                            "type": "done",
                            "full_text": data.get("full_text", "")
                        })
                elif msg_type == "error":
                    if task_id in self.tasks:
                        self.tasks[task_id].put_nowait({
                            "type": "error",
                            "code": data.get("code", "error"),
                            "message": data.get("message", "Unknown bridge error")
                        })
                else:
                    logger.warning(f"[ChromeBridge][{task_id}] unhandled message type: {msg_type}")
        except WebSocketDisconnect as ws_disc:
            logger.info(f"[ChromeBridge] WS disconnected (code={ws_disc.code}, reason={getattr(ws_disc, 'reason', '')}, ws_id={ws_id})")
        except Exception as e:
            logger.warning(f"[ChromeBridge] WS disconnected unexpectedly: {e} (ws_id={ws_id})")
        finally:
            async with self._lock:
                if self.active_websocket == websocket:
                    self.active_websocket = None
                    if self._ping_task:
                        self._ping_task.cancel()
                        self._ping_task = None
                    # Abort all waiting tasks immediately with ws_disconnected
                    self._abort_tasks("ws_disconnected", "Chrome extension WebSocket disconnected while waiting for response")

    def _abort_tasks(self, code: str = "ws_disconnected", message: str = "WebSocket disconnected"):
        """Aborts all currently waiting tasks with an error message."""
        for tid, q in list(self.tasks.items()):
            q.put_nowait({
                "type": "error",
                "code": code,
                "message": message
            })

    async def _handle_hello(self, data: dict):
        """Processes hello handshake, registers active providers if needed, avoiding repeated disk writes."""
        providers = [str(p).strip().lower() for p in data.get("providers", []) if p]
        self.active_providers = set(providers)
        self.user_agent = str(data.get("user_agent", "")).strip()
        self.extension_version = str(data.get("version", "1.6.0")).strip()
        logger.info(f"[ChromeBridge] Extension hello received (v{self.extension_version}): {len(providers)} providers active -> {sorted(list(self.active_providers))}")

        # Check existing accounts to avoid spamming subscriptions.yaml
        current_subs, _ = config_healer.load_or_create()
        existing_accs = {a.id: a for a in current_subs.accounts}

        for prov in providers:
            alias = PROVIDER_NAMES.get(prov, f"{prov.upper()} (Chrome Bridge)")
            acc_id = f"{prov}-web-primary"
            existing = existing_accs.get(acc_id)
            if existing and existing.status == "active":
                # Already active: record success in mesh without touching disk
                mesh.record_web_success(prov, acc_id)
                continue

            try:
                config_healer.register_web_session(
                    provider=prov,
                    account_id=acc_id,
                    alias=alias,
                    profile_dir=f"browser_profiles/{acc_id}"
                )
                mesh.record_web_success(prov, acc_id)
            except Exception as e:
                logger.warning(f"[ChromeBridge] Error activating provider '{prov}' in healer: {e}")

    async def _pinger_loop(self, ws: WebSocket):
        """Keeps MV3 extension service worker alive with 20s heartbeat."""
        try:
            while True:
                await asyncio.sleep(20.0)
                if self.active_websocket != ws:
                    break
                try:
                    await ws.send_text(json.dumps({"type": "ping", "ts": time.time()}))
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    async def cancel_all(self):
        """Cancels all active in-flight prompt tasks across the bridge."""
        async with self._lock:
            task_items = list(self.tasks.items())
            ws = self.active_websocket

        for task_id, queue in task_items:
            if ws:
                try:
                    await ws.send_text(json.dumps({"type": "cancel", "task_id": task_id}))
                except Exception:
                    pass
            queue.put_nowait({
                "type": "error",
                "code": "cancelled",
                "message": "Orchestration cancelled by user"
            })

    async def cancel_task(self, task_id: str):
        """Cancels a specific in-flight prompt task across the bridge."""
        async with self._lock:
            queue = self.tasks.get(task_id)
            ws = self.active_websocket

        if ws:
            try:
                await ws.send_text(json.dumps({"type": "cancel", "task_id": task_id}))
            except Exception:
                pass
        if queue:
            queue.put_nowait({
                "type": "error",
                "code": "cancelled",
                "message": f"Task {task_id} was cancelled"
            })

    async def close_all(self):
        """Closes active WebSocket and cleans up bridge state on server shutdown."""
        await self.cancel_all()
        async with self._lock:
            ws = self.active_websocket
            self.active_websocket = None
            if ws:
                try:
                    await ws.close(code=1000, reason="Server shutting down")
                except Exception:
                    pass
        self.active_providers.clear()

    async def stream_prompt(
        self,
        provider: str,
        prompt: str,
        model: Optional[str] = None,
        new_chat: bool = True,
        timeout: float = 120.0,
        ack_timeout: float = 25.0,
        first_chunk_timeout: float = 45.0
    ) -> AsyncIterator[str]:
        """
        Sends prompt through the Chrome Bridge WebSocket and yields text chunks as they stream.
        Enforces a 25s fail-fast acknowledgement timeout for prompt_sent/generating stages,
        and a 45s fail-fast timeout after prompt_sent waiting for the first chunk.
        """
        async with self._lock:
            ws = self.active_websocket
            if not ws:
                raise RuntimeError("Chrome Bridge WebSocket is not connected. Open Google Chrome with the ZCA extension installed.")

        clean_prov = (provider or "").strip().lower()
        task_id = f"task_{secrets.token_hex(8)}"
        queue: asyncio.Queue = asyncio.Queue()
        self.tasks[task_id] = queue

        req_payload = {
            "type": "prompt",
            "task_id": task_id,
            "provider": clean_prov,
            "model": model or "",
            "prompt": prompt,
            "new_chat": bool(new_chat),
            "timeout_sec": int(timeout)
        }

        logger.info(f"[ChromeBridge][{task_id}] -> WS send prompt (provider={clean_prov}, ws_id={id(ws)})")
        try:
            await ws.send_text(json.dumps(req_payload))
        except Exception as send_err:
            self.tasks.pop(task_id, None)
            raise RuntimeError(f"Failed to transmit prompt to Chrome Bridge (ws_id={id(ws)}): {send_err}")

        start_time = time.time()
        ack_received = False
        last_stage = "enqueued"
        last_yielded_len = 0
        prompt_sent_time: Optional[float] = None
        first_chunk_received = False
        task_succeeded = False

        try:
            while True:
                now = time.time()
                if not ack_received and (now - start_time) > ack_timeout:
                    raise TimeoutError(
                        f"Chrome Bridge fail-fast timeout ({ack_timeout}s) waiting for prompt acknowledgement from {clean_prov}. "
                        f"Last stage: {last_stage}"
                    )

                if prompt_sent_time is not None and not first_chunk_received:
                    if (now - prompt_sent_time) > first_chunk_timeout:
                        if self.is_connected() and self.active_websocket:
                            try:
                                await self.active_websocket.send_text(json.dumps({"type": "cancel", "task_id": task_id}))
                            except Exception:
                                pass
                        raise TimeoutError(
                            f"Chrome Bridge fail-fast timeout ({first_chunk_timeout}s) waiting for first response token from {clean_prov} after prompt submission. "
                            f"Last stage: {last_stage}"
                        )

                remaining_time = timeout - (now - start_time)
                if remaining_time <= 0:
                    raise TimeoutError(f"Chrome Bridge timeout ({timeout}s) waiting for response from {clean_prov}. Last stage: {last_stage}")

                max_slice = 0.5 if not first_chunk_received else 5.0
                wait_slice = max(0.01, min(remaining_time, max_slice))
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=wait_slice)
                except asyncio.TimeoutError:
                    if not self.is_connected():
                        raise RuntimeError("Chrome Bridge connection lost during generation")
                    continue

                m_type = msg.get("type")
                if m_type == "status":
                    stage = msg.get("stage", "")
                    detail = msg.get("detail", "")
                    last_stage = f"{stage}: {detail}" if detail else stage
                    if stage in ("received", "tab_ready", "injected", "prompt_sent", "generating"):
                        ack_received = True
                    if stage == "prompt_sent" and prompt_sent_time is None:
                        prompt_sent_time = time.time()
                    # Do not set first_chunk_received on generating heartbeat
                    continue
                elif m_type == "chunk":
                    ack_received = True
                    first_chunk_received = True
                    last_stage = "streaming"
                    delta = msg.get("delta", "")
                    full = msg.get("full_text", "")
                    if delta:
                        last_yielded_len += len(delta)
                        yield delta
                    elif full and len(full) > last_yielded_len:
                        diff = full[last_yielded_len:]
                        last_yielded_len = len(full)
                        yield diff
                elif m_type == "done":
                    ack_received = True
                    first_chunk_received = True
                    task_succeeded = True
                    full = msg.get("full_text", "")
                    if full and len(full) > last_yielded_len:
                        yield full[last_yielded_len:]
                    break
                elif m_type == "error":
                    code = msg.get("code", "error")
                    err_msg = msg.get("message", "Bridge execution error")
                    if code == "cancelled":
                        raise asyncio.CancelledError(f"Chrome Bridge task cancelled: {err_msg}")
                    if code == "needs_auth":
                        from core.drivers.base import WebSessionAuthError
                        raise WebSessionAuthError(f"В Chrome требуется авторизация в {clean_prov.upper()}: {err_msg}")
                    raise RuntimeError(f"[{code}] {err_msg} (Last stage: {last_stage})")
        except asyncio.CancelledError:
            task_succeeded = False
            if self.is_connected() and self.active_websocket:
                try:
                    await self.active_websocket.send_text(json.dumps({"type": "cancel", "task_id": task_id}))
                except Exception:
                    pass
            raise
        finally:
            self.tasks.pop(task_id, None)
            if not task_succeeded and self.is_connected() and self.active_websocket:
                try:
                    await self.active_websocket.send_text(json.dumps({"type": "cancel", "task_id": task_id}))
                except Exception:
                    pass

    async def execute_prompt(
        self,
        provider: str,
        prompt: str,
        model: Optional[str] = None,
        new_chat: bool = True,
        timeout: float = 120.0
    ) -> str:
        """Executes prompt via Chrome Bridge and returns complete text response."""
        chunks = []
        async for chunk in self.stream_prompt(
            provider=provider,
            prompt=prompt,
            model=model,
            new_chat=new_chat,
            timeout=timeout
        ):
            chunks.append(chunk)
        return "".join(chunks)


chrome_bridge = ChromeBridgeManager()
