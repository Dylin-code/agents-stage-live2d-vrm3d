"""Environment-driven configuration for the Telegram integration.

The integration is opt-in. If ``TELEGRAM_BOT_TOKEN`` is unset or empty,
the runtime treats the bot as disabled and the FastAPI lifespan skips
starting it. This keeps local dev frictionless — contributors who don't
care about TG never need to touch the env file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    """Resolved environment configuration for the Telegram integration."""

    token: str = ""
    allowed_user_ids: frozenset[int] = field(default_factory=frozenset)
    bindings_file: Optional[Path] = None
    binding_code_ttl_seconds: int = 600
    bot_username: str = ""

    def is_enabled(self) -> bool:
        return bool(self.token)

    def is_user_allowed(self, tg_user_id: int) -> bool:
        """When no whitelist is configured, all users are allowed.

        We keep the open-by-default behavior so a fresh ``.env`` (just
        ``TELEGRAM_BOT_TOKEN=...``) works out of the box. Operators who
        want to lock the bot down add ``TELEGRAM_ALLOWED_USERS``.
        """
        if not self.allowed_user_ids:
            return True
        return tg_user_id in self.allowed_user_ids


def _parse_user_ids(raw: str) -> frozenset[int]:
    if not raw:
        return frozenset()
    out: set[int] = set()
    for piece in raw.replace(";", ",").split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.add(int(piece))
        except ValueError:
            # Skip malformed entries silently — the bot owner sees this
            # in their .env and we don't want a typo to crash startup.
            continue
    return frozenset(out)


def _resolve_bindings_path(env_value: str) -> Path:
    if env_value:
        return Path(env_value).expanduser()
    # Repo-root sibling to ``config/master-agent/conversations`` so the
    # whole master-agent state lives under one prefix.
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "config" / "master-agent" / "telegram_bindings.json"


def load_telegram_config() -> TelegramConfig:
    """Read env vars into a :class:`TelegramConfig`.

    Env vars consumed:

    - ``TELEGRAM_BOT_TOKEN`` — bot token from @BotFather. Empty → disabled.
    - ``TELEGRAM_ALLOWED_USERS`` — optional comma-separated TG user ids
      (numeric). When set, only those users can ``/bind``.
    - ``TELEGRAM_BINDINGS_FILE`` — override binding storage path.
    - ``TELEGRAM_BINDING_CODE_TTL_SECONDS`` — override default 600s.
    - ``TELEGRAM_BOT_USERNAME`` — optional, shown in the frontend UI to
      tell the user which bot to open. The bot itself doesn't need it.
    """
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    raw_users = (os.getenv("TELEGRAM_ALLOWED_USERS") or "").strip()
    raw_ttl = (os.getenv("TELEGRAM_BINDING_CODE_TTL_SECONDS") or "").strip()
    bindings_env = (os.getenv("TELEGRAM_BINDINGS_FILE") or "").strip()
    username = (os.getenv("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")

    try:
        ttl = int(raw_ttl) if raw_ttl else 600
    except ValueError:
        ttl = 600
    if ttl < 30:
        ttl = 30

    return TelegramConfig(
        token=token,
        allowed_user_ids=_parse_user_ids(raw_users),
        bindings_file=_resolve_bindings_path(bindings_env),
        binding_code_ttl_seconds=ttl,
        bot_username=username,
    )
