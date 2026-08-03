"""Styling primitives for the stakeholder documents.

python-docx gives you Word's default template, which is Calibri 11 with blue
Cambria headings and a table style set nobody chose. A document that leaves the
repository should look like it was designed, so everything visual is set here
once and the document modules hold content only.

The palette is the one the decks use, so a document and a deck put in front of
the same audience do not look like they came from different organisations.
"""

from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Emu, Inches, Pt, RGBColor

# Palette — ink for body text, slate for secondary text, accent for headings and
# rules, wash for table header fills.
INK = RGBColor(0x1A, 0x1A, 0x1A)
SLATE = RGBColor(0x5A, 0x63, 0x6E)
ACCENT = RGBColor(0x0B, 0x4F, 0x71)
WASH = "E8EEF2"
BAND = "F5F8FA"
RULE = "C9D4DC"
# Warm tint, used only where a diagram has to mark the one span that differs
# from every other — a second colour earns its place only by carrying meaning.
FLAG = "F7E9D7"

BODY_FONT = "Calibri"
HEAD_FONT = "Calibri"

# Word's own page width less the margins set in `page_setup`, so a table asked
# to fill the text column gets an exact width rather than an approximation.
TEXT_WIDTH = Inches(8.27 - 1.6)
TEXT_WIDTH_TWIPS = int((8.27 - 1.6) * 1440)


def page_setup(doc):
    """A4 portrait with margins that leave a readable measure."""
    for section in doc.sections:
        section.page_width = Inches(8.27)
        section.page_height = Inches(11.69)
        section.top_margin = Inches(0.85)
        section.bottom_margin = Inches(0.85)
        section.left_margin = Inches(0.8)
        section.right_margin = Inches(0.8)
        section.header_distance = Inches(0.4)
        section.footer_distance = Inches(0.4)


def base_styles(doc):
    """Override the template defaults every style inherits from."""
    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = INK
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.12
    # East-Asian font mapping, or Word substitutes its own for anything the
    # Latin run does not cover.
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)

    for name, size, color, bold in (
        ("Heading 1", 17, ACCENT, True),
        ("Heading 2", 13, ACCENT, True),
        ("Heading 3", 11, INK, True),
    ):
        style = doc.styles[name]
        style.font.name = HEAD_FONT
        style.font.size = Pt(size)
        style.font.color.rgb = color
        style.font.bold = bold
        style.font.italic = False
        style.paragraph_format.space_before = Pt(14 if name == "Heading 1" else 10)
        style.paragraph_format.space_after = Pt(4)
        style.paragraph_format.keep_with_next = True


def _shade(cell, hex_fill):
    shd = cell._tc.get_or_add_tcPr().makeelement(qn("w:shd"), {})
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    cell._tc.get_or_add_tcPr().append(shd)


def _borders(table):
    """Horizontal rules only — vertical lines make a dense table read as a grid."""
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.makeelement(qn("w:tblBorders"), {})
    for edge in ("top", "bottom", "insideH"):
        el = borders.makeelement(qn(f"w:{edge}"), {})
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "6")
        el.set(qn("w:color"), RULE)
        borders.append(el)
    for edge in ("left", "right", "insideV"):
        el = borders.makeelement(qn(f"w:{edge}"), {})
        el.set(qn("w:val"), "none")
        borders.append(el)
    tbl_pr.append(borders)


def _fixed_widths(table, shares):
    """Pin column widths to the given proportions of the text column.

    Setting `cell.width` alone is advisory: Word and LibreOffice both fall back
    to auto-fitting on content unless the table declares a fixed layout, an
    explicit total width, and a grid. Without all three, a column carrying long
    text steals width from its neighbours and the proportions asked for here are
    silently ignored.
    """
    total = sum(shares)
    twips = [int(TEXT_WIDTH_TWIPS * share / total) for share in shares]

    tbl_pr = table._tbl.tblPr

    layout = tbl_pr.makeelement(qn("w:tblLayout"), {})
    layout.set(qn("w:type"), "fixed")
    tbl_pr.append(layout)

    width = tbl_pr.makeelement(qn("w:tblW"), {})
    width.set(qn("w:w"), str(sum(twips)))
    width.set(qn("w:type"), "dxa")
    tbl_pr.append(width)

    grid = table._tbl.find(qn("w:tblGrid"))
    for col, value in zip(grid.findall(qn("w:gridCol")), twips):
        col.set(qn("w:w"), str(value))

    for column, value in zip(table.columns, twips):
        for cell in column.cells:
            cell.width = Emu(int(value * 914400 / 1440))


