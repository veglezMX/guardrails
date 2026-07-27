"""Delivery model workbook — effort per role across the five milestones.

Audience: delivery lead, PMO, engineering management.

The whole design turns on one fact: solution-docs contains no effort, duration,
headcount or budget figures and deliberately refuses to. So this workbook is a
model, not a report. Every number a reader could act on is either an assumption
they can see and edit (Assumptions sheet) or a formula over those assumptions.
There is exactly one sheet with typed-in numbers, and it is labelled as such.

Three global assumptions sit on that sheet, each as a separate visible
multiplier rather than folded into the sizes: person-days per size, a
contingency uplift, and a per-role AI-assistance reduction. The last of these is
introduced here and appears nowhere in the source material — see facts.AI_ASSIST
for why it is per role and why it reduces effort rather than elapsed time.

Text is rationed. The argued-out version of every judgement in here lives in the
decks, which is what prose is for; a workbook earns its place by letting a
reader slice, chart and stress the numbers, so the caveats are tabulated to one
line each and the space goes to charts, rollups and a pivot-ready fact table
instead. Every chart reads cells on its own sheet, and those cells are formulas
over the model — so a chart is a view of the numbers, never a second copy of
them that can drift.

Sheet order is the reading order: what this is, the picture, what you own, the
work, the rollups, the what-if, the raw grain, then the context it all sits in.
"""

from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

import facts as f
from lib import (
    BODY_MUTED, CENTRE, DAYS_FMT, MAGNITUDE, MONEY_FMT, ORDINAL, PCT_FMT,
    RECESSIVE, SERIES, WARN, WARN_FONT, autofilter, bar_chart, cell, data_row,
    header_row, kpi, legend, line_chart, note, ref, section, sheet, title,
)

# --- Assumptions sheet anchors --------------------------------------------
# Every formula in the workbook points here, so the layout is fixed in one place.
SIZE_ROW0 = 8                                   # first size row
SIZE_ROW1 = SIZE_ROW0 + len(f.SIZES) - 1
ROLE_ROW0 = SIZE_ROW1 + 4                       # first role row
ROLE_ROW1 = ROLE_ROW0 + len(f.ROLES) - 1
CONT_ROW = ROLE_ROW1 + 3
AI_ROW0 = CONT_ROW + 3                          # first AI-assist row
AI_ROW1 = AI_ROW0 + len(f.AI_ASSIST) - 1

CONTINGENCY = f"Assumptions!$C${CONT_ROW}"
SIZE_TABLE = f"Assumptions!$A${SIZE_ROW0}:$C${SIZE_ROW1}"
RATE_RANGE = f"Assumptions!$C${ROLE_ROW0}:$C${ROLE_ROW1}"


def _rate(i):
    return f"Assumptions!$C${ROLE_ROW0 + i}"


def _ai(i):
    return f"Assumptions!$C${AI_ROW0 + i}"


# --- Work items sheet columns ----------------------------------------------
WI_ROW0 = 6                                     # first data row
COL_SIZE = 5                                    # E
COL_BASE = 6                                    # F
COL_DAYS = 7                                    # G — days including contingency
COL_SHARE0 = 8                                  # H..K
COL_SHARE_SUM = COL_SHARE0 + len(f.ROLES)       # L
COL_ROLE0 = COL_SHARE_SUM + 1                   # M..P — AI-adjusted role days
COL_AI_DAYS = COL_ROLE0 + len(f.ROLES)          # Q — the assisted total
WI_ROW1 = WI_ROW0 + len(f.WORK_ITEMS) - 1
WI_TOTAL_ROW = WI_ROW1 + 1

SHARE_TOLERANCE = 0.0001
FLAG_FILL = PatternFill("solid", fgColor="F7DDD4")

ROLE_KEYS = [key for key, _, _ in f.ROLES]
MILESTONE_KEYS = [m[0] for m in f.MILESTONES]
# First-appearance order, not size order: a ranking that moved every time
# someone edited a size would imply a stability the model does not have.
WORKSTREAMS = list(dict.fromkeys(w[2] for w in f.WORK_ITEMS))
# The Phase 1–2 validation gate. Everything past it is estimated but unfunded,
# and the charts say so in colour rather than in another paragraph.
PRE_GATE = {"M1", "M2"}


def _wi_range(col):
    letter = get_column_letter(col)
    return f"'Work items'!${letter}${WI_ROW0}:${letter}${WI_ROW1}"


def _wi_cell(col, row):
    return f"'Work items'!${get_column_letter(col)}${row}"


def _by(criteria_col, key_cell, value_col):
    """Assisted days matching one key — the workbook's most-repeated formula."""
    return f"=SUMIF({_wi_range(criteria_col)},{key_cell},{_wi_range(value_col)})"


def _unassisted(role_index, criteria_col=None, key_cell=None):
    """Role days before that role's AI-assistance reduction.

    The assisted figure is a column on Work items; the unassisted one is not,
    because carrying four more columns to hold days × share would double the
    sheet's width to restate what SUMPRODUCT computes in one cell.
    """
    product = (f"SUMPRODUCT({_wi_range(COL_DAYS)},"
               f"{_wi_range(COL_SHARE0 + role_index)}")
    if criteria_col is None:
        return f"={product})"
    return (f"=SUMPRODUCT(({_wi_range(criteria_col)}={key_cell})*"
            f"{_wi_range(COL_DAYS)}*{_wi_range(COL_SHARE0 + role_index)})")


