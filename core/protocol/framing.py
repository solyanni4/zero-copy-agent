"""
Binary Wire Framing for ZCAP (Zero-Copy Agent Protocol).
11-byte fixed header: Magic (4B) + Version (1B) + Channel (1B) + Flags (1B) + Length (4B).
"""
import asyncio
import struct
from core.protocol.types import ChannelType, ZCAPFrame

MAGIC = b"ZCAP"
VERSION = 0x01
HEADER_FORMAT = "!4sBBBI"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 11 bytes

def encode_frame(channel: ChannelType, payload: bytes, flags: int = 0) -> bytes:
    """Encodes a payload into a binary ZCAP frame."""
    header = struct.pack(HEADER_FORMAT, MAGIC, VERSION, int(channel), flags, len(payload))
    return header + payload

def decode_header(header_bytes: bytes):
    """Decodes the 11-byte frame header."""
    if len(header_bytes) != HEADER_SIZE:
        raise ValueError(f"Invalid header size: expected {HEADER_SIZE}, got {len(header_bytes)}")
    
    magic, version, channel_int, flags, length = struct.unpack(HEADER_FORMAT, header_bytes)
    if magic != MAGIC:
        raise ValueError(f"Invalid magic bytes: expected {MAGIC!r}, got {magic!r}")
    if version != VERSION:
        raise ValueError(f"Unsupported ZCAP version: {version}")
    
    return ChannelType(channel_int), flags, length

async def read_frame(reader: asyncio.StreamReader) -> ZCAPFrame:
    """Reads a complete ZCAP frame from an async stream reader."""
    header_bytes = await reader.readexactly(HEADER_SIZE)
    channel, flags, length = decode_header(header_bytes)
    payload = await reader.readexactly(length)
    return ZCAPFrame(channel=channel, flags=flags, payload=payload)

async def write_frame(writer: asyncio.StreamWriter, frame: ZCAPFrame):
    """Writes a ZCAP frame to an async stream writer."""
    raw = encode_frame(frame.channel, frame.payload, frame.flags)
    writer.write(raw)
    await writer.drain()
