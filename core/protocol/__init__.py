"""
Zero-Copy Agent Protocol (ZCAP) Package.
Includes ZCAP Native (Track A) and Industry Adapters (Track B).
"""
from core.protocol.types import ChannelType, ControlOp, MessageEnvelope, ZCAPFrame
from core.protocol.framing import encode_frame, decode_header, read_frame, write_frame
from core.protocol.transport import ZCAPTransportServer, ZCAPTransportClient
from core.protocol.zcap_native import ZCAPNativeEngine
from core.protocol.adapters import FastMCPAdapter
from core.protocol.resilience import CircuitBreaker, WatchdogTimer, IdempotencyCache

__all__ = [
    "ChannelType",
    "ControlOp",
    "MessageEnvelope",
    "ZCAPFrame",
    "encode_frame",
    "decode_header",
    "read_frame",
    "write_frame",
    "ZCAPTransportServer",
    "ZCAPTransportClient",
    "ZCAPNativeEngine",
    "FastMCPAdapter",
    "CircuitBreaker",
    "WatchdogTimer",
    "IdempotencyCache",
]