def _repeat_header(row):
    """Mark a row as a header so Word repeats it across a page break."""
    tr_pr = row._tr.get_or_add_trPr()
    el = tr_pr.makeelement(qn("w:tblHeader"), {})
    el.set(qn("w:val"), "true")
    tr_pr.append(el)


def _keep_whole(row):
    """Stop a row splitting across a page break.

    A plan read across a break loses the association between a gate and the
    evidence that closes it, which is the only thing the row is carrying.
    """
    tr_pr = row._tr.get_or_add_trPr()
    el = tr_pr.makeelement(qn("w:cantSplit"), {})
    el.set(qn("w:val"), "true")
    tr_pr.append(el)


def heading(doc, text, level=1):
    doc.add_heading(text, level=level)


def para(doc, text, *, size=10.5, color=INK, bold=False, italic=False,
         space_after=6, align=None):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.size = Pt(size)
    run.font.color.rgb = color
    run.bold = bold
    run.italic = italic
    p.paragraph_format.space_after = Pt(space_after)
    if align is not None:
        p.alignment = align
    return p


def bullets(doc, items, *, style="List Bullet"):
    for item in items:
        p = doc.add_paragraph(style=style)
        _rich(p, item)
        p.paragraph_format.space_after = Pt(3)


def checklist(doc, items):
    """An open checkbox per line — the document is meant to be worked through."""
    for item in items:
        p = doc.add_paragraph()
        # Hanging indent, so a wrapped line aligns with the text after the box
        # rather than under the box itself.
        p.paragraph_format.left_indent = Inches(0.47)
        p.paragraph_format.first_line_indent = Inches(-0.25)
        p.paragraph_format.space_after = Pt(3)
        box = p.add_run("☐  ")
        box.font.size = Pt(11)
        box.font.color.rgb = ACCENT
        # Calibri has no ballot box, so Word would substitute a font of its own
        # choosing and the boxes would not match each other across platforms.
        box.font.name = "Segoe UI Symbol"
        box.element.rPr.rFonts.set(qn("w:eastAsia"), "Segoe UI Symbol")
        box.element.rPr.rFonts.set(qn("w:cs"), "Segoe UI Symbol")
        _rich(p, item)


def _rich(paragraph, text):
    """Bold the run between ** markers; everything else is plain."""
    for index, chunk in enumerate(text.split("**")):
        if not chunk:
            continue
        run = paragraph.add_run(chunk)
        run.bold = index % 2 == 1
        run.font.size = Pt(10.5)


