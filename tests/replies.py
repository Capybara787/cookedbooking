"""Helpers that build fake ASP.NET UpdatePanel replies (length|type|id|content|)."""


def encode(kind: str, ident: str, content: str) -> str:
    return f"{len(content)}|{kind}|{ident}|{content}|"


def make_reply(panels: dict[str, str], extra: tuple[str, ...] = ()) -> str:
    parts = ["1|#||4|"]  # real replies start like this
    parts += [encode("updatePanel", ident, html) for ident, html in panels.items()]
    parts.append(encode("hiddenField", "__VIEWSTATE", "AAA|BBB"))  # content with '|' inside
    parts += list(extra)
    return "".join(parts)


def course_panel(other_teams: tuple[str, ...] = ()) -> str:
    options = "".join(f'<option value="{v}">{v.strip()}</option>' for v in other_teams)
    return (
        '<select id="ContentPlaceHolder1_ddlCourse"><option value="">-Please Select-</option>'
        '<option selected="selected" value="ONETEAM             ">02. Class 3</option></select>'
        '<select id="ContentPlaceHolder1_ddlOthTeamID">'
        '<option selected="selected" value="-Please Select-">-Please Select-</option>' + options + "</select>"
    )


NO_SLOTS_MESSAGE = '<span id="ContentPlaceHolder1_lblFullBookMsg">No available slots currently.</span>'
EMPTY_GRID = '<div class="grid-scroll-container"></div>'
SLOT_GRID = (
    "<table><tr><th>Date</th><th>Day</th><th>1<br/>08:30-10:10</th></tr>"
    '<tr><td>24/Oct/2026</td><td>SAT</td><td><img src="../images/Class3And3A/images1.gif"/></td></tr></table>'
)


def own_team_reply(other_teams: tuple[str, ...] = (), grid: str = EMPTY_GRID) -> str:
    return make_reply({
        "ContentPlaceHolder1_UpdatePanel1": course_panel(other_teams),
        "ContentPlaceHolder1_updSessionAvl": NO_SLOTS_MESSAGE,
        "ContentPlaceHolder1_updMainGridView": grid,
    })


def other_team_reply(grid: str) -> str:
    return make_reply({
        "ContentPlaceHolder1_UpdatePanel1": course_panel(),
        "ContentPlaceHolder1_updMainGridView": grid,
    })
