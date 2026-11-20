"""
Track B: Industry Adapters (FastMCP JSON-RPC & OpenAI SSE Stream).
Provides standard JSON-RPC 2.0 / MCP and SSE text-based wire formatting for comparison.
"""
import asyncio
import json
from typing import AsyncGenerator, Dict, Any

class FastMCPAdapter:
    """Simulates standard FastMCP (Model Context Protocol) over JSON-RPC 2.0 text transport."""
    def __init__(self):
        self.req_counter = 0

    def encode_request(self, tool_name: str, arguments: Dict[str, Any]) -> bytes:
        self.req_counter += 1
        msg = {
            "jsonrpc": "2.0",
            "method": f"tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            },
            "id": self.req_counter
        }
        return (json.dumps(msg) + "\n").encode("utf-8")

    def decode_response(self, raw_bytes: bytes) -> Dict[str, Any]:
        line = raw_bytes.decode("utf-8").strip()
        return json.loads(line)

    async def stream_tokens_sse(self, text: str) -> AsyncGenerator[bytes, None]:
        """Simulates OpenAI / FastMCP Server-Sent Events (SSE) text streaming."""
        words = text.split(" ")
        for word in words:
            chunk = {
                "id": "chatcmpl-bench",
                "object": "chat.completion.chunk",
                "choices": [{
                    "delta": {"content": word + " "},
                    "index": 0
                }]
            }
            sse_line = f"data: {json.dumps(chunk)}\n\n"
            yield sse_line.encode("utf-8")
        yield b"data: [DONE]\n\n"
