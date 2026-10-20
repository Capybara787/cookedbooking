"""Turn the booking page's HTML into a CheckResult.

Everything here is a pure function (HTML in, result out). That means it can be
tested with saved HTML files: no browser, no network, no login.

IMPORTANT: the "available slot" detection is built from the legend in the real
DevTools capture (images1.gif = Available) and a phone screenshot of the grid.
It has NOT yet been checked against a real populated grid. The code is written
to say "unexpected" rather than guess when it sees something unfamiliar.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from .models import CheckResult, Slot, Status

# --- Facts taken from the real page (DevTools capture) ------------------------
COURSE_SELECT_ID = "ContentPlaceHolder1_ddlCourse"
GRID_PANEL_ID = "ContentPlaceHolder1_updMainGridView"   # the UpdatePanel holding the slot grid
NO_SLOTS_MESSAGE_ID = "ContentPlaceHolder1_lblFullBookMsg"
NO_SLOTS_TEXT = "no available slots"

# The page's own legend maps each car icon to a meaning.
IMG_AVAILABLE = "images1.gif"
IMG_KNOWN_NOT_AVAILABLE = {"images2.gif", "images3.gif"}  # reserved by you / confirmed by you

# Column headers look like "3" + "12:45-14:25" (on separate lines in the page).
_HEADER_RE = re.compile(r"(\d+)\D+?(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")


def classify_page(html: str, team: str = "") -> CheckResult:
    """Decide what the page is telling us: slots, no slots, or something odd."""
    soup = BeautifulSoup(html, "html.parser")

    # 1. Are we even on the booking page? The course dropdown only exists there.
    if soup.find(id=COURSE_SELECT_ID) is None:
        return CheckResult(Status.LOGGED_OUT, detail="The booking page's course dropdown is missing.")

    grid = soup.find(id=GRID_PANEL_ID)
    if grid is None:
        return CheckResult(Status.UNEXPECTED, detail="The slot grid panel is missing from the page.")

    # 2. Look at every icon inside the grid (not the legend, which lives in a different panel).
    available: list[Tag] = []
    unknown: set[str] = set()
    for el in grid.find_all(["img", "input"]):  # slots may be <img> or <input type="image">
        name = _image_name(el.get("src"))
        if not name:
            continue
        if name == IMG_AVAILABLE:
            available.append(el)
        elif name not in IMG_KNOWN_NOT_AVAILABLE:
            unknown.add(name)

    if available:
        slots = frozenset(_describe(el, n, team) for n, el in enumerate(available, start=1))
        return CheckResult(Status.SLOTS, slots=slots)

    # 3. Icons we don't recognise are suspicious: maybe "available" looks different
    #    in the grid than in the legend. Flag it rather than silently saying "no slots".
    if unknown:
        return CheckResult(Status.UNEXPECTED, detail=f"Unrecognised icons in the grid: {sorted(unknown)[:3]}")

    # 4. Positive confirmation of "no slots": the page says so in words.
    message = soup.find(id=NO_SLOTS_MESSAGE_ID)
    if message is not None and NO_SLOTS_TEXT in message.get_text(" ", strip=True).lower():
        return CheckResult(Status.NO_SLOTS)

    return CheckResult(Status.UNEXPECTED, detail="The grid is empty but the 'no available slots' message is missing.")


def redact_html(html: str) -> str:
    """Make a saved page safer to share: drop scripts, blank out hidden form values and balances."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for field in soup.find_all("input", attrs={"type": "hidden"}):  # __VIEWSTATE and friends
        field["value"] = "REDACTED"
    for element_id in (
        "ContentPlaceHolder1_lblStoreValue",
        "ContentPlaceHolder1_lblCurrentCharge",
        "ContentPlaceHolder1_lblNewBalance",
    ):
        element = soup.find(id=element_id)
        if element is not None:
            element.string = "REDACTED"
    return soup.prettify()