def read_me(wb):
    ws = sheet(wb, "Read me", [30, 26, 26, 22, 22, 22])
    row = title(
        ws, 1, "AI Guardrails — delivery model",
        "Milestones, dependencies and gates are transcribed from solution-docs. "
        "The effort model laid over them is an assumption, not an estimate.")

    row = section(ws, row, "How to use it")
    for step, text in [
        ("1.  Open Assumptions.",
         "The only sheet with typed-in numbers, and every one is a placeholder "
         "chosen to make the model run. Replace them."),
        ("2.  Check the AI-assistance factors.",
         "They assume everyone here delivers with an agentic coding tool. Zero "
         "them for the unassisted model."),
        ("3.  Set a day rate per role.",
         "Leave them empty and every cost column stays empty rather than "
         "showing a misleading zero."),
        ("4.  Argue with the sizes and role splits.",
         "Both are judgements, editable on Work items, reasoned in the "
         "generator."),
        ("5.  Stress it.",
         "Sensitivity moves contingency and assistance together. Pivot data is "
         "the flat grain — build your own pivots off it."),
    ]:
        cell(ws, row, 1, step, bold=True)
        c = ws.cell(row=row, column=2, value=text)
        c.font = BODY_MUTED
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=6)
        row += 1
    row += 1

    row = legend(ws, row)

    row = section(ws, row, "What this workbook does not say")
    for headline, detail in f.STANDING_CAVEATS:
        cell(ws, row, 1, headline, bold=True)
        c = ws.cell(row=row, column=2, value=detail)
        c.font = BODY_MUTED
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=6)
        row += 1
    row += 1

    row = section(ws, row, "Where the content comes from")
    row = header_row(ws, row, ["Sheet", "Source", "", "", "", ""], freeze=False)
    for name, src in [
        ("Work items", "MILESTONES.md §3–§7 completion criteria, document order"),
        ("Roadmap", "MILESTONES.md §2–§7; AGR-020 five-phase delivery"),
        ("Dependencies", "DEPENDENCIES.md §2 register and §3 detail"),
        ("Release gates", "RELEASE-CRITERIA.md gate tables"),
        ("Roles", "TEAM-AND-OWNERSHIP.md §1 four ownership groups"),
        ("Sizes and role splits", "Nowhere — assumed here. See Assumptions."),
        ("AI-assistance factors",
         "Nowhere, and in no other document — assumed here."),
        ("Every chart and rollup", "Formulas over the two rows above."),
    ]:
        cell(ws, row, 1, name, bold=True)
        c = ws.cell(row=row, column=2, value=src)
        c.font = BODY_MUTED
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=6)
        row += 1
    return ws


