from pathlib import Path

from cdc_bot.models import Slot, Status
from cdc_bot.parser import classify_page, redact_html

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_no_slots_from_real_capture():
    result = classify_page(load("no_slots.html"))
    assert result.status is Status.NO_SLOTS
    assert not result.slots  # the legend's car icons must not be mistaken for slots


def test_slots_found_reads_date_day_and_time():
    result = classify_page(load("slots_found.html"))
    assert result.status is Status.SLOTS
    assert result.slots == {
        Slot("24/Oct/2026", "SAT", 3, "12:45-14:25"),
        Slot("26/Oct/2026", "MON", 5, "16:25-18:05"),
    }  # the red "reserved by you" icon on 27/Oct is ignored


def test_logged_out_page():
    result = classify_page(load("logged_out.html"))
    assert result.status is Status.LOGGED_OUT


def test_unrecognised_icon_is_flagged_not_ignored():
    html = load("slots_found.html").replace("images1.gif", "bluecar.gif")
    result = classify_page(html)
    assert result.status is Status.UNEXPECTED  # safer than silently saying "no slots"
    assert "bluecar.gif" in result.detail


def test_empty_grid_without_message_is_unexpected():
    html = load("no_slots.html").replace("No available slots", "Something else")
    assert classify_page(html).status is Status.UNEXPECTED


def test_slot_is_still_detected_if_table_layout_is_surprising():
    # No header row and no date cells: naming the slot fails, but we must still alert.
    html = load("no_slots.html").replace(
        '<div class="grid-scroll-container"></div>',
        '<div class="grid-scroll-container"><span><img src="x/images1.gif"></span></div>',
    )
    result = classify_page(html)
    assert result.status is Status.SLOTS
    assert len(result.slots) == 1


def test_redact_removes_scripts_and_hidden_values():
    html = (
        '<html><script>var secret = 1;</script>'
        '<input type="hidden" name="__VIEWSTATE" value="abc123">'
        '<span id="ContentPlaceHolder1_lblStoreValue">S$460.80</span></html>'
    )
    cleaned = redact_html(html)
    assert "secret" not in cleaned
    assert "abc123" not in cleaned
    assert "460.80" not in cleaned
