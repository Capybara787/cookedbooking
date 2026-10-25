"""Decide *when* to message you.

The browser code answers "what does the page look like right now?".
This module answers "is that worth a Telegram message?", which is mostly about
NOT sending the same news over and over.

It does no I/O and takes the current time as an argument, so it's easy to test.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .models import CheckResult, Slot, Status

_PROBLEM_TITLE = {
    Status.LOGGED_OUT: "can't see the booking page",
    Status.BROWSER_DOWN: "can't reach its browser",
    Status.UNEXPECTED: "got a page it doesn't understand",
}
_PROBLEM_FIX = {
    Status.LOGGED_OUT: "Fix: log in again in a normal tab of the bot's browser (not the bot's own tab, which it reloads).",
    Status.BROWSER_DOWN: "Fix: run launch_browser.bat, log in, and leave that window open.",
    Status.UNEXPECTED: "Fix: run `python -m cdc_bot check --dump` and look at the saved page.",
}
_STATUS_TEXT = {
    Status.NO_SLOTS: "no slots right now",
    Status.SLOTS: "slots available",
    Status.LOGGED_OUT: "logged out",
    Status.UNEXPECTED: "page not understood",
    Status.BROWSER_DOWN: "browser unreachable",
}


@dataclass
class Decision:
    messages: list[str] = field(default_factory=list)
    cooldown_seconds: float = 0.0  # >0 means "don't poll again for this long"


class AlertPolicy:
    def __init__(
        self,
        booking_url: str,
        heartbeat_every: timedelta,
        cooldown: timedelta,
        started_at: datetime,
        bad_checks_before_alert: int = 2,
    ) -> None:
        self._booking_url = booking_url
        self._heartbeat_every = heartbeat_every
        self._cooldown = cooldown
        self._bad_needed = bad_checks_before_alert

        self._seen_slots: frozenset[Slot] = frozenset()  # slots we've already told you about
        self._bad_streak = 0              # consecutive checks where we couldn't tell
        self._problem_reported = False    # have we already sent the "something's wrong" message?
        self._last_heartbeat = started_at
        self._checks_since_heartbeat = 0
        self._latest: CheckResult | None = None

    def handle(self, result: CheckResult, now: datetime) -> Decision:
        decision = Decision()
        self._latest = result
        self._checks_since_heartbeat += 1

        if result.status.is_problem:
            # One hiccup (a slow page, a timeout) isn't worth waking you up for.
            self._bad_streak += 1
            if self._bad_streak >= self._bad_needed and not self._problem_reported:
                decision.messages.append(self._problem_message(result))
                self._problem_reported = True  # say it once, not every poll
        else:
            self._bad_streak = 0
            if self._problem_reported:
                decision.messages.append("✅ CDC bot is back to normal and checking for slots again.")
                self._problem_reported = False

            if result.status is Status.SLOTS:
                new = result.slots - self._seen_slots
                if new:  # only message about slots you haven't been told about yet
                    decision.messages.append(self._slots_message(result.slots, new))
                    decision.cooldown_seconds = self._cooldown.total_seconds()
                self._seen_slots = result.slots
            else:
                self._seen_slots = frozenset()  # slots gone: if one comes back, tell me again

        if self._heartbeat_every > timedelta(0) and now - self._last_heartbeat >= self._heartbeat_every:
            decision.messages.append(self._heartbeat_message(now))
            self._last_heartbeat = now
            self._checks_since_heartbeat = 0

        return decision

    def reset_seen_slots(self) -> None:
        """Forget which slots we've reported, so the next check reports everything available."""
        self._seen_slots = frozenset()

    def paused_reminder(self, now: datetime) -> list[str]:
        """While paused nothing is checked, so the normal heartbeat never runs. Remind instead."""
        if self._heartbeat_every > timedelta(0) and now - self._last_heartbeat >= self._heartbeat_every:
            self._last_heartbeat = now
            return ["⏸ CDC bot is paused and NOT checking for slots. Send /resume to start again."]
        return []

    # ------------------------------------------------------------------ message text
    def _slots_message(self, slots: frozenset[Slot], new: frozenset[Slot]) -> str:
        title = "🚗 CDC slot available!" if len(slots) == 1 else f"🚗 {len(slots)} CDC slots available!"
        lines = [title]
        for slot in sorted(slots, key=lambda s: s.sort_key):
            tag = " (new)" if len(new) < len(slots) and slot in new else ""
            lines.append(f"• {slot.describe()}{tag}")
        lines.append("")
        lines.append(self._booking_url)
        if self._cooldown.total_seconds() > 0:
            minutes = round(self._cooldown.total_seconds() / 60)
            lines.append(f"I'll pause checking for {minutes} min so I don't get in your way while you book.")
        return "\n".join(lines)

    def _problem_message(self, result: CheckResult) -> str:
        lines = [f"⚠️ CDC bot {_PROBLEM_TITLE[result.status]}, so it can't tell whether slots are available."]
        if result.detail:
            lines.append(result.detail)
        lines.append(_PROBLEM_FIX[result.status])
        lines.append("I'll message you when it's working again.")
        return "\n".join(lines)

    def _heartbeat_message(self, now: datetime) -> str:
        status = _STATUS_TEXT[self._latest.status] if self._latest else "unknown"
        return (
            f"💚 CDC bot is still running ({self._checks_since_heartbeat} checks since the last heartbeat).\n"
            f"Latest status at {now:%H:%M}: {status}."
        )
