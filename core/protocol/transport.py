"""
Transport Layer for ZCAP.
High-speed Unix Domain Socket (IPC) for Linux/macOS, TCP fallback for Windows.
"""
import asyncio
import os
import sys
from pathlib import Path
from typing import Callable, Coroutine, Optional
from core.protocol.framing import read_frame, write_frame
from core.protocol.types import ZCAPFrame

DEFAULT_SOCKET_PATH = "/tmp/zcap_bridge.sock"
DEFAULT_TCP_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = 8999

class ZCAPTransportServer:
    def __init__(self, socket_path: str = DEFAULT_SOCKET_PATH, port: int = DEFAULT_TCP_PORT):
        self.socket_path = socket_path
        self.port = port
        self.is_unix = sys.platform != "win32"
        self.server: Optional[asyncio.AbstractServer] = None
        self.is_running = False

    async def start(self, handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Coroutine]):
        if self.is_unix:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
            self.server = await asyncio.start_unix_server(handler, path=self.socket_path)
        else:
            self.server = await asyncio.start_server(handler, host=DEFAULT_TCP_HOST, port=self.port)
        self.is_running = True

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        if self.is_unix and os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass
        self.is_running = False

class ZCAPTransportClient:
    def __init__(self, socket_path: str = DEFAULT_SOCKET_PATH, port: int = DEFAULT_TCP_PORT):
        self.socket_path = socket_path
        self.port = port
        self.is_unix = sys.platform != "win32"
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None

    async def connect(self):
        if self.is_unix:
            self.reader, self.writer = await asyncio.open_unix_connection(path=self.socket_path)
        else:
            self.reader, self.writer = await asyncio.open_connection(host=DEFAULT_TCP_HOST, port=self.port)

    async def send(self, frame: ZCAPFrame):
        if not self.writer:
            raise ConnectionError("ZCAP client not connected")
        await write_frame(self.writer, frame)

    async def receive(self) -> ZCAPFrame:
        if not self.reader:
            raise ConnectionError("ZCAP client not connected")
        return await read_frame(self.reader)

    async def close(self):
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None
            self.reader = None