def dashboard(wb):
    """The picture, and the small formula tables that feed it.

    Each chart sits beside the table it reads, rather than hiding its source on
    a stashed sheet: the table is also the accessible view of the chart, which
    is what discharges the contrast relief the lighter series carry.
    """
    ws = sheet(wb, "Dashboard", [26] + [12] * 13)
    title(ws, 1, "Dashboard",
          "Every figure is a formula over Assumptions and Work items. Change an "
          "assumption and every chart here moves with it.", span=8)

    total_after = _wi_cell(COL_AI_DAYS, WI_TOTAL_ROW)
    total_before = _wi_cell(COL_DAYS, WI_TOTAL_ROW)
    gate = " + ".join(
        f'SUMIF({_wi_range(2)},"{k}",{_wi_range(COL_AI_DAYS)})'
        for k in MILESTONE_KEYS if k in PRE_GATE)

    kpi(ws, 5, 1, "TOTAL DAYS, AFTER AI ASSIST", f"={total_after}",
        "The headline number. It is a model output, not an estimate.")
    kpi(ws, 5, 3, "TOTAL DAYS, BEFORE AI ASSIST", f"={total_before}",
        "Set every assistance factor to zero and the headline becomes this.")
    kpi(ws, 5, 5, "DAYS THE ASSIST ASSUMPTION REMOVES",
        f"={total_before}-{total_after}",
        "Removed by an assumption this workbook makes on its own authority.")
    kpi(ws, 5, 7, "DAYS BEHIND THE PHASE 1–2 GATE", f"={gate}",
        "M1 and M2 only. The rest is estimated, and must not be resourced yet.")

    # --- effort by milestone, and the gate ---------------------------------
    row = 10
    section(ws, row - 1, "Where the effort sits")
    header_row(ws, row, ["Milestone", "Days after AI assist"], freeze=False)
    for i, key in enumerate(MILESTONE_KEYS):
        cell(ws, row + 1 + i, 1, key, bold=True)
        cell(ws, row + 1 + i, 2, _by(2, f"$A${row + 1 + i}", COL_AI_DAYS),
             kind="computed", fmt=DAYS_FMT, align=CENTRE)
    bar_chart(
        ws, f"H{row}",
        title="Effort by milestone — shaded past the Phase 1–2 gate",
        data=ref(ws, 2, row, row + len(MILESTONE_KEYS)),
        cats=ref(ws, 1, row + 1, row + len(MILESTONE_KEYS)),
        colours=[MAGNITUDE], fmt="0", y_title="person-days",
        emphasis=[MAGNITUDE if k in PRE_GATE else RECESSIVE
                  for k in MILESTONE_KEYS])

    # --- what the assist assumption buys -----------------------------------
    row = 27
    section(ws, row - 1, "What the AI-assistance assumption buys")
    header_row(ws, row, ["Role", "Before assist", "After assist"], freeze=False)
    for i, key in enumerate(ROLE_KEYS):
        r = row + 1 + i
        cell(ws, r, 1, key, bold=True)
        cell(ws, r, 2, _unassisted(i), kind="computed", fmt=DAYS_FMT,
             align=CENTRE)
        cell(ws, r, 3, f"=SUM({_wi_range(COL_ROLE0 + i)})", kind="computed",
             fmt=DAYS_FMT, align=CENTRE)
    bar_chart(
        ws, f"H{row}", title="Days per role, before and after AI assistance",
        data=ref(ws, 2, row, row + len(ROLE_KEYS), col1=3),
        cats=ref(ws, 1, row + 1, row + len(ROLE_KEYS)),
        colours=SERIES[:2], fmt="0", y_title="person-days")

    # --- how the role mix shifts -------------------------------------------
    row = 44
    section(ws, row - 1, "How the role mix shifts across the milestones")
    header_row(ws, row, ["Milestone"] + ROLE_KEYS, freeze=False)
    for i, key in enumerate(MILESTONE_KEYS):
        r = row + 1 + i
        cell(ws, r, 1, key, bold=True)
        for j in range(len(ROLE_KEYS)):
            cell(ws, r, 2 + j, _by(2, f"$A${r}", COL_ROLE0 + j),
                 kind="computed", fmt=DAYS_FMT, align=CENTRE)
    bar_chart(
        ws, f"H{row}", title="Share of effort by role, per milestone",
        data=ref(ws, 2, row, row + len(MILESTONE_KEYS), col1=1 + len(ROLE_KEYS)),
        cats=ref(ws, 1, row + 1, row + len(MILESTONE_KEYS)),
        colours=SERIES, percent=True, labels=False)

    # --- the shape of the work ---------------------------------------------
    row = 61
    section(ws, row - 1, "The shape of the work")
    header_row(ws, row, ["Size", "Work items", "Days after AI assist"],
               freeze=False)
    for i, (key, _label, _meaning, _days) in enumerate(f.SIZES):
        r = row + 1 + i
        cell(ws, r, 1, key, bold=True)
        cell(ws, r, 2, f"=COUNTIF({_wi_range(COL_SIZE)},$A${r})",
             kind="computed", align=CENTRE)
        cell(ws, r, 3, _by(COL_SIZE, f"$A${r}", COL_AI_DAYS), kind="computed",
             fmt=DAYS_FMT, align=CENTRE)
    bar_chart(
        ws, f"H{row}", title="Effort by work-item size",
        data=ref(ws, 3, row, row + len(f.SIZES)),
        cats=ref(ws, 1, row + 1, row + len(f.SIZES)),
        colours=[MAGNITUDE], fmt="0", y_title="person-days")

    # --- workstreams --------------------------------------------------------
    row = 78
    section(ws, row - 1, "Effort by workstream")
    header_row(ws, row, ["Workstream", "Work items", "Days after AI assist"],
               freeze=False)
    for i, name in enumerate(WORKSTREAMS):
        r = row + 1 + i
        cell(ws, r, 1, name)
        cell(ws, r, 2, f"=COUNTIF({_wi_range(3)},$A${r})", kind="computed",
             align=CENTRE)
        cell(ws, r, 3, _by(3, f"$A${r}", COL_AI_DAYS), kind="computed",
             fmt=DAYS_FMT, align=CENTRE)
    bar_chart(
        ws, f"H{row}", title="Effort by workstream, in document order",
        data=ref(ws, 3, row, row + len(WORKSTREAMS)),
        cats=ref(ws, 1, row + 1, row + len(WORKSTREAMS)),
        colours=[MAGNITUDE], horizontal=True, fmt="0", height=13.0)
    return ws


