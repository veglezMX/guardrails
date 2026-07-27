"""Shared styling and sheet helpers for the generated workbooks.

Three cell roles carry meaning and are styled to be distinguishable at a glance,
because the distinction is the point of these workbooks:

    INPUT     an assumption you own. Nothing in solution-docs supplies it.
    DERIVED   a fact read out of solution-docs. Editing it here changes nothing
              upstream and will be overwritten on the next build.
    COMPUTED  a formula. Never a typed-in number.

Colours are placeholders, consistent with the deck tooling — no brand kit exists
yet.
"""

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# --- palette (placeholder) -------------------------------------------------

INK = "1D242C"
MUTED = "6B7785"
ACCENT = "2B4A69"
PAPER = "FFFFFF"
BAND = "F4F6F8"
HEAD_BG = "1D242C"
INPUT_BG = "FFF4D6"
INPUT_EDGE = "E0B341"
COMPUTED_BG = "EEF4FB"
WARN = "B4552B"

BODY_FONT = "Calibri"

# --- reusable styles -------------------------------------------------------

TITLE = Font(name=BODY_FONT, size=15, bold=True, color=INK)
SUBTITLE = Font(name=BODY_FONT, size=10.5, color=MUTED)
HEADER = Font(name=BODY_FONT, size=10.5, bold=True, color="FFFFFF")
SECTION = Font(name=BODY_FONT, size=11, bold=True, color=ACCENT)
BODY = Font(name=BODY_FONT, size=10.5, color=INK)
BODY_MUTED = Font(name=BODY_FONT, size=10, color=MUTED)
BODY_BOLD = Font(name=BODY_FONT, size=10.5, bold=True, color=INK)
WARN_FONT = Font(name=BODY_FONT, size=10.5, bold=True, color=WARN)

HEADER_FILL = PatternFill("solid", fgColor=HEAD_BG)
BAND_FILL = PatternFill("solid", fgColor=BAND)
INPUT_FILL = PatternFill("solid", fgColor=INPUT_BG)
COMPUTED_FILL = PatternFill("solid", fgColor=COMPUTED_BG)

_thin = Side(style="thin", color="D6DBE0")
_input_side = Side(style="thin", color=INPUT_EDGE)
GRID = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
INPUT_BORDER = Border(left=_input_side, right=_input_side, top=_input_side,
                      bottom=_input_side)

WRAP = Alignment(vertical="top", wrap_text=True)
TOP = Alignment(vertical="top")
CENTRE = Alignment(vertical="center", horizontal="center")

DAYS_FMT = "0.0"
MONEY_FMT = "#,##0.00"
PCT_FMT = "0%"


def sheet(wb, name, widths, tab_color=None):
    """Add a worksheet with fixed column widths."""
    ws = wb.create_sheet(name)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    if tab_color:
        ws.sheet_properties.tabColor = tab_color
    ws.sheet_view.showGridLines = False
    return ws


def title(ws, row, text, subtitle=None, span=6):
    ws.cell(row=row, column=1, value=text).font = TITLE
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
    if subtitle:
        c = ws.cell(row=row + 1, column=1, value=subtitle)
        c.font = SUBTITLE
        c.alignment = WRAP
        ws.merge_cells(start_row=row + 1, start_column=1,
                       end_row=row + 1, end_column=span)
        ws.row_dimensions[row + 1].height = 28
        return row + 3
    return row + 2


def section(ws, row, text, span=6):
    ws.cell(row=row, column=1, value=text).font = SECTION
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
    return row + 1


def note(ws, row, text, span=6, height=None, font=None):
    c = ws.cell(row=row, column=1, value=text)
    c.font = font or BODY_MUTED
    c.alignment = WRAP
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
    if height:
        ws.row_dimensions[row].height = height
    return row + 1


def header_row(ws, row, labels, start_col=1):
    for i, label in enumerate(labels):
        c = ws.cell(row=row, column=start_col + i, value=label)
        c.font = HEADER
        c.fill = HEADER_FILL
        c.alignment = Alignment(vertical="center", wrap_text=True)
        c.border = GRID
    ws.row_dimensions[row].height = 30
    ws.freeze_panes = ws.cell(row=row + 1, column=1)
    return row + 1


def cell(ws, row, col, value, *, kind="derived", fmt=None, bold=False,
         align=None, band=False):
    """Write one cell in one of the three roles. Returns the cell."""
    c = ws.cell(row=row, column=col, value=value)
    c.font = BODY_BOLD if bold else BODY
    c.alignment = align or WRAP
    c.border = GRID
    if kind == "input":
        c.fill = INPUT_FILL
        c.border = INPUT_BORDER
    elif kind == "computed":
        c.fill = COMPUTED_FILL
    elif band:
        c.fill = BAND_FILL
    if fmt:
        c.number_format = fmt
    return c


def data_row(ws, row, values, *, kinds=None, fmts=None, band=False):
    """Write a whole row. `kinds` and `fmts` are per-column, None to default."""
    for i, value in enumerate(values):
        cell(ws, row, i + 1, value,
             kind=(kinds[i] if kinds else "derived"),
             fmt=(fmts[i] if fmts else None),
             band=band)
    return row + 1


def legend(ws, row, span=6):
    """The three-cell-role key, so a reader knows what they may edit."""
    row = section(ws, row, "How to read a cell", span=span)
    for label, kind, text in (
        ("INPUT", "input", "An assumption you own. Nothing in the source "
                           "documentation supplies this value — edit it."),
        ("DERIVED", "derived", "Read from solution-docs at build time. Editing it "
                               "here changes nothing upstream and is overwritten "
                               "on the next build."),
        ("COMPUTED", "computed", "A formula over the other two. Never type over it."),
    ):
        cell(ws, row, 1, label, kind=kind, bold=True, align=CENTRE)
        c = ws.cell(row=row, column=2, value=text)
        c.font = BODY_MUTED
        c.alignment = WRAP
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=span)
        ws.row_dimensions[row].height = 26
        row += 1
    return row + 1
