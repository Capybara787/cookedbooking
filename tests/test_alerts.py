from datetime import datetime, timedelta

from cdc_bot.alerts import AlertPolicy
from cdc_bot.models import CheckResult, Slot, Status

T0 = datetime(2026, 10, 19, 12, 0)
A = Slot("24/Oct/2026", "SAT", 3, "12:45-14:25")
B = Slot("26/Oct/2026", "MON", 5, "16:25-18:05")
NONE = CheckResult(Status.NO_SLOTS)
OUT = CheckResult(Status.LOGGED_OUT, detail="Landed on /login")


def slots(*found):
    return CheckResult(Status.SLOTS, frozenset(found))


def policy(heartbeat_hours=24, cooldown_min=10):
    return AlertPolicy(
        "https://example.test/book",
        heartbeat_every=timedelta(hours=heartbeat_hours),
        cooldown=timedelta(minutes=cooldown_min),
        started_at=T0,
    )


def test_no_slots_is_silent():
    assert policy().handle(NONE, T0).messages == []


def test_new_slot_triggers_one_alert_and_a_cooldown():
    p = policy()
    d = p.handle(slots(A), T0)
    assert len(d.messages) == 1
    assert "24/Oct/2026" in d.messages[0] and "https://example.test/book" in d.messages[0]
    assert d.cooldown_seconds == 600


def test_same_slot_is_not_repeated():
    p = policy()
    p.handle(slots(A), T0)
    d = p.handle(slots(A), T0 + timedelta(minutes=15))
    assert d.messages == [] and d.cooldown_seconds == 0


def test_additional_slot_alerts_again_and_marks_the_new_one():
    p = policy()
    p.handle(slots(A), T0)
    d = p.handle(slots(A, B), T0 + timedelta(minutes=15))
    assert len(d.messages) == 1
    assert d.messages[0].count("(new)") == 1


def test_slot_that_disappears_and_returns_alerts_again():
    p = policy()
    p.handle(slots(A), T0)
    p.handle(NONE, T0 + timedelta(minutes=15))
    assert len(p.handle(slots(A), T0 + timedelta(minutes=30)).messages) == 1


def test_one_bad_check_is_ignored_but_two_alert_once():
    p = policy()
    assert p.handle(OUT, T0).messages == []                       # first hiccup: stay quiet
    d = p.handle(OUT, T0 + timedelta(minutes=3))
    assert len(d.messages) == 1 and "log in again" in d.messages[0]
    assert p.handle(OUT, T0 + timedelta(minutes=6)).messages == []  # no repeat nagging


def test_recovery_message_after_a_reported_problem():
    p = policy()
    p.handle(OUT, T0)
    p.handle(OUT, T0 + timedelta(minutes=3))
    d = p.handle(NONE, T0 + timedelta(minutes=6))
    assert len(d.messages) == 1 and "back to normal" in d.messages[0]


def test_recovery_straight_into_slots_sends_both_messages_in_order():
    p = policy()
    p.handle(OUT, T0)
    p.handle(OUT, T0 + timedelta(minutes=3))
    d = p.handle(slots(A), T0 + timedelta(minutes=6))
    assert "back to normal" in d.messages[0] and "slot" in d.messages[1]


def test_heartbeat_after_24_hours_only():
    p = policy()
    assert p.handle(NONE, T0 + timedelta(hours=23)).messages == []
    d = p.handle(NONE, T0 + timedelta(hours=24))
    assert len(d.messages) == 1 and "still running" in d.messages[0]
    assert p.handle(NONE, T0 + timedelta(hours=25)).messages == []


def test_heartbeat_can_be_disabled():
    assert policy(heartbeat_hours=0).handle(NONE, T0 + timedelta(days=3)).messages == []


def test_reset_makes_existing_slots_alert_again():
    p = policy()
    p.handle(slots(A), T0)
    assert p.handle(slots(A), T0 + timedelta(minutes=5)).messages == []
    p.reset_seen_slots()
    assert len(p.handle(slots(A), T0 + timedelta(minutes=10)).messages) == 1


def test_paused_reminder_only_when_due_and_can_be_disabled():
    p = policy()
    assert p.paused_reminder(T0 + timedelta(hours=23)) == []
    assert "paused" in p.paused_reminder(T0 + timedelta(hours=24))[0]
    assert p.paused_reminder(T0 + timedelta(hours=25)) == []
    assert policy(heartbeat_hours=0).paused_reminder(T0 + timedelta(days=3)) == []
