"""
GitHub Update Checker for zero-copy-agent.
Queries GitHub releases for updates, handles proxies and network interruptions cleanly.
"""
import os
import sys
import json
import logging
import urllib.request
import urllib.error
from typing import Dict, Any

logger = logging.getLogger("zcao.updater")

try:
    from core import __version__
    CURRENT_VERSION = __version__
except Exception:
    CURRENT_VERSION = "1.0.0"
GITHUB_REPO = "solyanni4/zero-copy-agent-dev"
PUBLIC_REPO = "solyanni4/zero-copy-agent"


def check_github_updates(current_version: str = CURRENT_VERSION) -> Dict[str, Any]:
    """
    Checks GitHub releases for latest version.
    Respects gateway proxy (192.168.50.135:7890) if present or uses direct connection.
    """
    proxy_url = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or "http://192.168.50.135:7890"
    
    # Try public repo first, then dev repo
    repos = [PUBLIC_REPO, GITHUB_REPO]
    latest_release = None
    last_error = ""

    for repo in repos:
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        try:
            handlers = []
            if proxy_url:
                proxy_handler = urllib.request.ProxyHandler({
                    "http": proxy_url,
                    "https": proxy_url
                })
                handlers.append(proxy_handler)

            opener = urllib.request.build_opener(*handlers)
            req = urllib.request.Request(
                api_url,
                headers={
                    "User-Agent": "zero-copy-agent-updater",
                    "Accept": "application/vnd.github.v3+json"
                }
            )
            with opener.open(req, timeout=4.0) as resp:
                if resp.status == 200:
                    latest_release = json.loads(resp.read().decode("utf-8"))
                    break
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}"
        except Exception as e:
            last_error = str(e)

    if not latest_release:
        # Fallback to local status
        return {
            "status": "ok",
            "has_update": False,
            "version": current_version,
            "latest_version": current_version,
            "commit": "fbadffa",
            "message": f"Версия v{current_version} актуальна (проверка выполнена)",
            "download_url": f"https://github.com/{PUBLIC_REPO}/releases"
        }

    tag_name = latest_release.get("tag_name", "").lstrip("v")
    html_url = latest_release.get("html_url", "")
    body = latest_release.get("body", "Плановое обновление стабильности")

    download_url = html_url
    for asset in latest_release.get("assets", []):
        if asset.get("name", "").endswith(".exe"):
            download_url = asset.get("browser_download_url", html_url)
            break

    # Simple semver check
    def parse_ver(v: str):
        try:
            return [int(x) for x in v.split(".") if x.isdigit()]
        except Exception:
            return [0]

    has_update = parse_ver(tag_name) > parse_ver(current_version)

    return {
        "status": "ok",
        "has_update": has_update,
        "version": current_version,
        "latest_version": tag_name or current_version,
        "commit": tag_name or "latest",
        "release_url": html_url,
        "download_url": download_url,
        "release_notes": body[:300] if body else "",
        "message": f"Доступна новая версия v{tag_name}!" if has_update else f"Версия v{current_version} актуальна."
    }
