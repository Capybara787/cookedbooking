"""Plain data types shared by the other modules.

Keeping them in one small file lets the parser, the browser code and the alert
logic agree on what a "check result" looks like without importing each other.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Status(str, Enum):
    NO_SLOTS = "no_slots"          # on the booking page, nothing available
    SLOTS = "slots"                # at least one slot is available
    LOGGED_OUT = "logged_out"      # we got bounced away from the booking page
    UNEXPECTED = "unexpected"      # on the page, but it doesn't look the way we expect
    BROWSER_DOWN = "browser_down"  # couldn't talk to Chrome/Edge at all

    @property
    def is_problem(self) -> bool:
        """True when we can't tell whether slots exist (as opposed to 'no' or 'yes')."""
        return self in (Status.LOGGED_OUT, Status.UNEXPECTED, Status.BROWSER_DOWN)


@dataclass(frozen=True)  # frozen => hashable, so Slots can live in sets
class Slot:
    date: str    # as shown on the page, e.g. "24/Oct/2026"
    day: str     # e.g. "SAT"
    number: int  # column 1-7 in the grid
    time: str    # e.g. "12:45-14:25" ("" if the header couldn't be read)
    team: str = ""  # the learner's team, or the selected other team

    def describe(self) -> str:
        when = f"{self.day} {self.date}".strip()
        slot = f"slot {self.number}" + (f" ({self.time})" if self.time else "")
        team = f"{self.team} - " if self.team else ""
        return f"{when} - {team}{slot}"

    @property
    def sort_key(self) -> tuple[datetime, int]:
        try:
            return (datetime.strptime(self.date, "%d/%b/%Y"), self.number)
        except ValueError:
            return (datetime.max, self.number)


@dataclass(frozen=True)
class CheckResult:
    status: Status
    slots: frozenset[Slot] = frozenset()
    detail: str = ""  # human-readable extra info, mostly for problems
