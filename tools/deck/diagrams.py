"""Native, editable diagram geometry shared by every deck.

Each function draws one diagram into an already-created slide and returns
nothing — the caller owns the title, kicker, footer, captions and speaker notes,
because those are where audiences differ. The geometry is the expensive part and
is written once here.

All shapes are real PowerPoint geometry with live text frames, per
solution-docs/40-sales-deck/VISUAL-DIRECTION.md §9 — no rasterised artwork.
"""

from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

from lib import (
    ACCENT, ACCENT_DIM, ALLOW, BG, BLOCK, BODY_BOTTOM, BODY_Y, CONTENT_W, HAIRLINE,
    LOG_ONLY, MARGIN, MUTED, SPLIT_L_W, SPLIT_L_X, SPLIT_R_W, SPLIT_R_X, SURFACE,
    SURFACE_ALT, TEXT, WARN, arrow, bar_chart, box, bullets, caption, chevron,
    edge_label, panel, para, slide_base, textbox,
)


def topology_slide(prs, title, kicker, footer, topology, exposure, exposure_color):
    """A deployment-mode topology slide: diagram plus its exposure statement.

    Returns the slide so the caller can attach audience-specific speaker notes.
    """
    s = slide_base(prs, title=title, kicker=kicker, footer=footer)
    topology(s)
    exposure_panel(s, exposure, exposure_color)
    return s


def logical_architecture(s):
    """Control plane over runtime over evaluation engines, with entry points."""
    box(s, 4.55, BODY_Y + 0.05, 4.25, 1.05,
        "Guardrails Control Plane\nTenants · Workloads · Policies · Bindings · Rollouts",
        bold=True, size=14)
    arrow(s, 6.675, BODY_Y + 1.10, 6.675, BODY_Y + 1.72)
    edge_label(s, 6.75, BODY_Y + 1.22, 2.6, "policy distribution (async)",
               align=PP_ALIGN.LEFT)

    ep_y = BODY_Y + 1.80
    for i, name in enumerate(["LiteLLM Connector", "Generic SDK", "Guarded Gateway"]):
        box(s, MARGIN + 0.15, ep_y + i * 0.72, 2.45, 0.55, name, size=12)
        arrow(s, MARGIN + 2.62, ep_y + i * 0.72 + 0.275, 4.50, BODY_Y + 2.28)
    edge_label(s, MARGIN + 0.15, ep_y + 2.24, 2.45, "entry points — evaluation calls")

    box(s, 4.55, BODY_Y + 1.75, 4.25, 1.45,
        "Guardrails Runtime\nEvaluation API · Policy resolver · Decision engine · "
        "Evaluator adapters · Audit producer", bold=True, size=14)

    arrow(s, 8.85, BODY_Y + 2.47, 9.75, BODY_Y + 2.47)
    box(s, 9.78, BODY_Y + 1.75, 2.80, 1.45,
        "Evaluation engines\nBedrock Guardrails · Deterministic rules · PII evaluators · "
        "Schema validators", bold=True, size=13)

    arrow(s, 6.675, BODY_Y + 3.22, 6.675, BODY_Y + 3.72, color=HAIRLINE)
    box(s, 4.55, BODY_Y + 3.74, 4.25, 0.48, "Audit events → Amazon SQS → Amazon S3",
        size=11.5, fill=SURFACE_ALT, color=MUTED)


