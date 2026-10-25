# cdc_bot

Watches the CDC booking page and sends you a Telegram message when a lesson slot appears.

**Notify-only.** It never reserves or books anything. You log in and book by hand.

## How it works

1. `launch_browser.bat` opens Chrome or Edge with a debugging port (reachable from this PC only) and its own profile folder.
2. You log in yourself, including the reCAPTCHA, and leave that window open.
3. `python -m cdc_bot run` attaches to that browser, opens a tab of its own, re-picks the course in the dropdown, reads the server's reply (your own team's slots, then each other team listed as having slots), and repeats every few minutes.
4. You get a Telegram message when something *changes* (new slot, logged out, back to normal), not on every check.

Firefox won't work for this. The attach mechanism (CDP) is Chromium-only, so use Chrome or Edge.

## Setup (Windows)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

You do **not** need `playwright install`; the bot drives the Chrome/Edge you already have.

**WSL / Linux instead:** use `source .venv/bin/activate` and `cp .env.example .env`, and start the browser with `./launch_browser.sh` (run `chmod +x launch_browser.sh` once). It expects the `chromium` command; edit the script if yours is named differently. Run the bot from the same WSL session as the browser.

**Telegram**

1. In Telegram, message `@BotFather`, send `/newbot`, and follow the prompts. It gives you a token.
2. Put the token in `.env` as `TELEGRAM_BOT_TOKEN=...`. It stays on your PC; don't paste it into chats.
3. Open your new bot in Telegram and send it any message (bots can't message you first).
4. Run `python -m cdc_bot chat-id`, then put the number it prints in `.env` as `TELEGRAM_CHAT_ID=...`.
5. Run `python -m cdc_bot test-telegram`. You should get a message.

## Daily use

1. Double-click `launch_browser.bat`, log in to the CDC portal, leave the window open.
2. `python -m cdc_bot check` runs one check and prints what the bot sees.
3. `python -m cdc_bot run` starts the real loop. Ctrl+C stops it.

If the session expires, you'll get a "log in again" message. Log in again in that same window; the bot notices by itself.

For 24/7 running, set Windows power settings to never sleep while plugged in.

## Controlling the bot from Telegram

Once `run` is going, message your bot:

| Send | Does |
|---|---|
| `/pause` (or `/stop`) | Stops checking for slots. You get a reminder every `HEARTBEAT_HOURS` while paused, and the bot keeps your login alive (see below). |
| `/resume` (or `/start`) | Starts checking again, immediately, and tells you about any slots available right now. |
| `/status` | Running or paused, the last check's result (with any slots), and when the next check is due. |
| `/help` | Lists the commands. |

- Only the chat in `TELEGRAM_CHAT_ID` is obeyed. Anyone can message a bot, so everything from other chats is ignored.
- Commands sent while the bot was switched off are ignored when it starts.
- The bot answers while it waits between checks. A command sent in the few seconds a check takes is answered right after it.
- Pausing is not remembered across restarts: starting `run` always starts checking.
- **Keep-alive while paused:** every `KEEPALIVE_MINUTES` (default 5, minimum 2, `0` = off) the bot loads the booking page, like a person leaving it open, so your login doesn't time out. It selects nothing and reads no slots. `/status` shows the latest result. If the keep-alive finds you've been logged out anyway, you get one message telling you to log in again before `/resume`. Whether a plain page load resets CDC's timer is an assumption; the `Keep-alive:` lines in `cdc_bot.log` show if it works. A fixed maximum session length can't be avoided this way.
- Only one copy of the bot should run per Telegram bot, otherwise Telegram reports a "Conflict" error about `getUpdates`.

## Commands (on your PC)

| Command | What it does |
|---|---|
| `chat-id` | Finds your Telegram chat id |
| `test-telegram` | Sends a test message |
| `check` | One check, printed to the screen |
| `check --dump` | Same, and saves a redacted copy of the page to `debug/` |
| `check --from-file X.html` | Runs the parser on a saved page (no browser) |
| `run` | The real loop |

## Files

| File | Job |
|---|---|
| `cdc_bot/browser.py` | Attaches to your browser and refreshes the slot list |
| `cdc_bot/parser.py` | HTML in, result out (pure functions, easy to test) |
| `cdc_bot/alerts.py` | Decides when a message is worth sending |
| `cdc_bot/commands.py` | Telegram commands: pause, resume, status |
| `cdc_bot/notifier.py` | Telegram, via plain HTTP requests |
| `cdc_bot/config.py` | Reads `.env` |
| `cdc_bot/models.py` | Shared data types |
| `cdc_bot/__main__.py` | Command line and the main loop |
| `tests/` | Run with `pip install pytest` then `python -m pytest` |

## Known gaps (not yet verified against the real site)

- **The "slot available" detection is inferred.** It assumes available cells use the `images1.gif` car icon (from the page's legend) inside the grid panel. There were no slots when the page was captured, so this hasn't been seen on a real populated grid. If the page looks different, the bot reports "page not understood" instead of guessing. `tests/fixtures/slots_found.html` is synthetic.
- **Going straight to the booking URL is untested.** If the site insists on being reached through its menus, the bot will say it can't see the booking page.
- **Other teams' grids are unverified.** They're read the same way as your own team's, but no other team's slot grid has been seen yet. Alerts for them are labelled with the team and a premium-fee note.
- **Only the first date window is checked** for each team. The "Next 5 Days" arrows aren't followed yet.

## Safety notes

- `.env` holds your bot token. It's in `.gitignore`; keep it out of git, chats and screenshots.
- The debugging port gives full control of that browser to anything on this PC that connects to it. It only listens locally, and the profile holds nothing but your CDC login. Don't forward or expose port 9222.
- Polling is deliberately gentle (default about 3 minutes, never faster than 60 seconds). Check the CDC portal's terms of use for automated access.
- Pages saved with `--dump` have hidden fields and balances blanked, but may still contain your name. Look before sharing.