def assumptions(wb):
    ws = sheet(wb, "Assumptions", [14, 26, 16, 62, 20, 20])
    row = title(
        ws, 1, "Assumptions — the only sheet you edit",
        "Everything else in this workbook is either transcribed from the "
        "documentation or computed from what is here.")

    row = note(ws, row,
               "The person-day values below are PLACEHOLDERS chosen so the model "
               "runs. No effort estimate exists upstream. Replace them before "
               "quoting any total.", font=WARN_FONT)

    # --- sizes -------------------------------------------------------------
    row = header_row(ws, SIZE_ROW0 - 1,
                     ["Size", "Label", "Person-days (unassisted)", "What it means",
                      "", ""], freeze=False)
    for key, label, meaning, days in f.SIZES:
        cell(ws, row, 1, key, bold=True, align=CENTRE)
        cell(ws, row, 2, label)
        cell(ws, row, 3, days, kind="input", fmt=DAYS_FMT, align=CENTRE)
        cell(ws, row, 4, meaning)
        row += 1

    # --- day rates ---------------------------------------------------------
    row = header_row(ws, ROLE_ROW0 - 1,
                     ["Role", "Name", "Day rate", "Mandate", "", ""], freeze=False)
    for key, name, mandate in f.ROLES:
        cell(ws, row, 1, key, bold=True, align=CENTRE)
        cell(ws, row, 2, name)
        cell(ws, row, 3, None, kind="input", fmt=MONEY_FMT, align=CENTRE)
        cell(ws, row, 4, mandate)
        row += 1

    # --- contingency -------------------------------------------------------
    row = CONT_ROW
    cell(ws, row, 1, "Contingency", bold=True)
    cell(ws, row, 2, "Applied to every work item")
    cell(ws, row, 3, 0, kind="input", fmt=PCT_FMT, align=CENTRE)
    cell(ws, row, 4, "One uplift across the whole model. Zero by default — "
                     "inventing a contingency figure on your behalf would defeat "
                     "the point of this sheet. Sensitivity shows the range.")

    # --- AI assistance -----------------------------------------------------
    row = header_row(ws, AI_ROW0 - 1,
                     ["Role", "AI assist", "Effort reduction",
                      "Why this figure and not another", "", ""], freeze=False)
    for i, (key, factor, why) in enumerate(f.AI_ASSIST):
        cell(ws, row, 1, key, bold=True, align=CENTRE)
        cell(ws, row, 2, f.ROLES[i][1])
        cell(ws, row, 3, factor, kind="input", fmt=PCT_FMT, align=CENTRE)
        cell(ws, row, 4, why)
        row += 1

    row += 1
    row = note(ws, row, f.AI_ASSIST_CAVEAT)
    row = note(ws, row,
               "Day rates ship empty on purpose: with no rate set, every cost "
               "column stays blank rather than computing a zero that could pass "
               "for a real figure.")
    return ws


def work_items(wb):
    widths = [10, 11, 15, 62, 8, 11, 13] + [9] * len(f.ROLES) + [10] + \
             [10] * len(f.ROLES) + [13]
    ws = sheet(wb, "Work items", widths)
    title(ws, 1, "Work items",
          "One row per milestone completion criterion, in document order. Size "
          "and role split are yours to edit; everything shaded blue is a "
          "formula. Filter from the header row.", span=len(widths))

    header_row(ws, WI_ROW0 - 1,
               ["ID", "Milestone", "Workstream", "Work item", "Size", "Base days",
                "Days incl. cont. (unassisted)"]
               + [f"{k} share" for k in ROLE_KEYS]
               + ["Share Σ"]
               + [f"{k} days" for k in ROLE_KEYS]
               + ["Days after AI assist"])

    # allow_blank=False alone rejects nothing: without showErrorMessage the list
    # is decorative, and a mistyped size drives #N/A through every total on
    # every sheet. The VLOOKUP is deliberately left unguarded so that a size
    # pasted past this check fails loudly rather than resolving to zero.
    validation = DataValidation(
        type="list",
        formula1='"' + ",".join(key for key, _, _, _ in f.SIZES) + '"',
        allow_blank=False,
        showDropDown=False,
        showErrorMessage=True,
        errorTitle="Unknown size",
        error="Size must be one of "
              + ", ".join(key for key, _, _, _ in f.SIZES)
              + ". Anything else breaks every total in this workbook.")
    ws.add_data_validation(validation)
    col_size = get_column_letter(COL_SIZE)
    col_base = get_column_letter(COL_BASE)
    col_days = get_column_letter(COL_DAYS)
    validation.add(f"{col_size}{WI_ROW0}:{col_size}{WI_ROW1}")

    row = WI_ROW0
    for wid, milestone, workstream, item, size, shares in f.WORK_ITEMS:
        band = (row - WI_ROW0) % 2 == 1
        cell(ws, row, 1, wid, bold=True, band=band)
        cell(ws, row, 2, milestone, align=CENTRE, band=band)
        cell(ws, row, 3, workstream, band=band)
        cell(ws, row, 4, item, band=band)

        cell(ws, row, COL_SIZE, size, kind="input", align=CENTRE)

        cell(ws, row, COL_BASE,
             f"=VLOOKUP(${col_size}{row},{SIZE_TABLE},3,FALSE)",
             kind="computed", fmt=DAYS_FMT, align=CENTRE)
        cell(ws, row, COL_DAYS,
             f"=${col_base}{row}*(1+{CONTINGENCY})",
             kind="computed", fmt=DAYS_FMT, align=CENTRE)

        for i, key in enumerate(ROLE_KEYS):
            cell(ws, row, COL_SHARE0 + i, shares.get(key, 0), kind="input",
                 fmt=PCT_FMT, align=CENTRE)

        first = get_column_letter(COL_SHARE0)
        last = get_column_letter(COL_SHARE0 + len(ROLE_KEYS) - 1)
        cell(ws, row, COL_SHARE_SUM, f"=SUM({first}{row}:{last}{row})",
             kind="computed", fmt=PCT_FMT, align=CENTRE)

        for i in range(len(ROLE_KEYS)):
            share_col = get_column_letter(COL_SHARE0 + i)
            cell(ws, row, COL_ROLE0 + i,
                 f"=${col_days}{row}*{share_col}{row}*(1-{_ai(i)})",
                 kind="computed", fmt=DAYS_FMT, align=CENTRE)

        role_first = get_column_letter(COL_ROLE0)
        role_last = get_column_letter(COL_ROLE0 + len(ROLE_KEYS) - 1)
        cell(ws, row, COL_AI_DAYS,
             f"=SUM({role_first}{row}:{role_last}{row})",
             kind="computed", fmt=DAYS_FMT, bold=True, align=CENTRE)
        row += 1

    cell(ws, row, 4, "Total", bold=True, align=CENTRE)
    for col in [COL_DAYS] + [COL_ROLE0 + i for i in range(len(ROLE_KEYS))] \
            + [COL_AI_DAYS]:
        letter = get_column_letter(col)
        cell(ws, row, col, f"=SUM({letter}{WI_ROW0}:{letter}{WI_ROW1})",
             kind="computed", fmt=DAYS_FMT, bold=True, align=CENTRE)

    # The filter must stop above the total row, or filtering hides the total.
    autofilter(ws, WI_ROW0 - 1, WI_ROW1, len(widths))

    # A role split that no longer sums to 100% silently mis-allocates days and
    # nothing else on the sheet would look wrong, so say so in colour.
    flag = (FLAG_FILL, Font(bold=True, color=WARN))
    sum_col = get_column_letter(COL_SHARE_SUM)
    ws.conditional_formatting.add(
        f"{sum_col}{WI_ROW0}:{sum_col}{WI_ROW1}",
        FormulaRule(formula=[f"ABS(${sum_col}{WI_ROW0}-1)>{SHARE_TOLERANCE}"],
                    fill=flag[0], font=flag[1], stopIfTrue=False))
    # And a size pasted in past the dropdown.
    ws.conditional_formatting.add(
        f"{col_size}{WI_ROW0}:{col_size}{WI_ROW1}",
        FormulaRule(formula=[f"ISNA(${col_base}{WI_ROW0})"],
                    fill=flag[0], font=flag[1], stopIfTrue=False))
    return ws


