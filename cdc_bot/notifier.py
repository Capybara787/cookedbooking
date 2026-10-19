"""Send Telegram messages with plain HTTP requests.

The Telegram Bot API is just HTTPS + JSON:

    POST https://api.telegram.org/bot<TOKEN>/<method>

so there's no need for a big library, and it's a nice small API to learn from.
"""
from __future__ import annotations

import requests

API_ROOT = "https://api.telegram.org"
TIMEOUT_SECONDS = 15
MAX_LEN = 4000  # Telegram's hard limit is 4096 characters per message


class TelegramError(Exception):
    """Any failure talking to Telegram. The message never contains the bot token."""


class Telegram:
    def __init__(self, token: str, chat_id: str | None = None) -> None:
        self._token = token
        self._chat_id = chat_id

    def send(self, text: str) -> None:
        if not self._chat_id:
            raise TelegramError("No chat id set.")
        self._call(
            "sendMessage",
            {
                "chat_id": self._chat_id,
                "text": text[:MAX_LEN],
                "link_preview_options": {"is_disabled": True},
            },
        )

    def find_chats(self) -> list[tuple[str, str]]:
        """Return (chat_id, name) for everyone who recently messaged the bot.

        A bot can't start a conversation, so you have to message it first;
        Telegram then keeps that message around for us to read via getUpdates.
        """
        data = self._call("getUpdates", {"timeout": 0})
        chats: dict[str, str] = {}
        for update in data.get("result", []):
            chat = (update.get("message") or {}).get("chat")
            if chat:
                name = chat.get("title") or chat.get("first_name") or chat.get("username") or "?"
                chats[str(chat["id"])] = name
        return list(chats.items())

    # ------------------------------------------------------------------ internals
    def _call(self, method: str, payload: dict) -> dict:
        # The token is part of the URL path, which is why it must never be logged.
        url = f"{API_ROOT}/bot{self._token}/{method}"
        try:
            response = requests.post(url, json=payload, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            # requests' error text includes the full URL (and so the token).
            # `from None` also stops Python printing the original exception.
            raise TelegramError(self._scrub(f"network problem: {exc}")) from None

        try:
            data = response.json()
        except ValueError:
            raise TelegramError(f"Telegram sent a non-JSON reply (HTTP {response.status_code}).") from None

        if not data.get("ok"):
            reason = self._scrub(str(data.get("description", "no description")))
            raise TelegramError(f"Telegram refused the request (HTTP {response.status_code}): {reason}")
        return data

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, "<token>") if self._token else text
