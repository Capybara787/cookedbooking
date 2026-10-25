"""Drive the browser that YOU logged in with.

You start Chrome/Edge with launch_browser.bat, solve the login (including the
reCAPTCHA) yourself, and leave the window open. This module then connects to
that browser over the Chrome DevTools Protocol (CDP) and, in a tab of its own,
re-picks the course from the dropdown to refresh the slot list, like you would.

Why attach instead of logging in from the script? The login is protected by
reCAPTCHA on purpose. We respect that: a human logs in, the script only watches.
The browser also takes care of the ASP.NET __VIEWSTATE bookkeeping for us.

We read the server's reply to each dropdown change, not the rendered page. The
reply is the source of truth, and it doesn't depend on when the page's JavaScript
gets round to showing it (reading the page too early gave false "unexpected" results).
"""
from __future__ import annotations

import logging
from dataclasses import replace
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from .config import Config
from .models import CheckResult, Status
from .parser import (
    COURSE_SELECT_ID,
    OTHER_TEAM_SELECT_ID,
    Record,
    classify_page,
    delta_to_html,
    other_team_options,
    parse_delta,
    scan_grid,
)

log = logging.getLogger("cdc_bot")

COURSE_SELECTOR = f"#{COURSE_SELECT_ID}"
OTHER_TEAM_SELECTOR = f"#{OTHER_TEAM_SELECT_ID}"

# The <option> values are padded with trailing spaces ("ONETEAM             "),
# so find the option whose trimmed value matches and use its exact value.
_FIND_OPTION_JS = """
(select, wanted) => {
    const option = Array.from(select.options).find(o => o.value.trim() === wanted);
    return option ? option.value : null;
}
"""

# ASP.NET's own JavaScript knows whether a background update is still running.
# Asking it is more reliable than sleeping and hoping.
_WEBFORMS_IDLE_JS = """
() => {
    try { return !Sys.WebForms.PageRequestManager.getInstance().get_isInAsyncPostBack(); }
    catch (e) { return true; }   // no ASP.NET Ajax object on this page: nothing to wait for
}
"""