def effort_by_role(wb):
    widths = [10, 26] + [11] * len(MILESTONE_KEYS) + [13, 14, 12, 11, 11, 15]
    ws = sheet(wb, "Effort by role", widths)
    title(ws, 1, "Effort by role",
          "Every cell is a formula over Work items and Assumptions. Milestone "
          "columns are net of each role's AI-assistance reduction; the two "
          "columns after the total show what that reduction removed.",
          span=len(widths))

    hdr = 4
    header_row(ws, hdr, ["Role", "Name"] + MILESTONE_KEYS
               + ["Total days", "Before assist", "Removed", "AI assist",
                  "Day rate", "Cost"])

    first_m = 3
    total_col = first_m + len(MILESTONE_KEYS)
    before_col = total_col + 1
    saved_col = total_col + 2
    ai_col = total_col + 3
    rate_col = total_col + 4
    cost_col = total_col + 5

    row = hdr + 1
    for i, (key, name, _) in enumerate(f.ROLES):
        band = i % 2 == 1
        cell(ws, row, 1, key, bold=True, align=CENTRE, band=band)
        cell(ws, row, 2, name, band=band)
        for j, milestone in enumerate(MILESTONE_KEYS):
            cell(ws, row, first_m + j, _by(2, f'"{milestone}"', COL_ROLE0 + i),
                 kind="computed", fmt=DAYS_FMT, align=CENTRE)
        first = get_column_letter(first_m)
        last = get_column_letter(total_col - 1)
        cell(ws, row, total_col, f"=SUM({first}{row}:{last}{row})",
             kind="computed", fmt=DAYS_FMT, bold=True, align=CENTRE)
        cell(ws, row, before_col, _unassisted(i), kind="computed", fmt=DAYS_FMT,
             align=CENTRE)
        cell(ws, row, saved_col,
             f"={get_column_letter(before_col)}{row}"
             f"-{get_column_letter(total_col)}{row}",
             kind="computed", fmt=DAYS_FMT, align=CENTRE)
        cell(ws, row, ai_col, f"={_ai(i)}", kind="computed", fmt=PCT_FMT,
             align=CENTRE)
        # A bare reference to an empty rate renders as 0.00, which is exactly the
        # misleading figure this sheet promises not to show.
        cell(ws, row, rate_col, f'=IF({_rate(i)}="","",{_rate(i)})',
             kind="computed", fmt=MONEY_FMT, align=CENTRE)
        cell(ws, row, cost_col,
             f'=IF({_rate(i)}="","",{get_column_letter(total_col)}{row}*{_rate(i)})',
             kind="computed", fmt=MONEY_FMT, align=CENTRE)
        row += 1

    cell(ws, row, 2, "Total", bold=True)
    for col in list(range(first_m, saved_col + 1)):
        letter = get_column_letter(col)
        cell(ws, row, col, f"=SUM({letter}{hdr + 1}:{letter}{row - 1})",
             kind="computed", fmt=DAYS_FMT, bold=True, align=CENTRE)
    cost_letter = get_column_letter(cost_col)
    cell(ws, row, cost_col,
         f'=IF(COUNT({RATE_RANGE})=0,"",'
         f'SUM({cost_letter}{hdr + 1}:{cost_letter}{row - 1}))',
         kind="computed", fmt=MONEY_FMT, bold=True, align=CENTRE)

    note(ws, row + 2,
         "Cost stays blank until a day rate is set on Assumptions. With only "
         "some rates set, the total covers only those roles.", span=len(widths))

    # Milestones are an ordered sequence, so they take a single-hue ramp rather
    # than four unrelated categorical hues.
    bar_chart(
        ws, f"A{row + 4}", title="Each role's days, split across the milestones",
        data=ref(ws, first_m, hdr, hdr + len(f.ROLES), col1=total_col - 1),
        cats=ref(ws, 2, hdr + 1, hdr + len(f.ROLES)),
        colours=ORDINAL, stacked=True, horizontal=True, fmt="0", labels=False)
    return ws


