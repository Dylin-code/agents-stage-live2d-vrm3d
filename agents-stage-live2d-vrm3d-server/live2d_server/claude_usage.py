"""Query Claude Code usage via Anthropic OAuth API.

Reads the OAuth access token from macOS Keychain (or env var fallback)
and fetches utilization data from the Anthropic usage endpoint.
"""

import json
import logging
import platform
import subprocess
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

USAGE_API_URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_EXCHANGE_URL = "https://platform.claude.com/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
KEYCHAIN_SERVICE = "Claude Code-credentials"
REQUEST_TIMEOUT = 4.0

# In-memory cache to avoid hitting the API too frequently
_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0}
CACHE_TTL_SECONDS = 30.0


def _read_credentials_file() -> Optional[dict[str, Any]]:
    """Read Claude Code OAuth credentials from ~/.claude/.credentials.json (cross-platform)."""
    from pathlib import Path
    cred_path = Path.home() / ".claude" / ".credentials.json"
    if not cred_path.exists():
        return None
    try:
        data = json.loads(cred_path.read_text(encoding="utf-8"))
        oauth = data.get("claudeAiOauth")
        if not isinstance(oauth, dict):
            logger.warning("Credentials file missing claudeAiOauth key")
            return None
        return oauth
    except Exception:
        logger.exception("Failed to read credentials file: %s", cred_path)
        return None


def _read_keychain_credentials() -> Optional[dict[str, Any]]:
    """Read Claude Code OAuth credentials from macOS Keychain."""
    if platform.system() != "Darwin":
        return None
    try:
        import getpass
        account = getpass.getuser()
        result = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            logger.warning("Keychain read failed (rc=%d): %s", result.returncode, result.stderr.strip())
            return None
        raw = result.stdout.strip()
        if not raw:
            return None
        data = json.loads(raw)
        oauth = data.get("claudeAiOauth")
        if not isinstance(oauth, dict):
            logger.warning("Keychain data missing claudeAiOauth key")
            return None
        return oauth
    except Exception:
        logger.exception("Failed to read Keychain credentials")
        return None


def _get_access_token() -> Optional[str]:
    """Retrieve a valid access token, refreshing if expired."""
    import os
    env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if env_token:
        return env_token

    # Try credentials file first (works on all platforms), then macOS Keychain
    oauth = _read_credentials_file() or _read_keychain_credentials()
    if not oauth:
        return None

    access_token = oauth.get("accessToken")
    expires_at = oauth.get("expiresAt")

    # Check expiration (expiresAt is epoch milliseconds)
    if isinstance(expires_at, (int, float)) and expires_at > 0:
        now_ms = time.time() * 1000
        if now_ms < expires_at - 30_000:  # 30s buffer
            return access_token

    # Token expired — try refresh
    refresh_token = oauth.get("refreshToken")
    if not refresh_token:
        logger.warning("Access token expired and no refresh token available")
        return None

    logger.info("Access token expired, attempting refresh")
    try:
        resp = httpx.post(
            TOKEN_EXCHANGE_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OAUTH_CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        new_tokens = resp.json()
        return new_tokens.get("access_token")
    except Exception:
        logger.exception("Token refresh failed")
        return access_token  # try stale token as fallback


async def fetch_claude_usage() -> Optional[dict[str, Any]]:
    """Fetch Claude usage data from the Anthropic API. Returns cached data if fresh."""
    now = time.time()
    if _cache["data"] is not None and (now - _cache["fetched_at"]) < CACHE_TTL_SECONDS:
        return _cache["data"]

    token = _get_access_token()
    if not token:
        logger.warning("No Claude OAuth token available")
        return None

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                USAGE_API_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "anthropic-beta": "oauth-2025-04-20",
                    "Content-Type": "application/json",
                },
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            _cache["data"] = data
            _cache["fetched_at"] = time.time()
            return data
    except Exception:
        logger.exception("Failed to fetch Claude usage")
        # Return stale cache on error
        if _cache["data"] is not None:
            return _cache["data"]
        return None


def format_usage_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Extract a frontend-friendly summary from raw usage data."""
    result: dict[str, Any] = {
        "five_hour": None,
        "seven_day": None,
        "extra_usage": None,
    }

    five_hour = data.get("five_hour")
    if isinstance(five_hour, dict) and five_hour.get("utilization") is not None:
        result["five_hour"] = {
            "utilization": five_hour["utilization"],
            "remaining": round(100 - five_hour["utilization"], 1),
            "resets_at": five_hour.get("resets_at"),
        }

    seven_day = data.get("seven_day")
    if isinstance(seven_day, dict) and seven_day.get("utilization") is not None:
        result["seven_day"] = {
            "utilization": seven_day["utilization"],
            "remaining": round(100 - seven_day["utilization"], 1),
            "resets_at": seven_day.get("resets_at"),
        }

    extra_usage = data.get("extra_usage")
    if isinstance(extra_usage, dict):
        result["extra_usage"] = {
            "is_enabled": extra_usage.get("is_enabled", False),
            "monthly_limit": extra_usage.get("monthly_limit"),
            "used_credits": extra_usage.get("used_credits"),
            "utilization": extra_usage.get("utilization"),
        }

    return result