class BrowserSession:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._booking_path = urlparse(cfg.booking_url).path.lower()
        self._pw = None
        self._browser = None
        self._page = None

    def __enter__(self) -> "BrowserSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ------------------------------------------------------------------ public API
    def check(self) -> CheckResult:
        """Refresh the slot list and report what we see. Never raises for browser trouble."""
        try:
            self._ensure_connected()
            return self._check_booking_page()
        except PlaywrightTimeout as exc:  # must come before PlaywrightError (it's a subclass)
            self.close()
            return CheckResult(Status.UNEXPECTED, detail=f"Timed out waiting for the page ({_short(exc)}).")
        except PlaywrightError as exc:
            self.close()  # drop the connection; the next check reconnects from scratch
            return CheckResult(Status.BROWSER_DOWN, detail=f"Lost contact with the browser ({_short(exc)}).")

    def keep_alive(self) -> str | None:
        """Load the booking page and touch nothing, so the login doesn't time out.

        Like a person leaving the page open and refreshing it now and then. Returns
        None if we're still logged in, otherwise a short description of what we saw.
        """
        try:
            self._ensure_connected()
            self._page.goto(self._cfg.booking_url, wait_until="domcontentloaded", timeout=30_000)
            try:
                self._page.wait_for_selector(COURSE_SELECTOR, timeout=10_000)
            except PlaywrightTimeout:
                return self._with_location(classify_page(self._page.content())).detail or None
            return None
        except PlaywrightTimeout as exc:
            self.close()
            return f"Timed out loading the page ({_short(exc)})."
        except PlaywrightError as exc:
            self.close()
            return f"Lost contact with the browser ({_short(exc)})."

    def current_html(self) -> str:
        """The page as it is right now (used by `check --dump`)."""
        try:
            return self._page.content() if self._page else ""
        except PlaywrightError:
            return ""

    def close(self) -> None:
        """Disconnect. This leaves your Chrome window open; only our own tab is closed."""
        for action in (
            lambda: self._page.close() if self._page and not self._page.is_closed() else None,
            lambda: self._browser.close() if self._browser else None,
            lambda: self._pw.stop() if self._pw else None,
        ):
            try:
                action()
            except Exception:  # noqa: BLE001 - cleaning up after a failure; nothing more to do
                pass
        self._page = self._browser = self._pw = None

    # ------------------------------------------------------------------ internals
    def _ensure_connected(self) -> None:
        if self._page is not None and not self._page.is_closed():
            return
        self.close()
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(self._cfg.cdp_url)
        # contexts[0] is the browser's normal profile, so it has your login cookies.
        # We use a tab of our own so we never poke the tab you're working in.
        self._page = self._browser.contexts[0].new_page()
        self._page.on("dialog", self._on_dialog)  # log pop-up errors the page would otherwise hide

    def _check_booking_page(self) -> CheckResult:
        page = self._page

        # A fresh page load every time: re-selecting the already-selected course
        # doesn't make ASP.NET rebuild the slot list.
        page.goto(self._cfg.booking_url, wait_until="domcontentloaded", timeout=30_000)
        try:
            page.wait_for_selector(COURSE_SELECTOR, timeout=10_000)
        except PlaywrightTimeout:
            return self._with_location(classify_page(page.content()))  # bounced to login, etc.

        option_value = page.eval_on_selector(COURSE_SELECTOR, _FIND_OPTION_JS, self._cfg.course_value)
        if option_value is None:
            return CheckResult(Status.UNEXPECTED, detail=f"Course {self._cfg.course_value!r} isn't in the dropdown.")

        # 1. Your own team's slots.
        records, problem = self._choose(COURSE_SELECTOR, option_value)
        if problem:
            return problem
        own_html = delta_to_html(records)
        result = classify_page(own_html)
        if result.status.is_problem:
            return self._with_location(result)
        slots = set(result.slots)

        # 2. Other teams. The dropdown only lists teams that have slots, so look at each.
        for value, label in other_team_options(own_html):
            records, problem = self._choose(OTHER_TEAM_SELECTOR, value)
            if problem:
                return problem
            found, unknown = scan_grid(delta_to_html(records), team=label)
            if not found and unknown:
                return CheckResult(Status.UNEXPECTED, detail=f"{label}: unrecognised icons in the grid: {unknown[:3]}")
            slots |= found

        if slots:
            return CheckResult(Status.SLOTS, slots=frozenset(slots))
        return CheckResult(Status.NO_SLOTS)

    def _choose(self, selector: str, value: str) -> tuple[list[Record], CheckResult | None]:
        """Pick a dropdown option; return the server's reply as records (and a problem, if any)."""
        page = self._page
        with page.expect_response(self._is_postback, timeout=30_000) as info:
            page.select_option(selector, value=value, timeout=30_000)
        response = info.value
        try:
            text = response.text()
        except PlaywrightError:
            text = ""  # a redirect has no body to read
        records = parse_delta(text)
        log.info("Dropdown POST: HTTP %s, %d chars, %d records", response.status, len(text), len(records))
        # Let the page finish its own update before we touch the next dropdown.
        page.wait_for_function(_WEBFORMS_IDLE_JS, polling=500, timeout=30_000)
        return records, self._reply_problem(response.status, records)

    def _reply_problem(self, status: int, records: list[Record]) -> CheckResult | None:
        kinds = {r.kind for r in records}
        if 300 <= status < 400 or "pageRedirect" in kinds:
            return self._with_location(CheckResult(Status.LOGGED_OUT, detail="The server redirected the dropdown request."))
        error = next((r for r in records if r.kind == "error"), None)
        if error:
            return CheckResult(Status.UNEXPECTED, detail=f"The server reported an error: HTTP {error.id}: {error.content[:100]}")
        if not records:
            return CheckResult(Status.UNEXPECTED, detail=f"Unreadable reply to the dropdown (HTTP {status}).")
        return None

    def _is_postback(self, response) -> bool:
        return response.request.method == "POST" and urlparse(response.url).path.lower() == self._booking_path

    def _on_dialog(self, dialog) -> None:
        log.warning("Page showed a %s dialog: %s", dialog.type, dialog.message[:200])
        dialog.dismiss()

    def _with_location(self, result: CheckResult) -> CheckResult:
        """If we've been bounced off the booking page, say where we ended up."""
        if result.status is not Status.LOGGED_OUT:
            return result
        # Path only, never the query string: URLs can carry tokens.
        where = urlparse(self._page.url).path or "/"
        title = self._page.title()
        return replace(result, detail=f"{result.detail} Landed on {where} (page title: {title!r}).")


def _short(exc: Exception) -> str:
    """First line of an error, trimmed, so Telegram messages stay readable."""
    first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return first_line[:150]