def effort_by_milestone(wb):
    widths = [10, 28, 11] + [11] * len(ROLE_KEYS) + [13, 13, 15]
    ws = sheet(wb, "Effort by milestone", widths)
    title(ws, 1, "Effort by milestone",
          "The same model rolled up the other way, net of AI assistance. "
          "Milestone order is dependency sequencing — it carries no dates and "
          "implies no schedule.", span=len(widths))

    hdr = 4
    header_row(ws, hdr, ["Milestone", "Name", "Phase"]
               + [f"{k} days" for k in ROLE_KEYS]
               + ["Total days", "Work items", "Cost"])

    role0 = 4
    total_col = role0 + len(ROLE_KEYS)
    count_col = total_col + 1
    cost_col = total_col + 2

    row = hdr + 1
    for i, (key, name, phase, _mode, _obj, _entry) in enumerate(f.MILESTONES):
        band = i % 2 == 1
        cell(ws, row, 1, key, bold=True, align=CENTRE, band=band)
        cell(ws, row, 2, name, band=band)
        cell(ws, row, 3, phase, align=CENTRE, band=band)
        for j in range(len(ROLE_KEYS)):
            cell(ws, row, role0 + j, _by(2, f"$A{row}", COL_ROLE0 + j),
                 kind="computed", fmt=DAYS_FMT, align=CENTRE)
        first = get_column_letter(role0)
        last = get_column_letter(total_col - 1)
        cell(ws, row, total_col, f"=SUM({first}{row}:{last}{row})",
             kind="computed", fmt=DAYS_FMT, bold=True, align=CENTRE)
        cell(ws, row, count_col, f'=COUNTIF({_wi_range(2)},$A{row})',
             kind="computed", align=CENTRE)
        terms = " + ".join(
            f"{get_column_letter(role0 + j)}{row}*{_rate(j)}"
            for j in range(len(ROLE_KEYS)))
        cell(ws, row, cost_col,
             f'=IF(COUNT({RATE_RANGE})=0,"",{terms})',
             kind="computed", fmt=MONEY_FMT, align=CENTRE)
        row += 1

    cell(ws, row, 2, "Total", bold=True)
    for col in range(role0, count_col + 1):
        letter = get_column_letter(col)
        cell(ws, row, col, f"=SUM({letter}{hdr + 1}:{letter}{row - 1})",
             kind="computed", fmt=(None if col == count_col else DAYS_FMT),
             bold=True, align=CENTRE)
    cost_letter = get_column_letter(cost_col)
    cell(ws, row, cost_col,
         f'=IF(COUNT({RATE_RANGE})=0,"",'
         f'SUM({cost_letter}{hdr + 1}:{cost_letter}{row - 1}))',
         kind="computed", fmt=MONEY_FMT, bold=True, align=CENTRE)

    note(ws, row + 2,
         "M3–M5 lie beyond the Phase 1–2 validation gate. Their totals appear "
         "because the feature set is estimated across all five phases, not "
         "because the work may start.", span=len(widths))

    bar_chart(
        ws, f"A{row + 4}", title="Days per milestone, by role",
        data=ref(ws, role0, hdr, hdr + len(f.MILESTONES), col1=total_col - 1),
        cats=ref(ws, 1, hdr + 1, hdr + len(f.MILESTONES)),
        colours=SERIES, stacked=True, fmt="0", y_title="person-days")
    return ws


