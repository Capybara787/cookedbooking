"""Telegram commands: pause, resume and status.

SECURITY: a Telegram bot is publicly reachable. Anyone who finds its username can
message it. So only the chat in TELEGRAM_CHAT_ID is obeyed; everything else is ignored.

The pieces:
  handle_command()   pure logic: command text in, reply out (easy to test)
  CommandListener    waits between checks while listening for commands
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime

from .models import CheckResult
from .notifier import Telegram, TelegramError

log = logging.getLogger("cdc_bot")

MENU = [
    ("pause", "Stop checking for slots"),
    ("resume", "Start checking again"),
    ("status", "What is the bot doing?"),
    ("help", "List the commands"),
]
HELP_TEXT = (
    "Commands:\n"
    "/pause (or /stop) - stop checking for slots\n"
    "/resume (or /start) - start checking again\n"
    "/status - what the bot is doing"
)
LONG_POLL_SECONDS = 25  # how long one request waits for a message before returning


@dataclass
class BotState:
    """What the main loop and the command handler share."""
    poll_seconds: int
    paused: bool = False
    last_result: CheckResult | None = None
    last_check_at: datetime | None = None
    next_check_at: datetime | None = None


@dataclass(frozen=True)
class Reply:
    text: str
    wake: bool = False  # True = stop waiting and run a check right now


def handle_command(text: str, state: BotState, now: datetime) -> Reply:
    word = text.strip().split()[0].lower().split("@")[0]  # "/Pause@MyBot" -> "/pause"

    if word in ("/pause", "/stop"):
        if state.paused:
            return Reply("Already paused. Send /resume to start checking again.")
        state.paused = True
        return Reply("⏸ Paused. I won't check CDC until you send /resume.")

    if word in ("/resume", "/start"):  # Telegram itself sends /start when you first open a bot
        if not state.paused:
            return Reply("Already running.\n" + describe(state, now))
        state.paused = False
        return Reply("▶️ Resumed. Checking now...", wake=True)

    if word == "/status":
        return Reply(describe(state, now))

    return Reply(HELP_TEXT if word == "/help" else f"Unknown command.\n{HELP_TEXT}")


def describe(state: BotState, now: datetime) -> str:
    lines = ["⏸ Paused: not checking for slots." if state.paused else "▶️ Running."]
    result = state.last_result
    if result and state.last_check_at:
        lines.append(f"Last check at {state.last_check_at:%H:%M}: {result.status.value.replace('_', ' ')}.")
        if result.detail:
            lines.append(result.detail)
        lines += [f"• {slot.describe()}" for slot in sorted(result.slots, key=lambda s: s.sort_key)]
    else:
        lines.append("No check has finished yet.")
    if not state.paused and state.next_check_at:
        minutes = max(0, round((state.next_check_at - now).total_seconds() / 60))
        lines.append(f"Next check in about {minutes} min (roughly every {state.poll_seconds} s).")
    return "\n".join(lines)


class CommandListener:
    def __init__(self, notifier: Telegram, chat_id: str, state: BotState,
                 clock=time.monotonic, sleep=time.sleep, now=datetime.now) -> None:
        self._notifier = notifier
        self._chat_id = str(chat_id)
        self.state = state
        self._clock, self._sleep, self._now = clock, sleep, now  # injectable so tests need no real waiting
        self._offset: int | None = None  # "I've read everything before this" (Telegram's bookmark)
        self._ready = False

    def start(self) -> None:
        """Ignore commands sent while the bot was off, and add the commands to Telegram's menu."""
        try:
            self._ensure_ready()
        except TelegramError as exc:
            log.warning("Couldn't set up Telegram commands yet (%s); will retry.", exc)

    def wait(self, seconds: float) -> None:
        """Sleep for up to `seconds`, answering commands meanwhile.

        Returns early when a command means "check now" (/resume).
        """
        deadline = self._clock() + seconds
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                return
            try:
                self._ensure_ready()
                updates = self._notifier.get_updates(
                    self._offset, wait_seconds=max(1, min(LONG_POLL_SECONDS, int(remaining)))
                )
            except TelegramError as exc:
                log.warning("Couldn't read Telegram commands: %s", exc)
                self._sleep(min(5, remaining))  # don't spin if Telegram is unreachable
                continue
            for update in updates:
                if self._handle(update):
                    return

    # ------------------------------------------------------------------ internals
    def _ensure_ready(self) -> None:
        if self._ready:
            return
        self._offset = self._notifier.skip_backlog()
        self._ready = True
        try:
            self._notifier.set_commands(MENU)
        except TelegramError as exc:
            log.warning("Couldn't update Telegram's command menu: %s", exc)

    def _handle(self, update: dict) -> bool:
        """Process one incoming message. Returns True if the main loop should check right now."""
        self._offset = update["update_id"] + 1  # mark as read
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return False

        sender = str((message.get("chat") or {}).get("id", ""))
        if sender != self._chat_id:
            log.warning("Ignored a command from an unknown chat (id %s).", sender)
            return False

        reply = handle_command(text, self.state, self._now())
        log.info("Command %s (paused=%s)", text.split()[0], self.state.paused)  # the command word only
        try:
            self._notifier.send(reply.text)
        except TelegramError as exc:
            log.warning("Couldn't send a command reply: %s", exc)
        return reply.wake
