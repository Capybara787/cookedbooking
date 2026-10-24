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


# ---- reading the server's reply directly ---------------------------------------
from replies import SLOT_GRID, encode, other_team_reply, own_team_reply  # noqa: E402
from cdc_bot.parser import delta_to_html, other_team_options, parse_delta, scan_grid  # noqa: E402

PADDED_TEAM = "1T TEAM 17          "


def test_parse_delta_copes_with_pipes_inside_content():
    records = parse_delta("1|#||4|" + encode("updatePanel", "p1", "a|b|c") + encode("hiddenField", "__VIEWSTATE", "x|y"))
    assert [(r.kind, r.id, r.content) for r in records] == [
        ("#", "", "4"), ("updatePanel", "p1", "a|b|c"), ("hiddenField", "__VIEWSTATE", "x|y"),
    ]


def test_parse_delta_stops_cleanly_on_garbage_or_truncation():
    assert parse_delta("<html>please log in</html>") == []
    assert parse_delta("") == []
    assert parse_delta("500|updatePanel|p1|too short|") == []  # declared length doesn't match


def test_reply_with_no_slots_is_classified_like_a_page():
    html = delta_to_html(parse_delta(own_team_reply()))
    assert classify_page(html).status is Status.NO_SLOTS
    assert other_team_options(html) == []


def test_other_team_options_keep_the_exact_padded_value():
    html = delta_to_html(parse_delta(own_team_reply(other_teams=(PADDED_TEAM,))))
    assert other_team_options(html) == [(PADDED_TEAM, "1T TEAM 17")]


def test_other_team_slots_are_tagged_with_the_team():
    slots, unknown = scan_grid(delta_to_html(parse_delta(other_team_reply(SLOT_GRID))), team="1T TEAM 17")
    assert unknown == []
    (slot,) = slots
    assert slot.team == "1T TEAM 17" and slot.time == "08:30-10:10"
    assert "1T TEAM 17" in slot.describe() and "premium" in slot.describe()


def test_own_team_slot_description_is_unchanged():
    assert Slot("24/Oct/2026", "SAT", 3, "12:45-14:25").describe() == "SAT 24/Oct/2026 - slot 3 (12:45-14:25)"


def test_redact_also_blanks_the_page_header_details():
    html = '<span id="HeaderSub_lblName">FAN DIWEN</span><span id="HeaderSub_lblUserID">00987160</span>'
    cleaned = redact_html(html)
    assert "FAN" not in cleaned and "00987160" not in cleaned
