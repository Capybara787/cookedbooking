import pytest
import requests

from cdc_bot.notifier import Telegram, TelegramError

TOKEN = "123456:SECRET-TOKEN"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


def test_network_error_never_leaks_the_token(monkeypatch):
    def boom(url, **kwargs):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(requests, "post", boom)
    with pytest.raises(TelegramError) as excinfo:
        Telegram(TOKEN, "42").send("hi")
    assert "SECRET-TOKEN" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None and excinfo.value.__suppress_context__


def test_telegram_refusal_becomes_a_clear_error(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse({"ok": False, "description": "Unauthorized"}, 401))
    with pytest.raises(TelegramError, match="Unauthorized"):
        Telegram(TOKEN, "42").send("hi")


def test_send_posts_to_the_right_place_with_the_right_body(monkeypatch):
    seen = {}

    def fake_post(url, json, timeout):
        seen.update(url=url, json=json, timeout=timeout)
        return FakeResponse({"ok": True, "result": {}})

    monkeypatch.setattr(requests, "post", fake_post)
    Telegram(TOKEN, "42").send("hello")
    assert seen["url"] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert seen["json"]["chat_id"] == "42" and seen["json"]["text"] == "hello"
    assert seen["timeout"] > 0


def test_find_chats_reads_getupdates(monkeypatch):
    payload = {"ok": True, "result": [
        {"update_id": 1, "message": {"chat": {"id": 987654, "first_name": "Sam"}, "text": "hi"}},
        {"update_id": 2, "message": {"chat": {"id": 987654, "first_name": "Sam"}, "text": "again"}},
    ]}
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(payload))
    assert Telegram(TOKEN).find_chats() == [("987654", "Sam")]


def test_get_updates_long_polls_with_a_longer_http_timeout(monkeypatch):
    seen = {}

    def fake_post(url, json, timeout):
        seen.update(url=url, json=json, timeout=timeout)
        return FakeResponse({"ok": True, "result": [{"update_id": 5}]})

    monkeypatch.setattr(requests, "post", fake_post)
    assert Telegram(TOKEN).get_updates(offset=7, wait_seconds=25) == [{"update_id": 5}]
    assert seen["url"].endswith("/getUpdates")
    assert seen["json"]["offset"] == 7 and seen["json"]["timeout"] == 25
    assert seen["timeout"] > 25  # otherwise requests would give up before Telegram answers


def test_skip_backlog_returns_the_next_offset_or_none(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse({"ok": True, "result": [{"update_id": 41}]}))
    assert Telegram(TOKEN).skip_backlog() == 42
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse({"ok": True, "result": []}))
    assert Telegram(TOKEN).skip_backlog() is None


def test_set_commands_posts_the_menu(monkeypatch):
    seen = {}
    monkeypatch.setattr(requests, "post", lambda url, json, timeout: seen.update(url=url, json=json) or FakeResponse({"ok": True}))
    Telegram(TOKEN).set_commands([("pause", "Stop")])
    assert seen["url"].endswith("/setMyCommands")
    assert seen["json"] == {"commands": [{"command": "pause", "description": "Stop"}]}