def effort_by_workstream(wb):
    """The dimension the source material carries and nothing else rolls up.

    Every work item names a workstream. Milestones say when work may start;
    workstreams say what kind of thing is being built, and the two do not line
    up — which is the point of having this sheet at all.
    """
    widths = [24, 11, 14, 14, 12] + [11] * len(ROLE_KEYS)
    ws = sheet(wb, "Effort by workstream", widths)
    title(ws, 1, "Effort by workstream",
          "Rolled up on the dimension the milestones cut across. Document "
          "order, not size order — a ranking that reshuffled on every edit "
          "would imply a stability this model does not have.", span=len(widths))

    hdr = 4
    header_row(ws, hdr, ["Workstream", "Work items", "Days after assist",
                         "Before assist", "Removed"]
               + [f"{k} days" for k in ROLE_KEYS])
    role0 = 6

    row = hdr + 1
    for i, name in enumerate(WORKSTREAMS):
        band = i % 2 == 1
        cell(ws, row, 1, name, bold=True, band=band)
        cell(ws, row, 2, f"=COUNTIF({_wi_range(3)},$A{row})", kind="computed",
             align=CENTRE)
        cell(ws, row, 3, _by(3, f"$A{row}", COL_AI_DAYS), kind="computed",
             fmt=DAYS_FMT, align=CENTRE)
        before = " + ".join(
            _unassisted(j, 3, f"$A{row}").lstrip("=")
            for j in range(len(ROLE_KEYS)))
        cell(ws, row, 4, f"={before}", kind="computed", fmt=DAYS_FMT,
             align=CENTRE)
        cell(ws, row, 5, f"=D{row}-C{row}", kind="computed", fmt=DAYS_FMT,
             align=CENTRE)
        for j in range(len(ROLE_KEYS)):
            cell(ws, row, role0 + j, _by(3, f"$A{row}", COL_ROLE0 + j),
                 kind="computed", fmt=DAYS_FMT, align=CENTRE)
        row += 1

    cell(ws, row, 1, "Total", bold=True)
    for col in range(2, role0 + len(ROLE_KEYS)):
        letter = get_column_letter(col)
        cell(ws, row, col, f"=SUM({letter}{hdr + 1}:{letter}{row - 1})",
             kind="computed", fmt=(None if col == 2 else DAYS_FMT), bold=True,
             align=CENTRE)
    autofilter(ws, hdr, row - 1, len(widths))

    bar_chart(
        ws, f"A{row + 2}", title="Workstream effort, by role",
        data=ref(ws, role0, hdr, hdr + len(WORKSTREAMS),
                 col1=role0 + len(ROLE_KEYS) - 1),
        cats=ref(ws, 1, hdr + 1, hdr + len(WORKSTREAMS)),
        colours=SERIES, stacked=True, horizontal=True, fmt="0", labels=False,
        height=14.0)
    return ws


# --- Sensitivity ------------------------------------------------------------
# Two knobs move the headline: the contingency uplift and how much of the
# assumed AI assistance you believe. A single point estimate off one pair of
# guesses invites a reader to quote it; a grid does not.
SENS_CONT = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25]
SENS_ASSIST = [
    (0.0, "No assistance"),
    (0.5, "Half the assumed"),
    (1.0, "The assumed factors"),
]


def sensitivity(wb):
    widths = [18, 18, 18, 18, 14, 14, 14, 14]
    ws = sheet(wb, "Sensitivity", widths)
    title(ws, 1, "Sensitivity",
          "Total person-days as the two knobs move. The contingency column here "
          "is local to this sheet and does not change Assumptions; the "
          "assistance multiplier scales every role's factor at once.",
          span=len(widths))

    # Unassisted base days per role, before contingency — the grid's closed
    # form needs each role separately, because the factors differ per role.
    base_hdr = 4
    header_row(ws, base_hdr, ["Base days, no contingency, no assistance"]
               + ROLE_KEYS + ["", "", "", ""], freeze=False)
    base_row = base_hdr + 1
    cell(ws, base_row, 1, "Per role", bold=True)
    for i in range(len(ROLE_KEYS)):
        cell(ws, base_row, 2 + i,
             f"=SUMPRODUCT({_wi_range(COL_BASE)},"
             f"{_wi_range(COL_SHARE0 + i)})",
             kind="computed", fmt=DAYS_FMT, align=CENTRE)

    grid_hdr = base_row + 3
    header_row(ws, grid_hdr, ["Contingency"] + [label for _k, label in SENS_ASSIST]
               + ["", "", "", ""], freeze=False)
    for i, cont in enumerate(SENS_CONT):
        r = grid_hdr + 1 + i
        cell(ws, r, 1, cont, kind="input", fmt=PCT_FMT, align=CENTRE)
        for j, (scale, _label) in enumerate(SENS_ASSIST):
            terms = " + ".join(
                f"{get_column_letter(2 + k)}${base_row}*(1-{_ai(k)}*{scale})"
                for k in range(len(ROLE_KEYS)))
            cell(ws, r, 2 + j, f"=(1+$A{r})*({terms})", kind="computed",
                 fmt=DAYS_FMT, align=CENTRE)

    last = grid_hdr + len(SENS_CONT)
    line_chart(
        ws, f"A{last + 2}",
        title="Total person-days against contingency, at three levels of assistance",
        data=ref(ws, 2, grid_hdr, last, col1=1 + len(SENS_ASSIST)),
        cats=ref(ws, 1, grid_hdr + 1, last),
        colours=[ORDINAL[0], ORDINAL[2], ORDINAL[4]],
        x_title="contingency", y_title="person-days", width=17.0)

    note(ws, last + 2 + 16,
         "The spread across this grid is wider than any difference the model's "
         "role splits produce. Argue about these two numbers first.",
         span=len(widths))
    return ws


