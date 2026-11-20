"""
Zero-Trust Local DLP Sanitizer (Security Shield) for zero-copy-agent.
Intercepts API keys, access tokens, SSH/TLS private keys, passwords, and private IP addresses
BEFORE prompts are transmitted to external web subscriptions or cloud models.
Replaces sensitive strings with reversible placeholders (__SEC_xx_TYPE__) and seamlessly
restores them in the model's response.
"""

import re
import ipaddress
import logging
from typing import Dict, Tuple, List, Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger("zcao.shield")

@dataclass
class SecretMatch:
    secret_type: str
    original: str
    placeholder: str

@dataclass
class SessionVault:
    """Ephemeral in-memory vault storing masked secrets for a single request lifecycle."""
    req_id: str
    mappings: Dict[str, str] = field(default_factory=dict)         # placeholder -> original
    reverse_map: Dict[str, str] = field(default_factory=dict)      # original -> placeholder
    secret_types: Dict[str, str] = field(default_factory=dict)     # placeholder -> secret_type
    detected_count: int = 0

    def add(self, original: str, secret_type: str) -> str:
        if original in self.reverse_map:
            return self.reverse_map[original]

        self.detected_count += 1
        placeholder = f"__SEC_{self.detected_count:02d}_{secret_type.upper()}__"
        self.mappings[placeholder] = original
        self.reverse_map[original] = placeholder
        self.secret_types[placeholder] = secret_type
        return placeholder

    def summary(self) -> Dict[str, Any]:
        return {
            "req_id": self.req_id,
            "total_masked": self.detected_count,
            "types": list(set(self.secret_types.values())),
            "placeholders": list(self.mappings.keys())
        }

    def __len__(self):
        return len(self.mappings)


class DLPShield:
    """High-speed Regex & AST-aware Data Loss Prevention Sanitizer."""

    # Pre-compiled high-confidence secret patterns
    PATTERNS = [
        # Private Keys (RSA, OpenSSH, EC, PGP)
        ("PRIV_KEY", re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')),

        # GitHub Tokens (Personal Access Token, OAuth, App, Refresh)
        ("GH_TOKEN", re.compile(r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,255}\b')),
        ("GH_PAT", re.compile(r'\bgithub_pat_[A-Za-z0-9_]{82}\b')),

        # OpenAI API Keys
        ("OAI_KEY", re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{32,128}\b')),

        # Google AI Studio / Gemini API Keys
        ("GEMINI_KEY", re.compile(r'\bAIzaSy[A-Za-z0-9_-]{33}\b')),

        # Anthropic Claude API Keys
        ("ANT_KEY", re.compile(r'\bsk-ant-[A-Za-z0-9_-]{32,128}\b')),

        # Telegram Bot Tokens
        ("TG_TOKEN", re.compile(r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b')),

        # AWS Access Keys & Secrets
        ("AWS_KEY", re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
        ("AWS_SECRET", re.compile(r'(?i)(?:aws_secret_access_key|aws_secret)\s*[:=]\s*["\']?([A-Za-z0-9/+=]{40})["\']?')),

        # HuggingFace Tokens
        ("HF_TOKEN", re.compile(r'\bhf_[A-Za-z0-9]{34}\b')),

        # JSON Web Tokens (JWT)
        ("JWT_TOKEN", re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b')),

        # URI Database / Service Credentials (e.g. postgres://user:password@host)
        ("URI_CRED", re.compile(r'://([^:\s\'"]+):([^@\s/\'"]+)@')),

        # Passwords / Secrets assignment in config & code
        ("PASSWORD", re.compile(r'(?i)\b(?:password|passwd|pwd|db_pass|auth_pass|secret)\s*[:=]\s*["\']([^"\'\s]{4,64})["\']')),
        ("API_SECRET", re.compile(r'(?i)\b(?:api_secret|client_secret|auth_token)\s*[:=]\s*["\']([^"\'\s]{8,128})["\']')),

        # Internal Network Domains
        ("INTERNAL_DOMAIN", re.compile(r'\b[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*\.(?:local|lan|internal|home|corp)\b')),
    ]

    # Regex for IPv4 addresses (validated via ipaddress module)
    IPV4_PATTERN = re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b')

    @classmethod
    def sanitize(cls, text: str, req_id: str = "") -> Tuple[str, SessionVault]:
        """
        Sanitizes text by replacing all matched secrets with reversible placeholders.
        Returns: (sanitized_text, SessionVault)
        """
        if not text:
            return text, SessionVault(req_id=req_id)

        vault = SessionVault(req_id=req_id)
        result = text

        # 1. Match structured token and key patterns
        for secret_type, regex in cls.PATTERNS:
            def replacer(match):
                # If pattern has capturing groups (e.g. password="secret" or ://user:pass@), mask the secret group
                if match.groups():
                    full_str = match.group(0)
                    secret_val = match.group(2) if len(match.groups()) > 1 else match.group(1)
                    placeholder = vault.add(secret_val, secret_type)
                    return full_str.replace(secret_val, placeholder, 1)
                else:
                    secret_val = match.group(0)
                    return vault.add(secret_val, secret_type)

            result = regex.sub(replacer, result)

        # 2. Match and validate Private IPv4 addresses (192.168.x.x, 10.x.x.x, 172.16-31.x.x)
        def ip_replacer(match):
            ip_str = match.group(0)
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                # Only mask private / loopback IPs, do NOT mask public IP benchmarks
                if ip_obj.is_private or ip_obj.is_loopback:
                    # Don't mask standard localhost 127.0.0.1 or 0.0.0.0 if user specifies it as gateway
                    if ip_str in ("127.0.0.1", "0.0.0.0", "localhost"):
                        return ip_str
                    return vault.add(ip_str, "IP_ADDR")
            except ValueError:
                pass
            return ip_str

        result = cls.IPV4_PATTERN.sub(ip_replacer, result)

        if len(vault) > 0:
            logger.info(f"[DLP Shield] Masked {len(vault)} sensitive items in request '{req_id}': {vault.summary()['types']}")

        return result, vault

    @classmethod
    def restore(cls, text: str, vault: SessionVault) -> str:
        """
        Restores original secrets from vault placeholders in model response.
        """
        if not text or not vault or len(vault) == 0:
            return text

        result = text
        for placeholder, original in vault.mappings.items():
            if placeholder in result:
                result = result.replace(placeholder, original)

        return result

    @classmethod
    def restore_stream_chunk(cls, chunk: str, vault: SessionVault, buffer: str = "") -> Tuple[str, str]:
        """
        Helper for SSE streaming: handles chunk boundaries where placeholder might be split.
        Returns: (restored_output, remaining_buffer)
        """
        if not vault or len(vault) == 0:
            return chunk, ""

        combined = buffer + chunk
        # If no placeholder prefix "__SEC_" in stream, flush immediately
        if "__SEC_" not in combined:
            return combined, ""

        # Check if an incomplete placeholder is at the end of combined
        match = re.search(r'__SEC_\d{0,2}[A-Za-z_]{0,15}$', combined)
        if match:
            # Hold the partial token in buffer for the next chunk
            flushable = combined[:match.start()]
            new_buffer = combined[match.start():]
        else:
            flushable = combined
            new_buffer = ""

        # Restore known placeholders in the flushable portion
        for placeholder, original in vault.mappings.items():
            if placeholder in flushable:
                flushable = flushable.replace(placeholder, original)

        return flushable, new_buffer


shield = DLPShield()
