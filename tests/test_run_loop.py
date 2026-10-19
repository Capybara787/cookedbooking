"""Exercises the `run` loop with a fake browser and a fake Telegram (no network, no sleeping)."""
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


def fake_session_class(script):
    class FakeSession:
        def __init__(self, cfg):
            self._results = iter(script)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def check(self):
            try:
                return next(self._results)
            except StopIteration:
                raise KeyboardInterrupt  # ends the loop, like pressing Ctrl+C

    return FakeSession


@pytest.fixture
def delays(monkeypatch):
    recorded = []
    FakeTelegram.sent, FakeTelegram.fail_next = [], 0
    monkeypatch.setattr(cli, "Telegram", FakeTelegram)
    monkeypatch.setattr(cli.time, "sleep", recorded.append)
    return recorded


def run(monkeypatch, script):
    monkeypatch.setattr(browser_module, "BrowserSession", fake_session_class(script))
    return cli.cmd_run(CFG, argparse.Namespace())


def test_slot_alert_then_cooldown_then_normal_polling(monkeypatch, delays):
    none, found = CheckResult(Status.NO_SLOTS), CheckResult(Status.SLOTS, frozenset({A}))
    assert run(monkeypatch, [none, found, found, none]) == 0

    assert FakeTelegram.sent[0].startswith("🟢")                        # startup message
    assert sum("slot available" in m for m in FakeTelegram.sent) == 1  # alerted once, not twice
    assert delays[0] == pytest.approx(180, rel=0.21)                   # normal jittered delay
    assert delays[1] == 600                                            # cooldown right after the alert
    assert delays[2] == pytest.approx(180, rel=0.21)                   # back to normal


def test_alert_survives_a_telegram_outage(monkeypatch, delays):
    found = CheckResult(Status.SLOTS, frozenset({A}))
    FakeTelegram.fail_next = 1   # the very first send (the startup message) fails
    run(monkeypatch, [found, CheckResult(Status.NO_SLOTS)])
    # Both messages stayed queued after the failure, then went out, in order, on the next round.
    assert len(FakeTelegram.sent) == 2
    assert FakeTelegram.sent[0].startswith("🟢")
    assert "slot available" in FakeTelegram.sent[1]
