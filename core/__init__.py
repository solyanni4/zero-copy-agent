"""
zero-copy-agent core module
"""
from core.version import __version__
from core.discovery import resolve_http_proxy

__all__ = ["__version__", "resolve_http_proxy"]