def policy_vs_connector(s):
    """The load-bearing split: what is evaluated vs how a gateway invokes it."""
    box(s, MARGIN, BODY_Y + 0.15, 5.55, 0.62, "POLICY", bold=True, size=13,
        fill=ACCENT_DIM, border=ACCENT, color=TEXT)
    panel(s, MARGIN, BODY_Y + 0.80, 5.55, 2.25)
    _, tf = textbox(s, MARGIN + 0.30, BODY_Y + 1.05, 4.95, 1.9)
    para(tf, "What must be evaluated, and how violations are handled.", size=14,
         first=True, space_after=12)
    para(tf, "“Detect prompt injection and block it.”", size=16, color=ACCENT,
         bold=True, space_after=12)
    para(tf, "Expressible and testable without reference to any connector. Carries no "
             "integration mechanics.", size=12, color=MUTED, space_after=0)

    box(s, 7.03, BODY_Y + 0.15, 5.55, 0.62, "CONNECTOR", bold=True, size=13,
        fill=SURFACE, border=HAIRLINE, color=TEXT)
    panel(s, 7.03, BODY_Y + 0.80, 5.55, 2.25)
    _, tf = textbox(s, 7.33, BODY_Y + 1.05, 4.95, 1.9)
    para(tf, "How one specific gateway invokes and enforces that policy.", size=14,
         first=True, space_after=12)
    para(tf, "“Invoke the Evaluation API during LiteLLM pre-call execution.”",
         size=16, color=TEXT, bold=True, space_after=12)
    para(tf, "Carries no policy logic, thresholds, or detection rules. A new gateway means "
             "a new connector — never a new policy.", size=12, color=MUTED, space_after=0)

    sep = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(6.62), Inches(BODY_Y + 0.15),
                             Inches(0.02), Inches(2.90))
    sep.fill.solid()
    sep.fill.fore_color.rgb = HAIRLINE
    sep.line.fill.background()
    sep.shadow.inherit = False


def mode_columns(s, modes, highlight=0):
    """Three side-by-side mode cards. `highlight` accents one column."""
    cw, gap_x = 3.79, 0.22
    for i, (tag, name, who, how, fit) in enumerate(modes):
        x = MARGIN + i * (cw + gap_x)
        hi = i == highlight
        panel(s, x, BODY_Y + 0.05, cw, 4.05)
        box(s, x, BODY_Y + 0.05, cw, 0.52, f"{tag} — {name}", bold=True, size=13,
            fill=ACCENT_DIM if hi else SURFACE, border=ACCENT if hi else HAIRLINE)
        _, tf = textbox(s, x + 0.22, BODY_Y + 0.72, cw - 0.44, 3.2)
        para(tf, who, size=14, bold=True, first=True, space_after=10, line=1.2)
        para(tf, how, size=11.5, color=MUTED, space_after=10)
        para(tf, "Best fit", size=10, color=ACCENT, bold=True, space_after=3)
        para(tf, fit, size=11.5, color=MUTED, space_after=0)


def enforcement_ladder(s, states, highlight=1, y=BODY_Y + 0.30):
    """Overlapping chevrons for the enforcement states, one accented."""
    cw, ov = 2.55, 0.30
    for i, (name, desc, col) in enumerate(states):
        x = MARGIN + i * (cw - ov)
        hi = i == highlight
        chevron(s, x, y, cw, 1.05, name,
                fill=ACCENT_DIM if hi else SURFACE,
                border=col, color=TEXT, size=15)
        _, tf = textbox(s, x + 0.42, y + 1.25, cw - 0.75, 1.0, align=PP_ALIGN.CENTER)
        para(tf, desc, size=11.5, color=MUTED, first=True, space_after=0,
             align=PP_ALIGN.CENTER, line=1.2)


def phase_roadmap(s, phases, highlight=0, here_label="WE ARE HERE"):
    """Five gated phase cards left to right, with a 'you are here' marker."""
    pw, pgap = 2.23, 0.16
    for i, (tag, name, obj, mode) in enumerate(phases):
        x = MARGIN + i * (pw + pgap)
        hi = i == highlight
        panel(s, x, BODY_Y + 0.45, pw, 2.95,
              fill=SURFACE if hi else SURFACE_ALT,
              border=ACCENT if hi else HAIRLINE)
        _, tf = textbox(s, x + 0.20, BODY_Y + 0.66, pw - 0.40, 2.55)
        para(tf, tag, size=11.5, color=ACCENT if hi else MUTED, bold=True, first=True,
             space_after=4)
        para(tf, name, size=14, bold=True, space_after=8, line=1.15)
        para(tf, obj, size=11, color=MUTED, space_after=8, line=1.2)
        para(tf, mode, size=10.5, color=ACCENT if hi else MUTED, bold=True, space_after=0)
        if i < len(phases) - 1:
            arrow(s, x + pw + 0.02, BODY_Y + 1.90, x + pw + pgap - 0.02, BODY_Y + 1.90,
                  color=HAIRLINE, width=1.5)
            edge_label(s, x + pw - 0.24, BODY_Y + 3.47, 0.80, "gate", size=8.5)

    if here_label:
        box(s, MARGIN + highlight * (pw + pgap), BODY_Y + 0.02, 2.28, 0.38, here_label,
            bold=True, size=10, fill=ACCENT, border=None, color=BG)


