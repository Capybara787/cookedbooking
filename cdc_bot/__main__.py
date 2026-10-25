"""Command line entry point:  python -m cdc_bot <command>

    chat-id         find your Telegram chat id (after messaging your bot once)
    test-telegram   send a test message
    check           look at the booking page once and print what the bot sees
    run             the real thing: check every few minutes, message you on changes
                    (then /pause, /resume and /status work from Telegram)
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .alerts import AlertPolicy, Decision
from .commands import BotState, CommandListener
from .config import MIN_POLL_SECONDS, ROOT, Config, ConfigError, load_config
from .models import CheckResult, Status
from .notifier import Telegram, TelegramError
from .parser import classify_page, redact_html

log = logging.getLogger("cdc_bot")
MAX_QUEUED_MESSAGES = 20


# ------------------------------------------------------------------ commands
def cmd_chat_id(cfg: Config, args: argparse.Namespace) -> int:
    chats = Telegram(cfg.require_token()).find_chats()
    if not chats:
        print("No messages found. Open Telegram, send any message to your bot, then run this again.")
        return 1
    for chat_id, name in chats:
        print(f"{name}: put this in .env ->  TELEGRAM_CHAT_ID={chat_id}")
    return 0


def cmd_test_telegram(cfg: Config, args: argparse.Namespace) -> int:
    token, chat_id = cfg.require_telegram()
    Telegram(token, chat_id).send("✅ CDC bot test: Telegram is wired up correctly.")
    print("Sent. Check Telegram.")
    return 0


def cmd_check(cfg: Config, args: argparse.Namespace) -> int:
    if args.from_file:  # parse a saved page instead of using the browser
        result = classify_page(Path(args.from_file).read_text(encoding="utf-8"))
        _print_result(result)
        return 1 if result.status.is_problem else 0

    from .browser import BrowserSession  # imported here so the other commands don't need Playwright

    with BrowserSession(cfg) as session:
        result = session.check()
        _print_result(result)
        if args.dump:
            _save_dump(session.current_html())
    return 1 if result.status.is_problem else 0


def cmd_run(cfg: Config, args: argparse.Namespace) -> int:
    token, chat_id = cfg.require_telegram()
    notifier = Telegram(token, chat_id)
    from .browser import BrowserSession

    policy = AlertPolicy(
        booking_url=cfg.booking_url,
        heartbeat_every=timedelta(hours=cfg.heartbeat_hours),
        cooldown=timedelta(minutes=cfg.cooldown_minutes),
        started_at=datetime.now(),
    )
    state = BotState(poll_seconds=cfg.poll_seconds)  # shared with the Telegram command handler
    listener = CommandListener(notifier, chat_id, state)
    listener.start()

    # Messages wait in an "outbox" until Telegram confirms delivery, so a network
    # blip doesn't swallow a slot alert.
    outbox = [
        f"🟢 CDC bot started. Checking about every {cfg.poll_seconds} seconds. "
        "Send /pause, /resume or /status anytime."
    ]
    log.info("Started. Polling about every %s seconds.", cfg.poll_seconds)
    keepalive_every = timedelta(minutes=cfg.keepalive_minutes)
    next_keepalive = datetime.now()
    session_warned = False  # have we told you the session looks dead while paused?
    was_paused = False

    with BrowserSession(cfg) as session:
        try:
            while True:
                if state.paused:
                    now = datetime.now()
                    if not was_paused:  # just paused: we touched the site moments ago
                        was_paused = True
                        next_keepalive = now + keepalive_every
                        state.keepalive = f"every {cfg.keepalive_minutes:g} min" if cfg.keepalive_minutes > 0 else "off"
                    outbox.extend(policy.paused_reminder(now))
                    if keepalive_every > timedelta(0) and now >= next_keepalive:
                        messages, session_warned = _keep_alive(session, state, session_warned)
                        outbox.extend(messages)
                        next_keepalive = datetime.now() + keepalive_every
                    outbox = _flush(notifier, outbox)
                    listener.wait(60)  # no slot checks while paused, but keep listening for /resume
                    continue
                if was_paused:
                    policy.reset_seen_slots()  # so you hear about whatever is available right now
                    session_warned = False
                    was_paused = False

                result = _safe_check(session)
                state.last_result, state.last_check_at = result, datetime.now()
                log.info("Check: %s %s", result.status.value, result.detail)

                decision = policy.handle(result, datetime.now())
                outbox.extend(decision.messages)
                outbox = _flush(notifier, outbox)

                delay = _next_delay(cfg, decision)
                if result.status.is_problem:
                    # Logged out or a page glitch: look again soon instead of waiting out the
                    # full interval. Still never faster than the 60 s guard rail.
                    delay = MIN_POLL_SECONDS * random.uniform(1.0, 1.3)
                state.next_check_at = datetime.now() + timedelta(seconds=delay)
                listener.wait(delay)  # sleeps, but answers /pause, /resume and /status meanwhile
        except KeyboardInterrupt:
            log.info("Stopped by user.")
        except Exception as exc:  # noqa: BLE001 - tell you before dying silently
            log.exception("Bot crashed.")
            try:
                notifier.send(f"💥 CDC bot crashed and has stopped ({type(exc).__name__}). See cdc_bot.log.")
            except TelegramError:
                pass
            raise
    return 0


# ------------------------------------------------------------------ helpers
def _safe_check(session) -> CheckResult:
    try:
        return session.check()
    except Exception as exc:  # noqa: BLE001 - one bug shouldn't kill a 24/7 bot
        log.exception("Unexpected error during a check.")
        return CheckResult(Status.UNEXPECTED, detail=f"Internal error: {type(exc).__name__}: {str(exc)[:150]}")


def _keep_alive(session, state: BotState, warned: bool) -> tuple[list[str], bool]:
    """One keep-alive. Returns (messages to send, whether you've now been warned)."""
    try:
        problem = session.keep_alive()
    except Exception as exc:  # noqa: BLE001 - one bug shouldn't kill a 24/7 bot
        log.exception("Unexpected error during a keep-alive.")
        problem = f"Internal error: {type(exc).__name__}: {str(exc)[:100]}"
    log.info("Keep-alive: %s", problem or "ok")
    state.keepalive = f"{'failed' if problem else 'ok'} at {datetime.now():%H:%M}"

    if problem and not warned:
        return [
            "⚠️ While paused, the bot couldn't keep your CDC session alive.\n"
            f"{problem}\n"
            "Before you /resume, check the bot's browser is open and log in again "
            "(in a normal tab, not the bot's own tab). I'll tell you when the session looks good again."
        ], True
    if not problem and warned:
        return ["✅ Your CDC session looks good again."], False
    return [], warned


def _flush(notifier: Telegram, outbox: list[str]) -> list[str]:
    """Send queued messages in order. Whatever fails stays queued for the next round."""
    while outbox:
        try:
            notifier.send(outbox[0])
        except TelegramError as exc:
            log.warning("Couldn't send a Telegram message (will retry): %s", exc)
            break
        outbox.pop(0)
    return outbox[-MAX_QUEUED_MESSAGES:]


def _next_delay(cfg: Config, decision: Decision) -> float:
    if decision.cooldown_seconds:
        return decision.cooldown_seconds
    # A little randomness so we aren't hitting the server on an exact metronome.
    return cfg.poll_seconds * random.uniform(0.8, 1.2)


def _print_result(result: CheckResult) -> None:
    print(f"Status: {result.status.value}")
    if result.detail:
        print(f"Detail: {result.detail}")
    for slot in sorted(result.slots, key=lambda s: s.sort_key):
        print(f"  - {slot.describe()}")


def _save_dump(html: str) -> None:
    if not html:
        print("Nothing to dump (no page available).")
        return
    folder = ROOT / "debug"
    folder.mkdir(exist_ok=True)
    path = folder / f"page_{datetime.now():%Y%m%d_%H%M%S}.html"
    path.write_text(redact_html(html), encoding="utf-8")
    print(f"Saved a redacted copy to {path}")
    print("Skim it before sharing: it may still contain your name or other personal details.")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(ROOT / "cdc_bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
        ],
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)  # its debug logs include URLs (and so the token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cdc_bot", description="Watch the CDC booking page and ping you on Telegram.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("chat-id", help="find your Telegram chat id")
    sub.add_parser("test-telegram", help="send a test message")
    check = sub.add_parser("check", help="check the page once and print the result")
    check.add_argument("--dump", action="store_true", help="save a redacted copy of the page to ./debug/")
    check.add_argument("--from-file", metavar="HTML", help="parse a saved HTML file instead of using the browser")
    sub.add_parser("run", help="check repeatedly and message you when something changes")
    args = parser.parse_args(argv)

    commands = {"chat-id": cmd_chat_id, "test-telegram": cmd_test_telegram, "check": cmd_check, "run": cmd_run}
    _setup_logging()
    try:
        return commands[args.command](load_config(), args)
    except (ConfigError, TelegramError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
