"""Drive the browser that YOU logged in with.

You start Chrome/Edge with launch_browser.bat, solve the login (including the
reCAPTCHA) yourself, and leave the window open. This module then connects to
that browser over the Chrome DevTools Protocol (CDP) and, in a tab of its own,
re-picks the course from the dropdown to refresh the slot list, like you would.

Why attach instead of logging in from the script? The login is protected by
reCAPTCHA on purpose. We respect that: a human logs in, the script only watches.
The browser also takes care of the ASP.NET __VIEWSTATE bookkeeping for us.
"""
from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from .config import Config
from .models import CheckResult, Status
from .parser import COURSE_SELECT_ID, classify_page, current_team_name, other_team_selector

COURSE_SELECTOR = f"#{COURSE_SELECT_ID}"

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

    def _check_booking_page(self) -> CheckResult:
        page = self._page

        if not self._on_booking_page():
            page.goto(self._cfg.booking_url, wait_until="domcontentloaded", timeout=30_000)
            try:
                page.wait_for_selector(COURSE_SELECTOR, timeout=10_000)
            except PlaywrightTimeout:
                return self._with_location(classify_page(page.content()))

        if not self._select_course():
            return CheckResult(Status.UNEXPECTED, detail=f"Course {self._cfg.course_value!r} isn't in the dropdown.")

        html = page.content()
        if self._cfg.course_value.upper() != "ONETEAM":
            return self._with_location(classify_page(html, team=current_team_name(html)))

        # The previous check may have left another team selected. Return the
        # dropdown to its placeholder before reading the learner's own grid.
        team_control = other_team_selector(html)
        if team_control is None:
            return CheckResult(
                Status.UNEXPECTED,
                detail="The 'from other teams' dropdown is missing from the booking page.",
            )
        selector, _, placeholder = team_control
        if placeholder is None:
            return CheckResult(Status.UNEXPECTED, detail="The other-teams dropdown has no placeholder option.")
        selected_value = page.eval_on_selector(selector, "select => select.value")
        if selected_value != placeholder:
            self._select_team(selector, placeholder)

        html = page.content()
        current_team = current_team_name(html)
        current_result = classify_page(html, team=current_team)
        if current_result.status.is_problem:
            return self._with_location(current_result)

        team_control = other_team_selector(html)
        if team_control is None:
            return CheckResult(Status.UNEXPECTED, detail="The 'from other teams' dropdown disappeared after refresh.")
        selector, options, _ = team_control
        slots = set(current_result.slots)
        checked_teams: set[str] = set()
        for value, label in options:
            team = label.strip()
            if not team or team.casefold() == current_team.strip().casefold() or team.casefold() in checked_teams:
                continue
            checked_teams.add(team.casefold())
            self._select_team(selector, value)
            result = classify_page(page.content(), team=team)
            if result.status.is_problem:
                detail = f"Couldn't read slots for {team}: {result.detail}" if result.detail else f"Couldn't read slots for {team}."
                return CheckResult(result.status, detail=detail)
            slots.update(result.slots)

        if slots:
            return CheckResult(Status.SLOTS, slots=frozenset(slots))
        return CheckResult(Status.NO_SLOTS)

    def _on_booking_page(self) -> bool:
        if urlparse(self._page.url).path.lower() != self._booking_path:
            return False
        return self._page.query_selector(COURSE_SELECTOR) is not None

    def _select_course(self) -> bool:
        """Pick the course in the dropdown and wait for the page to finish updating."""
        page = self._page
        option_value = page.eval_on_selector(COURSE_SELECTOR, _FIND_OPTION_JS, self._cfg.course_value)
        if option_value is None:
            return False

        # Changing the dropdown fires a background POST (the UpdatePanel request
        # you captured in DevTools). Wait for it to come back...
        with page.expect_response(self._is_postback, timeout=30_000):
            page.select_option(COURSE_SELECTOR, value=option_value)
        # ...then for the page's own JavaScript to finish swapping in the new HTML.
        page.wait_for_function(_WEBFORMS_IDLE_JS, timeout=30_000)
        page.wait_for_timeout(300)  # small safety margin
        return True

    def _select_team(self, selector: str, value: str) -> None:
        """Select another team and wait for the portal's partial page update."""
        page = self._page
        with page.expect_response(self._is_postback, timeout=30_000):
            page.select_option(selector, value=value)
        page.wait_for_function(_WEBFORMS_IDLE_JS, timeout=30_000)
        page.wait_for_timeout(300)

    def _is_postback(self, response) -> bool:
        return response.request.method == "POST" and urlparse(response.url).path.lower() == self._booking_path

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