def numbered_panels(s, items, y0=BODY_Y + 0.10, gap=1.42, h=1.24):
    """Full-width panels each led by a large muted number."""
    for i, (num, head, body) in enumerate(items):
        y = y0 + i * gap
        panel(s, MARGIN, y, CONTENT_W, h)
        _, tf = textbox(s, MARGIN + 0.30, y + 0.24, 0.9, 0.8)
        para(tf, num, size=26, color=ACCENT_DIM, bold=True, first=True, space_after=0)
        _, tf = textbox(s, MARGIN + 1.15, y + 0.20, CONTENT_W - 1.55, 0.95)
        para(tf, head, size=15.5, bold=True, first=True, space_after=5)
        para(tf, body, size=12, color=MUTED, space_after=0)


def onboarding_stepper(s, steps, y=BODY_Y + 0.35):
    """Numbered step boxes joined by arrows; the last step is accented."""
    sw, sgap = 1.55, 0.14
    for i, label in enumerate(steps):
        x = MARGIN + i * (sw + sgap)
        last = i == len(steps) - 1
        box(s, x, y, sw, 1.35, label, size=12, bold=True,
            fill=SURFACE, border=ACCENT if last else HAIRLINE)
        _, tf = textbox(s, x, y - 0.25, sw, 0.25, align=PP_ALIGN.CENTER)
        para(tf, str(i + 1), size=10.5, color=MUTED, bold=True, first=True,
             space_after=0, align=PP_ALIGN.CENTER)
        if not last:
            arrow(s, x + sw + 0.01, y + 0.67, x + sw + sgap - 0.01, y + 0.67,
                  color=HAIRLINE, width=1.5)


def posture_rows(s, postures, badge="PHASES 1–2 START HERE"):
    """One row per trust posture: topology, who can bypass, when it fits."""
    for i, (name, topo, bypass, when, col, current) in enumerate(postures):
        y = BODY_Y + 0.15 + i * 1.42
        panel(s, MARGIN, y, CONTENT_W, 1.25)
        marker = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(MARGIN), Inches(y),
                                    Inches(0.07), Inches(1.25))
        marker.fill.solid()
        marker.fill.fore_color.rgb = col
        marker.line.fill.background()
        marker.shadow.inherit = False

        _, tf = textbox(s, MARGIN + 0.32, y + 0.16, 3.55, 1.0)
        para(tf, name, size=14.5, bold=True, first=True, space_after=4)
        para(tf, topo, size=10.5, color=MUTED, space_after=0, line=1.15)
        if current and badge:
            box(s, MARGIN + 0.32, y + 0.92, 1.85, 0.26, badge, size=8.5,
                bold=True, fill=ACCENT, border=None, color=BG)

        _, tf = textbox(s, MARGIN + 4.15, y + 0.16, 3.55, 1.0)
        para(tf, "Who can bypass it", size=9.5, color=ACCENT, bold=True, first=True,
             space_after=3)
        para(tf, bypass, size=11.5, color=TEXT, space_after=0, line=1.2)

        _, tf = textbox(s, MARGIN + 8.00, y + 0.16, 3.80, 1.0)
        para(tf, "Appropriate when", size=9.5, color=ACCENT, bold=True, first=True,
             space_after=3)
        para(tf, when, size=11.5, color=MUTED, space_after=0, line=1.2)


def scope_columns(s, is_items, is_not_items):
    """Side-by-side 'what it is' / 'what it is not' columns."""
    panel(s, MARGIN, BODY_Y + 0.05, 5.95, 4.30)
    box(s, MARGIN, BODY_Y + 0.05, 5.95, 0.55, "WHAT IT IS", bold=True, size=12.5,
        fill=ACCENT_DIM, border=ACCENT)
    bullets(s, MARGIN + 0.28, BODY_Y + 0.90, 5.45, is_items, size=12.5, gap=0.70,
            dot=ALLOW)

    panel(s, 7.13, BODY_Y + 0.05, 5.45, 4.30)
    box(s, 7.13, BODY_Y + 0.05, 5.45, 0.55, "WHAT IT IS NOT", bold=True, size=12.5,
        fill=SURFACE, border=BLOCK)
    bullets(s, 7.41, BODY_Y + 0.85, 4.95, is_not_items, size=12.5, gap=0.62, dot=BLOCK)


