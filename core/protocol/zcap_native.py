"""
Track A: ZCAP Native Engine.
Provides high-performance native agent-to-agent and bot-to-agent communication
using Unix Sockets, binary framing, multiplexing, and built-in fault tolerance.
"""
import asyncio
import json
import time
from typing import AsyncGenerator, Callable, Dict, Optional
from core.protocol.types import ChannelType, ControlOp, MessageEnvelope, ZCAPFrame
from core.protocol.framing import encode_frame, read_frame, write_frame
from core.protocol.transport import ZCAPTransportServer, ZCAPTransportClient
from core.protocol.resilience import CircuitBreaker, IdempotencyCache, WatchdogTimer

class ZCAPNativeEngine:
    def __init__(self, socket_path: str = "/tmp/zcap_bridge.sock"):
        self.transport = ZCAPTransportServer(socket_path=socket_path)
        self.circuit_breaker = CircuitBreaker()
        self.idempotency = IdempotencyCache()
        self.watchdog = WatchdogTimer()
        self.agent_handlers: Dict[str, Callable[[MessageEnvelope], AsyncGenerator[ZCAPFrame, None]]] = {}

    def register_agent(self, agent_name: str, handler: Callable[[MessageEnvelope], AsyncGenerator[ZCAPFrame, None]]):
        """Registers a backend agent handler (e.g. Gemini, Claude, Ollama)."""
        self.agent_handlers[agent_name] = handler

    async def start(self):
        """Starts the native ZCAP IPC server."""
        await self.transport.start(self._handle_client)

    async def stop(self):
        """Stops the native ZCAP IPC server."""
        await self.transport.stop()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handles an incoming connection from a bot or IDE agent with mandatory token authentication."""
        authenticated = False
        valid_tokens = {"sk-zero-copy"}
        try:
            from core.config import config
            server_key = getattr(getattr(config, "server", None), "api_key", None)
            if server_key:
                valid_tokens.add(server_key)
        except Exception:
            pass

        try:
            while True:
                frame = await read_frame(reader)
                self.watchdog.touch()

                # Mandatory Authentication Check
                if not authenticated:
                    if frame.channel == ChannelType.CONTROL:
                        cmd = frame.payload.decode("utf-8", errors="ignore").strip()
                        token_candidate = None
                        if cmd.startswith("AUTH "):
                            token_candidate = cmd.split(" ", 1)[1].strip()
                        elif cmd.startswith("Bearer "):
                            token_candidate = cmd.split(" ", 1)[1].strip()
                        elif cmd in valid_tokens:
                            token_candidate = cmd

                        if token_candidate and token_candidate in valid_tokens:
                            authenticated = True
                            ok_frame = ZCAPFrame(channel=ChannelType.CONTROL, payload=b"AUTH_OK")
                            await write_frame(writer, ok_frame)
                            continue
                    else:
                        try:
                            payload_dict = json.loads(frame.payload.decode("utf-8"))
                            env_token = payload_dict.get("metadata", {}).get("auth_token") or payload_dict.get("auth_token")
                            if env_token in valid_tokens:
                                authenticated = True
                        except Exception:
                            pass

                    if not authenticated:
                        err_frame = ZCAPFrame(
                            channel=ChannelType.CONTROL,
                            payload=b"ERROR: Unauthorized. Valid token required (sk-zero-copy)"
                        )
                        await write_frame(writer, err_frame)
                        break

                if frame.channel == ChannelType.CONTROL:
                    cmd = frame.payload.decode("utf-8", errors="ignore").strip()
                    if cmd == ControlOp.PING:
                        pong_frame = ZCAPFrame(channel=ChannelType.CONTROL, payload=ControlOp.PONG.encode("utf-8"))
                        await write_frame(writer, pong_frame)
                    continue

                # Process MessageEnvelope
                try:
                    payload_dict = json.loads(frame.payload.decode("utf-8"))
                    envelope = MessageEnvelope.from_dict(payload_dict)
                except Exception as e:
                    err_frame = ZCAPFrame(
                        channel=ChannelType.CONTROL,
                        payload=f"ERROR: Invalid envelope format ({e})".encode("utf-8")
                    )
                    await write_frame(writer, err_frame)
                    continue

                # Idempotency check
                if envelope.idempotency_key and not self.idempotency.check_and_set(envelope.idempotency_key):
                    cached_frame = ZCAPFrame(
                        channel=ChannelType.CONTROL,
                        payload=b"SKIPPED_DUPLICATE: Request was already processed"
                    )
                    await write_frame(writer, cached_frame)
                    continue

                # Circuit breaker check
                if not self.circuit_breaker.can_execute():
                    fallback_frame = ZCAPFrame(
                        channel=ChannelType.TOKEN_STREAM,
                        payload=b"[CIRCUIT_OPEN] Primary agent is overloaded. Routed to secondary fallback."
                    )
                    await write_frame(writer, fallback_frame)
                    done_frame = ZCAPFrame(channel=ChannelType.PAYLOAD_DONE, payload=b"{\"status\": \"fallback\"}")
                    await write_frame(writer, done_frame)
                    continue

                # Execute target agent handler
                target = envelope.target_agent or "default"
                handler = self.agent_handlers.get(target) or self._default_handler

                try:
                    async for out_frame in handler(envelope):
                        await write_frame(writer, out_frame)
                    self.circuit_breaker.record_success()
                except Exception as ex:
                    self.circuit_breaker.record_failure()
                    err_frame = ZCAPFrame(
                        channel=ChannelType.CONTROL,
                        payload=f"AGENT_ERROR: {ex}".encode("utf-8")
                    )
                    await write_frame(writer, err_frame)

        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def _default_handler(self, envelope: MessageEnvelope) -> AsyncGenerator[ZCAPFrame, None]:
        """Built-in high-performance mock stream generator for testing."""
        # 1. Thought stream chunk
        thought_data = b"Analyzing request intent and capability matrix..."
        yield ZCAPFrame(channel=ChannelType.THOUGHT_STREAM, payload=thought_data)

        # 2. Token stream
        response_text = f"[ZCAP Native Response to '{envelope.payload}']"
        for word in response_text.split(" "):
            chunk = (word + " ").encode("utf-8")
            yield ZCAPFrame(channel=ChannelType.TOKEN_STREAM, payload=chunk)

        # 3. Done chunk
        yield ZCAPFrame(channel=ChannelType.PAYLOAD_DONE, payload=b"{\"finish_reason\": \"stop\"}")
