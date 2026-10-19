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