def context_diagram(s, consumers, dependencies, core, exclusions,
                    exclusions_title="Outside the boundary, permanently",
                    consumers_label="CONSUMERS", dependencies_label="DEPENDENCIES"):
    """Consumers on the left, the platform in the middle, dependencies on the
    right, over a red-bordered statement of what stays outside the boundary."""
    panel(s, MARGIN, BODY_Y + 0.10, 3.55, 3.30, fill=SURFACE_ALT)
    caption(s, MARGIN + 0.25, BODY_Y + 0.26, 3.1, consumers_label, size=10, color=MUTED)
    for i, name in enumerate(consumers):
        box(s, MARGIN + 0.28, BODY_Y + 0.66 + i * 0.86, 3.0, 0.70, name, size=11.5)

    box(s, 4.75, BODY_Y + 0.72, 3.85, 2.10, core, bold=True, size=14, border=ACCENT)
    arrow(s, 4.05, BODY_Y + 1.60, 4.72, BODY_Y + 1.60)
    arrow(s, 8.62, BODY_Y + 1.35, 9.55, BODY_Y + 1.35)

    panel(s, 9.58, BODY_Y + 0.10, 3.00, 3.30, fill=SURFACE_ALT)
    caption(s, 9.80, BODY_Y + 0.26, 2.6, dependencies_label, size=10, color=MUTED)
    for i, name in enumerate(dependencies):
        box(s, 9.80, BODY_Y + 0.66 + i * 0.86, 2.55, 0.70, name, size=11.5,
            fill=SURFACE_ALT)

    panel(s, MARGIN, BODY_BOTTOM - 1.18, CONTENT_W, 1.08, fill=SURFACE_ALT,
          border=BLOCK)
    _, tf = textbox(s, MARGIN + 0.32, BODY_BOTTOM - 1.02, CONTENT_W - 0.64, 0.85)
    para(tf, exclusions_title, size=11, color=BLOCK, bold=True, first=True,
         space_after=4)
    para(tf, exclusions, size=12, color=MUTED, space_after=0)


def segmented_identifier(s, template, segments, x=MARGIN + 1.55, y=BODY_Y + 0.20,
                         w=6.6, seg_w=2.2):
    """One identifier format shown whole, with each segment annotated beneath."""
    box(s, x, y, w, 0.82, template, bold=True, size=20, fill=ACCENT_DIM,
        border=ACCENT)
    for i, (head, sub) in enumerate(segments):
        _, tf = textbox(s, x + i * seg_w, y + 0.92, seg_w, 0.9, align=PP_ALIGN.CENTER)
        para(tf, head, size=11, color=ACCENT, bold=True, first=True, space_after=3,
             align=PP_ALIGN.CENTER)
        para(tf, sub, size=10, color=MUTED, space_after=0, align=PP_ALIGN.CENTER,
             line=1.15)


def seam_cards(s, seams, highlight=None, y=BODY_Y + 0.10, h=2.05, gap=0.19):
    """A row of equal cards, one per extension seam. `highlight` accents one."""
    n = len(seams)
    cw = (CONTENT_W - gap * (n - 1)) / n
    for i, (name, desc) in enumerate(seams):
        x = MARGIN + i * (cw + gap)
        hi = i == highlight
        panel(s, x, y, cw, h)
        box(s, x, y, cw, 0.86, name, bold=True, size=13,
            fill=ACCENT_DIM if hi else SURFACE,
            border=ACCENT if hi else HAIRLINE)
        _, tf = textbox(s, x + 0.20, y + 0.98, cw - 0.40, h - 1.05)
        para(tf, desc, size=11, color=MUTED, first=True, space_after=0, line=1.2)


