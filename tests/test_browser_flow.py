"""Tests the browser logic with a fake page (no real browser, no network)."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import TimeoutError as PlaywrightTimeout  # noqa: E402

from cdc_bot.browser import OTHER_TEAM_SELECTOR, COURSE_SELECTOR, BrowserSession  # noqa: E402
from cdc_bot.config import Config  # noqa: E402
from cdc_bot.models import Slot, Status  # noqa: E402
from replies import SLOT_GRID, encode, make_reply, other_team_reply, own_team_reply  # noqa: E402

CFG = Config("tok", "42", "http://127.0.0.1:9222", "https://example.test/book", "ONETEAM", 180, 10.0, 24.0)
PADDED_TEAM = "1T TEAM 17          "


class FakeResponse:
    def __init__(self, text, status=200):
        self._text, self.status = text, status

    def text(self):
        if self._text is None:
            raise PlaywrightError("no body for a redirect")
        return self._text


class FakePage:
    url = "https://example.test/book"

    def __init__(self, *replies, selector_missing=False):
        self._replies = list(replies)
        self.selected = []
        self.gotos = 0
        self._selector_missing = selector_missing

    def is_closed(self): return False

    def goto(self, *a, **k):
        self.gotos += 1

    def wait_for_selector(self, *a, **k):
        if self._selector_missing:
            raise PlaywrightTimeout("no course dropdown")
    def wait_for_function(self, *a, **k): pass
    def content(self): return "<html></html>"
    def title(self): return "fake"
    def eval_on_selector(self, selector, js, arg): return "ONETEAM             "

    def select_option(self, selector, value=None, timeout=None):
        self.selected.append((selector, value))

    @contextmanager
    def expect_response(self, predicate, timeout=None):
        holder = SimpleNamespace(value=None)
        yield holder
        holder.value = self._replies.pop(0)


def check(*replies):
    session = BrowserSession(CFG)
    session._page = FakePage(*replies)
    return session._check_booking_page(), session._page


def test_no_slots_anywhere():
    result, page = check(FakeResponse(own_team_reply()))
    assert result.status is Status.NO_SLOTS
    assert page.selected == [(COURSE_SELECTOR, "ONETEAM             ")]


def test_other_team_slot_is_found_and_the_exact_value_is_selected():
    result, page = check(
        FakeResponse(own_team_reply(other_teams=(PADDED_TEAM,))),
        FakeResponse(other_team_reply(SLOT_GRID)),
    )
    assert result.status is Status.SLOTS
    assert result.slots == {Slot("24/Oct/2026", "SAT", 1, "08:30-10:10", "1T TEAM 17")}
    assert page.selected[1] == (OTHER_TEAM_SELECTOR, PADDED_TEAM)  # trailing spaces preserved


def test_own_and_other_team_slots_are_combined():
    result, _ = check(
        FakeResponse(own_team_reply(other_teams=(PADDED_TEAM,), grid=SLOT_GRID)),
        FakeResponse(other_team_reply(SLOT_GRID)),
    )
    assert {s.team for s in result.slots} == {"", "1T TEAM 17"}


def test_listed_team_whose_slot_vanished_is_not_an_error():
    result, _ = check(
        FakeResponse(own_team_reply(other_teams=(PADDED_TEAM,))),
        FakeResponse(other_team_reply("<div class='grid-scroll-container'></div>")),
    )
    assert result.status is Status.NO_SLOTS


def test_unfamiliar_icon_on_another_teams_grid_is_flagged():
    odd_grid = SLOT_GRID.replace("images1.gif", "premium.gif")
    result, _ = check(
        FakeResponse(own_team_reply(other_teams=(PADDED_TEAM,))),
        FakeResponse(other_team_reply(odd_grid)),
    )
    assert result.status is Status.UNEXPECTED and "premium.gif" in result.detail


def test_redirect_means_logged_out():
    result, _ = check(FakeResponse(None, status=302))
    assert result.status is Status.LOGGED_OUT


def test_server_error_record_is_reported():
    result, _ = check(FakeResponse(make_reply({}, extra=(encode("error", "500", "Boom"),))))
    assert result.status is Status.UNEXPECTED and "Boom" in result.detail


def test_unreadable_reply_is_reported():
    result, _ = check(FakeResponse("<html>Just a moment...</html>"))
    assert result.status is Status.UNEXPECTED and "Unreadable" in result.detail


def keep_alive(selector_missing=False):
    session = BrowserSession(CFG)
    session._page = FakePage(selector_missing=selector_missing)
    return session.keep_alive(), session._page


def test_keep_alive_loads_the_page_and_touches_nothing():
    problem, page = keep_alive()
    assert problem is None
    assert page.gotos == 1 and page.selected == []  # no dropdown changes, so no slot reading


def test_keep_alive_reports_being_logged_out():
    problem, _ = keep_alive(selector_missing=True)
    assert "dropdown" in problem and "Landed on" in problem