def pivot_data(wb):
    """One row per work item per role — the flat grain, for building pivots on.

    The Work items sheet is wide because it is meant to be read. This is the
    same model long, because a PivotTable cannot consume the wide one: four
    role columns are four measures there, and one dimension here.
    """
    widths = [10, 11, 9, 20, 7, 8, 9, 15, 15, 50]
    ws = sheet(wb, "Pivot data", widths)
    title(ws, 1, "Pivot data",
          "Every work item, once per role. Insert → PivotTable over this sheet "
          "and slice it any way the rollups do not. Rows with a zero share are "
          "kept so the grain stays rectangular.", span=len(widths))

    phase_for = {m[0]: m[2] for m in f.MILESTONES}
    hdr = 4
    header_row(ws, hdr, ["Work item", "Milestone", "Phase", "Workstream", "Size",
                         "Role", "Share", "Days before assist",
                         "Days after assist", "Description"])

    row = hdr + 1
    for index, (wid, milestone, workstream, item, size, _shares) in \
            enumerate(f.WORK_ITEMS):
        src = WI_ROW0 + index
        for j, role in enumerate(ROLE_KEYS):
            band = index % 2 == 1
            cell(ws, row, 1, wid, bold=True, band=band)
            cell(ws, row, 2, milestone, align=CENTRE, band=band)
            cell(ws, row, 3, phase_for[milestone], align=CENTRE, band=band)
            cell(ws, row, 4, workstream, band=band)
            cell(ws, row, 5, size, align=CENTRE, band=band)
            cell(ws, row, 6, role, align=CENTRE, band=band)
            share = _wi_cell(COL_SHARE0 + j, src).replace("$", "")
            cell(ws, row, 7, f"={share}", kind="computed", fmt=PCT_FMT,
                 align=CENTRE)
            cell(ws, row, 8,
                 f"={_wi_cell(COL_DAYS, src).replace('$', '')}*{share}",
                 kind="computed", fmt=DAYS_FMT, align=CENTRE)
            cell(ws, row, 9,
                 f"={_wi_cell(COL_ROLE0 + j, src).replace('$', '')}",
                 kind="computed", fmt=DAYS_FMT, align=CENTRE)
            cell(ws, row, 10, item, band=band)
            row += 1

    # A real Excel table, not just a filter: a PivotTable built over a named
    # table grows with it, one built over a fixed range does not.
    table = Table(displayName="PivotData",
                  ref=f"A{hdr}:{get_column_letter(len(widths))}{row - 1}")
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleLight1", showRowStripes=False, showColumnStripes=False)
    ws.add_table(table)
    return ws


def roadmap(wb):
    widths = [10, 28, 11, 34, 52, 52]
    ws = sheet(wb, "Roadmap", widths)
    title(ws, 1, "Roadmap",
          "Dependency sequencing, not scheduling. No calendar dates or "
          "durations exist in the source material and none are invented here.",
          span=len(widths))

    row = header_row(ws, 4, ["Milestone", "Name", "Phase", "Primary deployment mode",
                             "Objective", "Entry condition"])
    for i, (key, name, phase, mode, objective, entry) in enumerate(f.MILESTONES):
        row = data_row(ws, row, [key, name, phase, mode, objective, entry],
                       band=i % 2 == 1)
    autofilter(ws, 4, row - 1, len(widths))

    row += 1
    note(ws, row,
         "The full Phase 1–2 validation demonstration — all nine criteria "
         "together, against two genuinely independent deployments — passes at "
         "M2, not M1. No subset satisfies the gate.", span=len(widths))
    return ws


def dependencies(wb):
    widths = [42, 12, 34, 30, 18, 60]
    ws = sheet(wb, "Dependencies", widths)
    title(ws, 1, "Dependencies",
          "What has to be true before the work can proceed, and who owns it. "
          "None is confirmed today.", span=len(widths))

    row = header_row(ws, 4, ["Dependency", "Type", "Needed for", "Owner", "Status",
                             "Why it gates"])
    for i, dep in enumerate(f.DEPENDENCIES):
        row = data_row(ws, row, list(dep), band=i % 2 == 1)
    autofilter(ws, 4, row - 1, len(widths))

    row += 1
    note(ws, row,
         "Two sit outside the delivery team entirely: managed engine access in "
         "the target Regions, and a second team's willingness to run an "
         "independent instance. Neither waits on effort, so no AI-assistance "
         "factor touches them.", span=len(widths))
    return ws


def release_gates(wb):
    widths = [13, 34, 66, 40, 11]
    ws = sheet(wb, "Release gates", widths)
    title(ws, 1, "Release gates",
          "Pass or fail statements with a named owner. The nine validation "
          "gates must pass together — a subset does not satisfy the "
          "demonstration.", span=len(widths))

    row = header_row(ws, 4, ["ID", "Gate", "What passing means", "Accountable owner",
                             "Milestone"])
    for i, gate in enumerate(f.RELEASE_GATES):
        gid, name, statement, owner, milestone = gate
        row = data_row(ws, row, [gid, name, statement, owner, milestone],
                       band=i % 2 == 1)
    autofilter(ws, 4, row - 1, len(widths))

    row += 1
    note(ws, row,
         "Verification method, environment and sign-off owner per criterion are "
         "only partly assigned upstream — a gap owed before the first phase "
         "exits.", span=len(widths))
    return ws


SHEETS = [
    read_me,
    dashboard,
    assumptions,
    work_items,
    effort_by_role,
    effort_by_milestone,
    effort_by_workstream,
    sensitivity,
    pivot_data,
    roadmap,
    dependencies,
    release_gates,
]