def store_split(s, left, right, y=BODY_Y + 0.10, h=0.90, tail=0.50):
    """Two stores side by side, with a short stem down into whatever follows."""
    box(s, SPLIT_L_X, y, SPLIT_L_W, h, left, size=12.5, bold=True)
    box(s, SPLIT_R_X, y, SPLIT_R_W, h, right, size=12.5, bold=True)
    mid = (SPLIT_L_X + SPLIT_L_W + SPLIT_R_X) / 2
    arrow(s, mid, y + h + 0.15, mid, y + h + 0.15 + tail, color=HAIRLINE)


def pull_model(s, left, right, rules, y=BODY_Y + 0.10):
    """The two stores over the rules that govern how a runtime reads them."""
    store_split(s, left, right, y=y, h=0.90, tail=0.42)
    ry = y + 1.55
    n = len(rules)
    gap = 0.22
    cw = (CONTENT_W - gap * (n - 1)) / n
    for i, (head, sub) in enumerate(rules):
        x = MARGIN + i * (cw + gap)
        panel(s, x, ry, cw, 1.60)
        _, tf = textbox(s, x + 0.22, ry + 0.20, cw - 0.44, 1.25)
        para(tf, head, size=13, bold=True, first=True, space_after=6, line=1.15)
        para(tf, sub, size=11, color=MUTED, space_after=0, line=1.2)


def clamp_pipeline(s, stages, bypass_label, bypass_note, y=BODY_Y + 0.40, h=1.00,
                   gap=0.34):
    """The clamping path left to right, over the lane that bypasses it.

    `stages` is a list of (name, note, accented) — the accented stage is the
    clamp itself. The lane underneath carries everything the ceiling never
    touches, which is the part most often misread as escalation.
    """
    n = len(stages)
    bw = (CONTENT_W - gap * (n - 1)) / n
    for i, (name, note, hi) in enumerate(stages):
        x = MARGIN + i * (bw + gap)
        box(s, x, y, bw, h, name, bold=True, size=13,
            fill=ACCENT_DIM if hi else SURFACE,
            border=ACCENT if hi else HAIRLINE)
        _, tf = textbox(s, x + 0.06, y + h + 0.10, bw - 0.12, 0.70,
                        align=PP_ALIGN.CENTER)
        para(tf, note, size=10.5, color=MUTED, first=True, space_after=0,
             align=PP_ALIGN.CENTER, line=1.15)
        if i < n - 1:
            arrow(s, x + bw + 0.02, y + h / 2, x + bw + gap - 0.02, y + h / 2,
                  color=HAIRLINE, width=1.5)

    lane_y = y + h + 0.90
    panel(s, MARGIN, lane_y, CONTENT_W, 0.62, fill=SURFACE_ALT)
    _, tf = textbox(s, MARGIN + 0.28, lane_y + 0.13, 4.4, 0.40)
    para(tf, bypass_label, size=11.5, color=ALLOW, bold=True, first=True,
         space_after=0)
    arrow(s, MARGIN + 4.85, lane_y + 0.31, MARGIN + 6.55, lane_y + 0.31,
          color=ALLOW, width=1.5, dashed=True)
    _, tf = textbox(s, MARGIN + 6.75, lane_y + 0.13, CONTENT_W - 7.05, 0.40)
    para(tf, bypass_note, size=11.5, color=MUTED, first=True, space_after=0)


def ask_columns(s, headline, headline_sub, asking, not_asking,
                asking_title="And, before this session closes",
                not_asking_title="Not in scope for this session",
                y=BODY_Y - 0.05):
    """The closing ask: one banner statement over two contrasting lists."""
    panel(s, MARGIN, y, CONTENT_W, 1.55, fill=SURFACE, border=ACCENT)
    _, tf = textbox(s, MARGIN + 0.40, y + 0.25, CONTENT_W - 0.8, 1.05)
    para(tf, headline, size=18, bold=True, first=True, space_after=6, line=1.2)
    para(tf, headline_sub, size=12.5, color=MUTED, space_after=0)

    _, tf = textbox(s, MARGIN, y + 1.67, SPLIT_L_W + 0.10, 0.4)
    para(tf, asking_title, size=13, color=ACCENT, bold=True, first=True, space_after=0)
    bullets(s, MARGIN, y + 2.10, SPLIT_L_W + 0.10, asking, size=12.5, gap=0.62,
            dot=ALLOW)

    _, tf = textbox(s, SPLIT_R_X - 0.10, y + 1.67, SPLIT_R_W + 0.10, 0.4)
    para(tf, not_asking_title, size=13, color=MUTED, bold=True, first=True,
         space_after=0)
    bullets(s, SPLIT_R_X - 0.10, y + 2.10, SPLIT_R_W + 0.10, not_asking, size=12.5,
            gap=0.62, dot=LOG_ONLY)


