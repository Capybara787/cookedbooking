"""Exercises the `run` loop with a fake browser, fake Telegram and fake command listener."""
import argparse

import pytest

pytest.importorskip("playwright")  # cmd_run imports the browser module

import cdc_bot.__main__ as cli
import cdc_bot.browser as browser_module
from cdc_bot.config import Config
from cdc_bot.models import CheckResult, Slot, Status
from cdc_bot.notifier import TelegramError

A = Slot("24/Oct/2026", "SAT", 3, "12:45-14:25")
CFG = Config("tok", "42", "http://127.0.0.1:9222", "https://example.test/book", "ONETEAM", 180, 10.0, 24.0)


class FakeTelegram:
    sent: list[str] = []
    fail_next = 0

    def __init__(self, token, chat_id=None):
        pass

    def send(self, text):
        if FakeTelegram.fail_next:
            FakeTelegram.fail_next -= 1
            raise TelegramError("network down")
        FakeTelegram.sent.append(text)


class FakeListener:
    """Stands in for the Telegram command listener. Records each wait instead of sleeping."""
    waits: list[float] = []
    script: list = []  # actions run after each wait, e.g. "the user sends /pause"

    def __init__(self, notifier, chat_id, state):
        self.state = state

    def start(self):
        pass

    def wait(self, seconds):
        FakeListener.waits.append(seconds)
        if FakeListener.script:
            FakeListener.script.pop(0)(self.state)


def fake_session_class(script, calls):
    class FakeSession:
        def __init__(self, cfg):
            self._results = iter(script)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def check(self):
            calls.append(1)
            try:
                return next(self._results)
            except StopIteration:
                raise KeyboardInterrupt  # ends the loop, like pressing Ctrl+C

    return FakeSession


@pytest.fixture
def env(monkeypatch):
    FakeTelegram.sent, FakeTelegram.fail_next = [], 0
    FakeListener.waits, FakeListener.script = [], []
    monkeypatch.setattr(cli, "Telegram", FakeTelegram)
    monkeypatch.setattr(cli, "CommandListener", FakeListener)


def run(monkeypatch, script):
    calls = []
    monkeypatch.setattr(browser_module, "BrowserSession", fake_session_class(script, calls))
    assert cli.cmd_run(CFG, argparse.Namespace()) == 0
    return calls


def test_slot_alert_then_cooldown_then_normal_polling(monkeypatch, env):
    none, found = CheckResult(Status.NO_SLOTS), CheckResult(Status.SLOTS, frozenset({A}))
    run(monkeypatch, [none, found, found, none])

    waits = FakeListener.waits
    assert FakeTelegram.sent[0].startswith("🟢") and "/pause" in FakeTelegram.sent[0]
    assert sum("slot available" in m for m in FakeTelegram.sent) == 1  # alerted once, not twice
    assert waits[0] == pytest.approx(180, rel=0.21)                    # normal jittered delay
    assert waits[1] == 600                                             # cooldown right after the alert
    assert waits[2] == pytest.approx(180, rel=0.21)                    # back to normal


def test_alert_survives_a_telegram_outage(monkeypatch, env):
    found = CheckResult(Status.SLOTS, frozenset({A}))
    FakeTelegram.fail_next = 1   # the very first send (the startup message) fails
    run(monkeypatch, [found, CheckResult(Status.NO_SLOTS)])
    # Both messages stayed queued after the failure, then went out, in order, on the next round.
    assert len(FakeTelegram.sent) == 2
    assert FakeTelegram.sent[0].startswith("🟢")
    assert "slot available" in FakeTelegram.sent[1]


def test_pause_stops_checking_and_resume_reports_current_slots_again(monkeypatch, env):
    found = CheckResult(Status.SLOTS, frozenset({A}))
    FakeListener.script = [
        lambda s: setattr(s, "paused", True),    # after the first check, the user sends /pause
        lambda s: None,                          # still paused through a second wait
        lambda s: setattr(s, "paused", False),   # the user sends /resume
    ]
    calls = run(monkeypatch, [found, found])

    assert FakeListener.waits == [600, 60, 60, 600]   # 60 s waits are the paused ones
    assert len(calls) == 3                            # 2 real checks (+1 that ends the loop); none while paused
    assert sum("slot available" in m for m in FakeTelegram.sent) == 2  # resuming re-reports the slot
