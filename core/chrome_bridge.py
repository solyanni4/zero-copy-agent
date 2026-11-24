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
        # If extension reported any providers, verify inclusion; if reported empty, default to supported 6
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
        logger.info("[ChromeBridge] WebSocket connection established from Chrome extension")

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
                except Exception:
                    continue

                msg_type = data.get("type")
                if msg_type == "hello":
                    await self._handle_hello(data)
                elif msg_type == "pong":
                    self.last_seen = time.time()
                elif msg_type == "chunk":
                    task_id = data.get("task_id")
                    if task_id in self.tasks:
                        self.tasks[task_id].put_nowait({
                            "type": "chunk",
                            "delta": data.get("delta", ""),
                            "full_text": data.get("full_text", "")
                        })
                elif msg_type == "done":
                    task_id = data.get("task_id")
                    if task_id in self.tasks:
                        self.tasks[task_id].put_nowait({
                            "type": "done",
                            "full_text": data.get("full_text", "")
                        })
                elif msg_type == "error":
                    task_id = data.get("task_id")
                    if task_id in self.tasks:
                        self.tasks[task_id].put_nowait({
                            "type": "error",
                            "code": data.get("code", "error"),
                            "message": data.get("message", "Unknown bridge error")
                        })
        except WebSocketDisconnect:
            logger.info("[ChromeBridge] Chrome extension disconnected cleanly")
        except Exception as e:
            logger.warning(f"[ChromeBridge] WebSocket connection dropped: {e}")
        finally:
            async with self._lock:
                if self.active_websocket == websocket:
                    self.active_websocket = None
                    if self._ping_task:
                        self._ping_task.cancel()
                        self._ping_task = None
            # Abort all waiting tasks
            for tid, q in list(self.tasks.items()):
                q.put_nowait({
                    "type": "error",
                    "code": "disconnected",
                    "message": "Chrome extension disconnected while waiting for response"
                })

    async def _handle_hello(self, data: dict):
        """Processes hello handshake, registers active providers, and resets mesh cooldowns."""
        providers = [str(p).strip().lower() for p in data.get("providers", []) if p]
        self.active_providers = set(providers)
        self.user_agent = str(data.get("user_agent", "")).strip()
        self.extension_version = str(data.get("version", "1.6.0")).strip()
        logger.info(f"[ChromeBridge] Extension hello received (v{self.extension_version}): {len(providers)} providers active -> {sorted(list(self.active_providers))}")

        for prov in providers:
            alias = PROVIDER_NAMES.get(prov, f"{prov.upper()} (Chrome Bridge)")
            acc_id = f"{prov}-web-primary"
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

    async def stream_prompt(
        self,
        provider: str,
        prompt: str,
        model: Optional[str] = None,
        new_chat: bool = True,
        timeout: float = 120.0
    ) -> AsyncIterator[str]:
        """
        Sends prompt through the Chrome Bridge WebSocket and yields text chunks as they stream.
        """
        if not self.is_connected() or not self.active_websocket:
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

        try:
            await self.active_websocket.send_text(json.dumps(req_payload))
        except Exception as send_err:
            self.tasks.pop(task_id, None)
            raise RuntimeError(f"Failed to transmit prompt to Chrome Bridge: {send_err}")

        start_time = time.time()
        last_yielded_len = 0

        try:
            while True:
                remaining_time = timeout - (time.time() - start_time)
                if remaining_time <= 0:
                    raise TimeoutError(f"Chrome Bridge timeout ({timeout}s) waiting for response from {clean_prov}")

                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=min(remaining_time, 15.0))
                except asyncio.TimeoutError:
                    # Check if connection is still healthy
                    if not self.is_connected():
                        raise RuntimeError("Chrome Bridge connection lost during generation")
                    continue

                m_type = msg.get("type")
                if m_type == "chunk":
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
                    full = msg.get("full_text", "")
                    if full and len(full) > last_yielded_len:
                        yield full[last_yielded_len:]
                    break
                elif m_type == "error":
                    code = msg.get("code", "error")
                    err_msg = msg.get("message", "Bridge execution error")
                    if code == "needs_auth":
                        from core.drivers.base import WebSessionAuthError
                        raise WebSessionAuthError(f"В Chrome требуется авторизация в {clean_prov.upper()}: {err_msg}")
                    raise RuntimeError(f"[{code}] {err_msg}")
        finally:
            self.tasks.pop(task_id, None)
            # Send cancellation notice if we exited prematurely
            if self.is_connected() and self.active_websocket:
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