def contract_change_classes(s, y=BODY_Y + 0.25, h=3.30):
    """What each kind of contract change costs, counted from the ADR's own table.

    The point of the chart is the shape, not the totals: five of the seven
    change kinds force a new major, so the contract is deliberately expensive
    to widen.
    """
    bar_chart(s, MARGIN, y, CONTENT_W, h,
              categories=["Major — new prefix,\nplus bind-time refusal",
                          "Major — new path prefix",
                          "Minor — no client change"],
              values=[1, 4, 2],
              colors=[BLOCK, WARN, ALLOW])


def entity_grid(s, entities):
    """Two-column grid of domain entities."""
    for i, (name, desc) in enumerate(entities):
        col, row = i % 2, i // 2
        x = MARGIN + col * (CONTENT_W / 2 + 0.10)
        y = BODY_Y + 0.10 + row * 1.42
        w = CONTENT_W / 2 - 0.10
        panel(s, x, y, w, 1.25)
        _, tf = textbox(s, x + 0.28, y + 0.18, w - 0.56, 1.0)
        para(tf, name, size=14, color=ACCENT, bold=True, first=True, space_after=5)
        para(tf, desc, size=11.5, color=MUTED, space_after=0, line=1.2)


# --- mode topologies -------------------------------------------------------


def exposure_panel(s, text, color):
    """The content-exposure statement that closes every topology slide."""
    panel(s, MARGIN, BODY_BOTTOM - 1.22, CONTENT_W, 1.12, fill=SURFACE_ALT)
    _, tf = textbox(s, MARGIN + 0.30, BODY_BOTTOM - 1.06, CONTENT_W - 0.6, 0.9)
    para(tf, "Content exposure", size=10.5, color=ACCENT, bold=True, first=True,
         space_after=4)
    para(tf, text, size=12.5, color=color, space_after=0)


def mode_a_topology(s):
    """Team-owned gateway calling a hosted evaluation runtime."""
    panel(s, MARGIN, BODY_Y + 0.10, 6.85, 3.05, fill=SURFACE_ALT)
    caption(s, MARGIN + 0.25, BODY_Y + 0.25, 4.0, "TEAM'S OWN ENVIRONMENT", size=10,
            color=MUTED)
    panel(s, 8.90, BODY_Y + 0.10, 3.68, 3.05, fill=SURFACE_ALT)
    caption(s, 9.12, BODY_Y + 0.25, 3.3, "AI GUARDRAILS HOSTED ACCOUNT", size=10,
            color=MUTED)

    box(s, 1.20, BODY_Y + 0.72, 1.90, 0.58, "Application", size=12)
    arrow(s, 3.12, BODY_Y + 1.01, 3.58, BODY_Y + 1.01)
    box(s, 3.60, BODY_Y + 0.62, 3.55, 2.05,
        "Team-owned LiteLLM\n(or equivalent gateway)\n\nHolds the team's own "
        "model-provider credentials", size=13, bold=True)
    box(s, 1.20, BODY_Y + 1.98, 1.90, 0.58, "Model provider", size=12,
        fill=SURFACE_ALT)
    arrow(s, 3.58, BODY_Y + 2.27, 3.12, BODY_Y + 2.27, color=HAIRLINE)

    box(s, 9.30, BODY_Y + 0.88, 3.05, 1.50,
        "Hosted Guardrails Runtime\nEvaluation API · policy execution · detection "
        "engines", size=13, bold=True, border=ACCENT)
    arrow(s, 7.17, BODY_Y + 1.05, 9.28, BODY_Y + 1.32)
    edge_label(s, 7.55, BODY_Y + 0.58, 1.30, "input\nevaluation", size=9.5)
    arrow(s, 7.17, BODY_Y + 2.15, 9.28, BODY_Y + 1.94)
    edge_label(s, 7.55, BODY_Y + 2.26, 1.30, "output\nevaluation", size=9.5)


