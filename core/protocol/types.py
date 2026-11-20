"""
Protocol Types and Enums for ZCAP (Zero-Copy Agent Protocol).
"""
import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Union

class ChannelType(enum.IntEnum):
    CONTROL = 0x01       # Handshake, PING/PONG, Abort, Backpressure
    TOKEN_STREAM = 0x02  # Real-time generated assistant text tokens
    THOUGHT_STREAM = 0x03# Reasoning chain tokens (DeepSeek R1 / o1 / Gemini Thinking)
    TOOL_RPC = 0x04      # Tool call request, execution result, tool error
    PAYLOAD_DONE = 0x05  # Generation finished, usage stats, finish reason

class ControlOp(str, enum.Enum):
    PING = "PING"
    PONG = "PONG"
    ABORT = "ABORT"
    HANDSHAKE = "HANDSHAKE"
    BUSY = "BUSY"
    READY = "READY"

@dataclass
class ZCAPFrame:
    """Low-level wire frame."""
    channel: ChannelType
    payload: bytes
    flags: int = 0
    magic: bytes = b"ZCAP"
    version: int = 0x01

@dataclass
class MessageEnvelope:
    """High-level semantic envelope for bot-to-agent and agent-to-agent communication."""
    sender_id: str
    channel: ChannelType
    payload: Union[str, Dict[str, Any]]
    msg_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    idempotency_key: Optional[str] = None
    thought: Optional[str] = None
    target_agent: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "sender_id": self.sender_id,
            "target_agent": self.target_agent,
            "channel": int(self.channel),
            "thought": self.thought,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MessageEnvelope":
        return cls(
            msg_id=data.get("msg_id", uuid.uuid4().hex),
            sender_id=data.get("sender_id", "unknown"),
            target_agent=data.get("target_agent"),
            channel=ChannelType(data.get("channel", ChannelType.TOKEN_STREAM)),
            thought=data.get("thought"),
            idempotency_key=data.get("idempotency_key"),
            payload=data.get("payload", ""),
            timestamp=data.get("timestamp", time.time()),
        )
