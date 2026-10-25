from datetime import datetime, timedelta

from cdc_bot.commands import BotState, CommandListener, handle_command
from cdc_bot.models import CheckResult, Slot, Status
from cdc_bot.notifier import TelegramError

NOW = datetime(2026, 10, 21, 14, 30)
A = Slot("24/Oct/2026", "SAT", 3, "12:45-14:25")


def fresh_state(**kw):
    return BotState(poll_seconds=180, **kw)


# ---- handle_command: pure logic ------------------------------------------------
def test_pause_then_resume():
    s = fresh_state()
    r = handle_command("/pause", s, NOW)
    assert s.paused and not r.wake
    r = handle_command("/resume", s, NOW)
    assert not s.paused and r.wake  # resume means "check right now"


def test_aliases_and_bot_name_suffix_and_case():
    s = fresh_state()
    handle_command("/STOP", s, NOW)
    assert s.paused
    handle_command("/start@CdcSlotBot", s, NOW)
    assert not s.paused


def test_repeating_a_command_is_harmless():
    s = fresh_state()
    assert "Already running" in handle_command("/resume", s, NOW).text
    handle_command("/pause", s, NOW)
    r = handle_command("/pause", s, NOW)
    assert "Already paused" in r.text and not r.wake and s.paused


def test_status_running_lists_slots_and_next_check():
    s = fresh_state(
        last_result=CheckResult(Status.SLOTS, frozenset({A})),
        last_check_at=NOW,
        next_check_at=NOW + timedelta(minutes=3),
    )
    text = handle_command("/status", s, NOW).text
    assert "Running" in text and "14:30" in text and "SAT 24/Oct/2026" in text and "3 min" in text


def test_status_when_paused_and_before_any_check():
    s = fresh_state(paused=True)
    text = handle_command("/status", s, NOW).text
    assert "Paused" in text and "No check has finished yet" in text and "Next check" not in text


def test_status_while_paused_shows_the_keepalive():
    text = handle_command("/status", fresh_state(paused=True, keepalive="ok at 14:25"), NOW).text
    assert "Session keep-alive: ok at 14:25" in text


def test_unknown_command_gets_help():
    assert "/pause" in handle_command("/dance", fresh_state(), NOW).text
    assert "/pause" in handle_command("/help", fresh_state(), NOW).text


# ---- CommandListener: fake Telegram, fake clock ----------------------------------
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class FakeNotifier:
    def __init__(self, clock, batches=()):
        self.clock, self.batches = clock, list(batches)
        self.sent, self.offsets, self.menu, self.skipped = [], [], None, False

    def skip_backlog(self):
        self.skipped = True
        return 100

    def set_commands(self, commands):
        self.menu = commands

    def get_updates(self, offset, wait_seconds):
        self.offsets.append(offset)
        self.clock.advance(wait_seconds)  # a long poll that found nothing takes the full wait
        item = self.batches.pop(0) if self.batches else []
        if isinstance(item, Exception):
            raise item
        return item

    def send(self, text):
        self.sent.append(text)


def msg(update_id, text, chat=42):
    return {"update_id": update_id, "message": {"text": text, "chat": {"id": chat}}}


def listener(batches, state=None):
    clock = Clock()
    notifier = FakeNotifier(clock, batches)
    state = state or fresh_state()
    return CommandListener(notifier, "42", state, clock=clock, sleep=clock.advance, now=lambda: NOW), notifier, state, clock


def test_owner_command_is_obeyed_answered_and_marked_read():
    lst, notifier, state, _ = listener([[msg(101, "/pause")]])
    lst.wait(30)
    assert state.paused
    assert len(notifier.sent) == 1 and "Paused" in notifier.sent[0]
    assert notifier.offsets[0] == 100 and notifier.offsets[1] == 102  # 101 acknowledged


def test_backlog_is_skipped_and_menu_registered_before_listening():
    lst, notifier, _, _ = listener([])
    lst.wait(1)
    assert notifier.skipped and notifier.menu and notifier.offsets[0] == 100


def test_other_chats_and_plain_text_are_ignored():
    lst, notifier, state, _ = listener([[msg(101, "/pause", chat=999), msg(102, "hello"), msg(103, "/stop", chat=7)]])
    lst.wait(30)
    assert not state.paused and notifier.sent == []


def test_resume_ends_the_wait_early():
    lst, notifier, state, clock = listener([[msg(101, "/resume")]], state=fresh_state(paused=True))
    lst.wait(300)
    assert not state.paused
    assert clock.t < 300 and len(notifier.offsets) == 1  # didn't sit out the whole 300 s


def test_telegram_outage_does_not_crash_the_wait():
    lst, _, _, clock = listener([TelegramError("down"), TelegramError("down")])
    lst.wait(12)
    assert clock.t >= 12


def test_unreadable_command_reply_does_not_crash():
    lst, notifier, state, _ = listener([[msg(101, "/pause")]])
    notifier.send = lambda text: (_ for _ in ()).throw(TelegramError("send failed"))
    lst.wait(10)
    assert state.paused  # the command still took effect