def mode_b_topology(s):
    """One provider-operated endpoint carrying guardrails and inference."""
    panel(s, MARGIN, BODY_Y + 0.10, 3.35, 3.05, fill=SURFACE_ALT)
    caption(s, MARGIN + 0.25, BODY_Y + 0.25, 3.0, "CONSUMING TEAM", size=10, color=MUTED)
    panel(s, 4.55, BODY_Y + 0.10, 8.03, 3.05, fill=SURFACE_ALT)
    caption(s, 4.80, BODY_Y + 0.25, 5.0, "PROVIDER-OPERATED BOUNDARY", size=10,
            color=MUTED)

    box(s, MARGIN + 0.45, BODY_Y + 1.20, 2.45, 0.75, "Application", size=13, bold=True)
    arrow(s, MARGIN + 2.92, BODY_Y + 1.57, 4.95, BODY_Y + 1.57)
    edge_label(s, MARGIN + 2.97, BODY_Y + 1.16, 1.20, "single call", size=9)

    box(s, 4.97, BODY_Y + 0.62, 4.20, 2.20,
        "Provider-managed\nGuarded Gateway\n\nInput guardrails · model routing · model "
        "invocation · output guardrails · audit and usage reporting",
        size=14, bold=True, border=ACCENT)
    arrow(s, 9.19, BODY_Y + 1.72, 10.15, BODY_Y + 1.72)
    box(s, 10.17, BODY_Y + 1.35, 2.20, 0.75, "Model provider", size=12.5,
        fill=SURFACE_ALT)


def mode_c_topology(s):
    """The same runtime, deployed inside the customer's own AWS account."""
    panel(s, MARGIN, BODY_Y + 0.10, 8.55, 3.05, fill=SURFACE_ALT)
    caption(s, MARGIN + 0.25, BODY_Y + 0.25, 5.0, "CUSTOMER'S OWN AWS ACCOUNT",
            size=10, color=MUTED)
    panel(s, 9.75, BODY_Y + 0.10, 2.83, 3.05, fill=SURFACE_ALT)
    caption(s, 9.95, BODY_Y + 0.25, 2.5, "HOSTED (PROVIDER)", size=10, color=MUTED)

    box(s, MARGIN + 0.35, BODY_Y + 1.30, 1.85, 0.62, "Application", size=12)
    arrow(s, MARGIN + 2.22, BODY_Y + 1.61, MARGIN + 2.80, BODY_Y + 1.61)
    box(s, MARGIN + 2.82, BODY_Y + 1.30, 2.05, 0.62, "Customer LiteLLM\nor application",
        size=12)
    arrow(s, MARGIN + 4.89, BODY_Y + 1.61, MARGIN + 5.45, BODY_Y + 1.61)
    box(s, MARGIN + 5.47, BODY_Y + 0.62, 2.75, 2.20,
        "Customer-hosted\nGuardrails Runtime\n\nPolicy cache · Bedrock Guardrails "
        "adapter · custom evaluators · local audit integration",
        size=13, bold=True, border=ACCENT)
    box(s, MARGIN + 2.82, BODY_Y + 2.30, 2.05, 0.55, "Model provider", size=11.5,
        fill=SURFACE_ALT)
    arrow(s, MARGIN + 3.85, BODY_Y + 1.94, MARGIN + 3.85, BODY_Y + 2.28,
          color=HAIRLINE)

    box(s, 9.95, BODY_Y + 1.15, 2.43, 1.05, "Hosted control plane\npolicy sync only",
        size=12, fill=SURFACE_ALT)
    arrow(s, MARGIN + 8.24, BODY_Y + 1.68, 9.93, BODY_Y + 1.68, color=MUTED,
          dashed=True)
    edge_label(s, 8.60, BODY_Y + 2.32, 3.4,
               "optional, customer-selected\ntelemetry only — down to none", size=9)


TOPOLOGIES = {
    "A": mode_a_topology,
    "B": mode_b_topology,
    "C": mode_c_topology,
}