# ------------------------------------------------------------------ helpers
def _image_name(src: str | None) -> str:
    """'../images/Class3And3A/images1.gif?v=2' -> 'images1.gif'"""
    if not src:
        return ""
    return src.split("?")[0].rsplit("/", 1)[-1].lower()


def current_team_name(html: str) -> str:
    """Read the current-team label shown above the booking grid, if present."""
    soup = BeautifulSoup(html, "html.parser")
    marker = re.compile(r"you\s+are\s+with", re.IGNORECASE)
    for text in soup.find_all(string=marker):
        for parent in [text.parent, *text.parent.parents]:
            value = parent.get_text(" ", strip=True)
            match = re.search(
                r"you\s+are\s+with\s+(.+?)(?=\s+(?:slots available|note:|select course)\b|$)",
                value,
                re.IGNORECASE,
            )
            if match:
                team = match.group(1).strip()
                if team:
                    return team
    return ""


def other_team_selector(html: str) -> tuple[str, list[tuple[str, str]], str | None] | None:
    """Find the dropdown next to the "from other teams" label.

    The portal uses generated ASP.NET element IDs, so locate the control by its
    visible heading and return a CSS selector plus its exact option values.
    """
    soup = BeautifulSoup(html, "html.parser")
    course = soup.find("select", id=COURSE_SELECT_ID)
    heading = soup.find(string=re.compile(r"from\s+other\s+teams", re.IGNORECASE))
    if heading is None:
        return None

    team_select = None
    for container in [heading.parent, *heading.parent.parents]:
        candidates = [
            select for select in container.find_all("select")
            if select is not course
        ]
        if candidates:
            team_select = candidates[0]
            break
    if team_select is None:
        team_select = heading.find_next("select")
    if team_select is None or team_select is course:
        return None

    element_id = team_select.get("id")
    name = team_select.get("name")
    if element_id:
        selector = f'select[id="{element_id}"]'
    elif name:
        selector = f'select[name="{name}"]'
    else:
        return None

    all_options = team_select.find_all("option", recursive=False)
    placeholder = next(
        (
            str(option.get("value", ""))
            for option in all_options
            if not option.get("value", "").strip()
            or re.match(r"[-\s]*please select|select team", option.get_text(" ", strip=True), re.IGNORECASE)
        ),
        None,
    )
    options = [
        (str(option.get("value", "")), option.get_text(" ", strip=True))
        for option in all_options
        if option.get("value", "").strip() and option.get_text(" ", strip=True)
        and str(option.get("value", "")) != placeholder
    ]
    return selector, options, placeholder


def _describe(icon: Tag, ordinal: int, team: str = "") -> Slot:
    """Work out which date and time an 'available' icon sits under (best effort).

    Spotting the slot is what matters; naming it is a bonus. So a surprise in the
    table layout must never hide a real slot, which is why this falls back to a
    generic Slot instead of raising.
    """
    try:
        cell = icon.find_parent("td")
        row = cell.find_parent("tr")
        cells = row.find_all("td", recursive=False)
        column = cells.index(cell)
        number, time = _column_info(row, column)
        return Slot(
            date=cells[0].get_text(" ", strip=True),
            day=cells[1].get_text(" ", strip=True),
            number=number,
            time=time,
            team=team,
        )
    except Exception:  # noqa: BLE001 - deliberate, see docstring
        return Slot(date=f"(unreadable cell #{ordinal})", day="", number=0, time="", team=team)


def _column_info(row: Tag, column: int) -> tuple[int, str]:
    """Slot number and time range for a column, read from the table's header row."""
    table = row.find_parent("table")
    header_row = next((tr for tr in table.find_all("tr") if tr.find("th")), None) or table.find("tr")
    heads = header_row.find_all(["th", "td"], recursive=False)
    if column < len(heads):
        match = _HEADER_RE.search(heads[column].get_text(" ", strip=True))
        if match:
            return int(match.group(1)), f"{match.group(2)}-{match.group(3)}"
    return max(column - 1, 0), ""  # fallback: Date and Day come first, then slot 1, 2, ...
