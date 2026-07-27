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

from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.data_source import AxDataSource, StrRef
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.marker import DataPoint, Marker
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.drawing.line import LineProperties
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

# --- chart palette ---------------------------------------------------------
# Categorical hues in FIXED slot order — a series takes the slot its entity
# owns, never the slot its rank happens to put it in, so a filtered-down chart
# does not repaint the survivors. This order is validated: worst adjacent-pair
# separation is dE 9.1 under protanopia and 22.9 in normal vision, both clear of
# the gates. Slots are not cycled — a fifth categorical series would be wrong
# here, not recoloured.
SERIES = ["2A78D6", "EB6834", "1BAF7A", "EDA100"]

# Milestones are an ORDERED sequence, not four unrelated things, so they get a
# single-hue ramp light-to-dark rather than categorical hues. Lightest step is
# held at 2:1 against the sheet so the first milestone does not dissolve.
ORDINAL = ["86B6EF", "5598E7", "2A78D6", "1C5CAB", "104281"]

# One hue for magnitude, one gray for "present but not the point".
MAGNITUDE = "2A78D6"
RECESSIVE = "B9C0C8"

GRID_LINE = "E3E7EB"
AXIS_LINE = "C8CFD6"

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

# Excel auto-fits row height for a wrapped cell — but never for a *merged* one.
# Every long note in these workbooks is merged, so height has to be computed or
# the text silently clips to one line. Deriving it here rather than passing a
# number at each call site means a new note cannot forget.
_LINE_H = 14.0          # points per wrapped line at 10–10.5pt Calibri
_PAD = 4.0
DEFAULT_WIDTH = 8.43


def span_width(ws, span, start_col=1):
    """Total character width of `span` columns, as Excel counts width."""
    total = 0.0
    for col in range(start_col, start_col + span):
        dim = ws.column_dimensions.get(get_column_letter(col))
        total += (dim.width if dim is not None and dim.width else DEFAULT_WIDTH)
    return total


