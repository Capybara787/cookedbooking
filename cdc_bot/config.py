"""Settings, loaded from the .env file in the project folder.

Secrets (the Telegram token) live in .env and nowhere else: not in the code,
not in git, not in chat messages.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_BOOKING_URL = "https://bookingportal.cdc.com.sg/NewPortal/Booking/BookingPL.aspx"
MIN_POLL_SECONDS = 60  # guard rail: be gentle with the server, whatever .env says
MIN_KEEPALIVE_MINUTES = 2.0

log = logging.getLogger(__name__)


class ConfigError(Exception):
    """A setting is missing or invalid. The message says how to fix it."""


@dataclass(frozen=True)
class Config:
    telegram_token: str
    telegram_chat_id: str
    cdp_url: str
    booking_url: str
    course_value: str
    poll_seconds: int
    cooldown_minutes: float
    heartbeat_hours: float
    keepalive_minutes: float = 5.0  # while paused; 0 = off

    def require_token(self) -> str:
        if not self.telegram_token:
            raise ConfigError("TELEGRAM_BOT_TOKEN is empty. Put the token from @BotFather in your .env file.")
        return self.telegram_token

    def require_telegram(self) -> tuple[str, str]:
        token = self.require_token()
        if not self.telegram_chat_id:
            raise ConfigError("TELEGRAM_CHAT_ID is empty. Run `python -m cdc_bot chat-id` to find it.")
        return token, self.telegram_chat_id


def _number(name: str, default, cast=float):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, but .env has {raw!r}.") from None


def load_config() -> Config:
    # Real environment variables win over .env (load_dotenv's default behaviour).
    load_dotenv(ROOT / ".env")

    poll = _number("POLL_SECONDS", 180, int)
    if poll < MIN_POLL_SECONDS:
        log.warning("POLL_SECONDS=%s is too aggressive; using %s instead.", poll, MIN_POLL_SECONDS)
        poll = MIN_POLL_SECONDS

    keepalive = max(0.0, _number("KEEPALIVE_MINUTES", 5.0))
    if 0 < keepalive < MIN_KEEPALIVE_MINUTES:
        log.warning("KEEPALIVE_MINUTES=%s is too aggressive; using %s instead.", keepalive, MIN_KEEPALIVE_MINUTES)
        keepalive = MIN_KEEPALIVE_MINUTES

    return Config(
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        # 127.0.0.1 rather than "localhost": on Windows "localhost" can resolve to
        # IPv6 (::1) while Chrome only listens on IPv4.
        cdp_url=os.getenv("CDP_URL", "http://127.0.0.1:9222").strip(),
        booking_url=os.getenv("BOOKING_URL", DEFAULT_BOOKING_URL).strip(),
        course_value=os.getenv("COURSE_VALUE", "ONETEAM").strip(),
        poll_seconds=poll,
        cooldown_minutes=_number("COOLDOWN_MINUTES", 10.0),
        heartbeat_hours=_number("HEARTBEAT_HOURS", 24.0),
        keepalive_minutes=keepalive,
    )