def table(doc, headers, rows, widths=None, *, font=9.5, band=True, vmerge=()):
    """A banded table with a filled header row and horizontal rules only.

    `vmerge` is a list of (column, first_data_row, span) runs to merge
    vertically — the shape a grouped table needs when one label covers several
    rows. Merging happens before any text is written, because merging two cells
    that already hold paragraphs keeps both and leaves the group padded with
    blank lines.
    """
    t = doc.add_table(rows=1, cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    _borders(t)

    header_cells = t.rows[0].cells
    for cell, text in zip(header_cells, headers):
        cell.text = ""
        p = cell.paragraphs[0]
        run = p.add_run(text)
        run.bold = True
        run.font.size = Pt(font)
        run.font.color.rgb = ACCENT
        p.paragraph_format.space_before = Pt(3)
        p.paragraph_format.space_after = Pt(3)
        _shade(cell, WASH)
    _repeat_header(t.rows[0])

    for _ in rows:
        _keep_whole(t.add_row())

    _fixed_widths(t, widths or [1] * len(headers))

    swallowed = set()
    for column, first, span in vmerge:
        top = t.rows[first + 1].cells[column]
        top.merge(t.rows[first + span].cells[column])
        swallowed.update((r, column) for r in range(first + 1, first + span))

    for r, row in enumerate(rows):
        cells = t.rows[r + 1].cells
        for c, text in enumerate(row):
            if (r, c) in swallowed:
                continue
            cell = cells[c]
            cell.text = ""
            p = cell.paragraphs[0]
            _rich(p, str(text))
            for run in p.runs:
                run.font.size = Pt(font)
            p.paragraph_format.space_before = Pt(2.5)
            p.paragraph_format.space_after = Pt(2.5)
            if band and r % 2 == 1:
                _shade(cell, BAND)

    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def _valign(cell, value="center"):
    el = cell._tc.get_or_add_tcPr().makeelement(qn("w:vAlign"), {})
    el.set(qn("w:val"), value)
    cell._tc.get_or_add_tcPr().append(el)


def _plain_cell(cell, text, *, size=8.5, bold=False, fill=None, color=INK):
    cell.text = ""
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run(text)
    run.font.size = Pt(size)
    run.bold = bold
    run.font.color.rgb = color
    _valign(cell)
    if fill:
        _shade(cell, fill)


def flow(doc, steps, *, note=None):
    """A left-to-right chain of labelled boxes joined by arrows.

    Drawn as a table rather than as artwork so it stays editable, reflows with
    the page and carries no rasterised image the reader cannot correct.
    """
    columns = len(steps) * 2 - 1
    t = doc.add_table(rows=1, cols=columns)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False

    cells = t.rows[0].cells
    for index, step in enumerate(steps):
        _plain_cell(cells[index * 2], step, bold=True, fill=WASH)
    for index in range(len(steps) - 1):
        _plain_cell(cells[index * 2 + 1], "▶", size=9, color=ACCENT)

    shares = []
    for index in range(columns):
        shares.append(5 if index % 2 == 0 else 1)
    _fixed_widths(t, shares)

    if note:
        para(doc, note, size=8.5, color=SLATE, space_after=8,
             align=WD_ALIGN_PARAGRAPH.CENTER)
    else:
        doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def band(doc, labels, groups):
    """A row of labelled cells over merged spans that classify them.

    `groups` is a list of (span, text, fill) covering the labels left to right —
    the shape a roadmap needs when the point is which stages share a property.
    """
    t = doc.add_table(rows=2, cols=len(labels))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False

    for cell, label in zip(t.rows[0].cells, labels):
        _plain_cell(cell, label, size=9, bold=True, fill=WASH)

    # Widths first: once cells are merged, the column view no longer maps one
    # cell to one column and the assignment below would size the merged span as
    # though it were a single column.
    _fixed_widths(t, [1] * len(labels))

    start = 0
    for span, text, fill in groups:
        cells = t.rows[1].cells
        merged = cells[start] if span == 1 else cells[start].merge(
            cells[start + span - 1])
        _plain_cell(merged, text, size=8.5, fill=fill, color=ACCENT, bold=True)
        start += span
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def callout(doc, text):
    """A single-cell shaded block for something the reader must not skim past."""
    t = doc.add_table(rows=1, cols=1)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    cell = t.rows[0].cells[0]
    cell.text = ""
    p = cell.paragraphs[0]
    _rich(p, text)
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(6)
    _shade(cell, WASH)
    _borders(t)
    _fixed_widths(t, [1])
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def title_block(doc, title, subtitle, meta_lines):
    p = doc.add_paragraph()
    run = p.add_run(title)
    run.font.name = HEAD_FONT
    run.font.size = Pt(24)
    run.font.bold = True
    run.font.color.rgb = ACCENT
    p.paragraph_format.space_after = Pt(2)

    p = doc.add_paragraph()
    run = p.add_run(subtitle)
    run.font.size = Pt(12)
    run.font.color.rgb = SLATE
    p.paragraph_format.space_after = Pt(10)

    for line in meta_lines:
        para(doc, line, size=9, color=SLATE, space_after=1)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def footer_note(doc, text):
    footer = doc.sections[0].footer
    p = footer.paragraphs[0]
    p.text = ""
    run = p.add_run(text)
    run.font.size = Pt(8)
    run.font.color.rgb = SLATE
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