def wrapped_height(ws, text, span, start_col=1):
    """Row height that fits `text` wrapped across `span` merged columns."""
    width = max(span_width(ws, span, start_col), 1.0)
    lines = 0
    for paragraph in str(text).split("\n"):
        lines += max(1, -(-len(paragraph) // int(width)))
    return max(15.0, lines * _LINE_H + _PAD)


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
        ws.row_dimensions[row + 1].height = wrapped_height(ws, subtitle, span)
        return row + 3
    return row + 2


def section(ws, row, text, span=6):
    ws.cell(row=row, column=1, value=text).font = SECTION
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
    return row + 1


def note(ws, row, text, span=6, height=None, font=None):
    """A merged, wrapped paragraph. Height is fitted unless one is forced."""
    c = ws.cell(row=row, column=1, value=text)
    c.font = font or BODY_MUTED
    c.alignment = WRAP
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
    ws.row_dimensions[row].height = height or wrapped_height(ws, text, span)
    return row + 1


def header_row(ws, row, labels, start_col=1, freeze=True):
    """A table header. `freeze` only for the header a reader scrolls under —
    a sheet with two tables must not freeze on the second, which would pin
    everything above it."""
    for i, label in enumerate(labels):
        c = ws.cell(row=row, column=start_col + i, value=label)
        c.font = HEADER
        c.fill = HEADER_FILL
        c.alignment = Alignment(vertical="center", wrap_text=True)
        c.border = GRID
    ws.row_dimensions[row].height = 30
    if freeze:
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
        ws.row_dimensions[row].height = wrapped_height(ws, text, span - 1,
                                                       start_col=2)
        row += 1
    return row + 1


def autofilter(ws, header_row_num, last_row, last_col):
    """Filter dropdowns over a table. Cheaper than an Excel Table object, which
    would impose its own banding on top of the styling already applied here."""
    ws.auto_filter.ref = (f"A{header_row_num}:"
                          f"{get_column_letter(last_col)}{last_row}")


# --- charts ----------------------------------------------------------------
# Every chart here reads cells on its own sheet. Those cells are formulas
# pointing at the model, so a chart is never a second copy of the numbers that
# can drift from the first — it is a view of them, and it moves when the
# assumptions do.

CHART_H = 7.6                       # cm
CHART_W = 15.5
_PT = 12700                         # EMU per point


def _stroke(colour, pt=1.0):
    return LineProperties(solidFill=colour, w=int(pt * _PT))


def _recede(axis, *, gridlines=False, fmt=None):
    """Axes and gridlines are scaffolding — visible, never competing with the
    marks. openpyxl defaults `delete` to True, which silently drops the axis."""
    axis.delete = False
    axis.majorTickMark = "none"
    axis.minorTickMark = "none"
    axis.spPr = GraphicalProperties(ln=_stroke(AXIS_LINE, 0.75))
    axis.majorGridlines = (
        ChartLines(spPr=GraphicalProperties(ln=_stroke(GRID_LINE, 0.75)))
        if gridlines else None)
    if fmt:
        axis.numFmt = fmt


def _labels(chart, fmt, position=None):
    chart.dLbls = DataLabelList()
    chart.dLbls.showVal = True
    chart.dLbls.showSerName = False
    chart.dLbls.showCatName = False
    chart.dLbls.showLegendKey = False
    chart.dLbls.numFmt = fmt
    if position:
        chart.dLbls.dLblPos = position


def ref(ws, col, row0, row1=None, col1=None):
    return Reference(ws, min_col=col, min_row=row0,
                     max_col=col1 or col, max_row=row1 or row0)


def _text_categories(chart, cats):
    """Point the category axis at a string reference.

    `set_categories` always emits a numeric reference. Excel recovers from that
    for labels that are plainly text, but a category range holding "M1" is not
    numeric data and saying so keeps the axis out of the reader's hands.
    """
    source = AxDataSource(strRef=StrRef(f=str(cats)))
    for series in chart.series:
        series.cat = source


def bar_chart(ws, anchor, *, title, data, cats, colours, stacked=False,
              percent=False, horizontal=False, fmt="0", labels=True,
              legend=True, height=CHART_H, width=CHART_W, y_title=None,
              emphasis=None, titles_from_data=True, text_cats=True):
    """A bar or column chart.

    `colours` assigns series in fixed slot order. `emphasis` instead colours a
    single series point by point — the form to reach for when one subset of the
    categories is the story and the rest are context.
    """
    chart = BarChart()
    chart.type = "bar" if horizontal else "col"
    if percent:
        chart.grouping, chart.overlap = "percentStacked", 100
    elif stacked:
        chart.grouping, chart.overlap = "stacked", 100
    else:
        chart.grouping, chart.overlap = "clustered", -12
    chart.gapWidth = 55
    chart.title = title
    chart.height, chart.width = height, width
    chart.add_data(data, titles_from_data=titles_from_data)
    chart.set_categories(cats)
    if text_cats:
        _text_categories(chart, cats)

    for i, series in enumerate(chart.series):
        # A 1.5pt sheet-coloured edge is the 2px gap between fills: it separates
        # stacked segments and neighbouring bars without drawing a box.
        series.graphicalProperties = GraphicalProperties(
            solidFill=colours[i % len(colours)], ln=_stroke(PAPER, 1.5))
    if emphasis:
        chart.series[0].data_points = [
            DataPoint(idx=i, spPr=GraphicalProperties(
                solidFill=colour, ln=_stroke(PAPER, 1.5)))
            for i, colour in enumerate(emphasis)]

    _recede(chart.x_axis)
    _recede(chart.y_axis, gridlines=True,
            fmt="0%" if percent else (fmt if not horizontal else fmt))
    if y_title:
        chart.y_axis.title = y_title
    if labels:
        _labels(chart, "0%" if percent else fmt,
                "ctr" if (stacked or percent) else "outEnd")
    # One series is named by the title; a legend box would only repeat it.
    if legend and len(chart.series) > 1:
        chart.legend.position = "b"
        chart.legend.overlay = False
    else:
        chart.legend = None
    ws.add_chart(chart, anchor)
    return chart


def line_chart(ws, anchor, *, title, data, cats, colours, fmt="0",
               height=CHART_H, width=CHART_W, x_title=None, y_title=None):
    chart = LineChart()
    chart.title = title
    chart.height, chart.width = height, width
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    for i, series in enumerate(chart.series):
        colour = colours[i % len(colours)]
        series.graphicalProperties = GraphicalProperties(ln=_stroke(colour, 2.0))
        series.smooth = False
        series.marker = Marker(symbol="circle", size=7)
        series.marker.graphicalProperties = GraphicalProperties(
            solidFill=colour, ln=_stroke(PAPER, 1.0))
    _recede(chart.x_axis)
    _recede(chart.y_axis, gridlines=True, fmt=fmt)
    if x_title:
        chart.x_axis.title = x_title
    if y_title:
        chart.y_axis.title = y_title
    if len(chart.series) > 1:
        chart.legend.position = "b"
        chart.legend.overlay = False
    else:
        chart.legend = None
    ws.add_chart(chart, anchor)
    return chart


KPI_LABEL = Font(name=BODY_FONT, size=9, bold=True, color=MUTED)
KPI_VALUE = Font(name=BODY_FONT, size=20, bold=True, color=INK)
KPI_FOOT = Font(name=BODY_FONT, size=9, color=MUTED)
KPI_FILL = PatternFill("solid", fgColor=BAND)


def kpi(ws, row, col, label, formula, footnote, *, fmt=DAYS_FMT, span=2):
    """A stat tile: one number, named, with the caveat that governs it.

    A single headline number is not a one-bar chart — it is a number. The tile
    is three stacked cells so the value can carry 20pt without the label
    inheriting it.
    """
    for offset, (text, font, height) in enumerate((
            (label, KPI_LABEL, 14), (formula, KPI_VALUE, 28),
            (footnote, KPI_FOOT, 26))):
        c = ws.cell(row=row + offset, column=col, value=text)
        c.font = font
        c.fill = KPI_FILL
        c.alignment = Alignment(vertical="center", horizontal="left",
                                wrap_text=offset == 2)
        for extra in range(1, span):
            ws.cell(row=row + offset, column=col + extra).fill = KPI_FILL
        ws.merge_cells(start_row=row + offset, start_column=col,
                       end_row=row + offset, end_column=col + span - 1)
        ws.row_dimensions[row + offset].height = height
    ws.cell(row=row + 1, column=col).number_format = fmt
    return row + 3
