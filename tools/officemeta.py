"""Document metadata for every generated Office artifact.

These files leave the repository and get circulated, so what they claim about
themselves matters. The libraries that build them ship defaults that are wrong
in ways a reader would notice:

  * python-pptx carries its own author's name in `lastModifiedBy`, a 2013
    template date, a thumbnail of the template's blank slide, and an extended
    properties part declaring "Microsoft Macintosh PowerPoint" and a 4:3 slide
    size the decks do not use.
  * openpyxl names itself as the creator and stamps the wall-clock build time.

None of that identifies the machine or the user who ran the build — verified, and
re-verified by the checkers — but all of it is false provenance on a document
someone will open in a meeting.

So every artifact gets the same treatment: honest core properties naming the
repository as the author, a fixed date so a rebuild is byte-comparable, no
thumbnail, and an extended properties part that states only what is true.

The date comes from SOURCE_DATE_EPOCH when set (the reproducible-builds
convention), and otherwise from the constant below.
"""

import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# The documentation set's own date. Fixed rather than wall-clock so two builds
# of unchanged sources produce identical bytes.
FALLBACK_DATE = datetime(2026, 7, 26, tzinfo=timezone.utc)

AUTHOR = "AI Guardrails — generated from solution-docs"
CATEGORY = "Internal"
STATUS = "Draft — design intent and delivery plan; nothing built or piloted"

_APP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"

# Parts that carry nothing we authored and nothing a reader benefits from.
DROP_PARTS = {"docProps/thumbnail.jpeg", "docProps/thumbnail.jpg"}


def build_date():
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch and epoch.strip().isdigit():
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    return FALLBACK_DATE


def _naive(dt):
    """python-pptx and openpyxl both want naive UTC datetimes."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def stamp_pptx(prs, title, subject):
    """Honest core properties on a python-pptx presentation, before saving."""
    cp = prs.core_properties
    when = _naive(build_date())
    cp.title = title
    cp.subject = subject
    cp.author = AUTHOR
    cp.last_modified_by = AUTHOR
    cp.comments = ("Generated from the solution-docs set. Rebuild rather than "
                   "edit — hand edits are overwritten.")
    cp.category = CATEGORY
    cp.content_status = STATUS
    cp.keywords = ""
    cp.language = "en"
    cp.revision = 1
    cp.created = when
    cp.modified = when


def stamp_xlsx(wb, title, subject):
    """Honest core properties on an openpyxl workbook, before saving."""
    pr = wb.properties
    when = _naive(build_date())
    pr.title = title
    pr.subject = subject
    pr.creator = AUTHOR
    pr.lastModifiedBy = AUTHOR
    pr.description = ("Generated from the solution-docs set. Rebuild rather than "
                      "edit — hand edits are overwritten.")
    pr.category = CATEGORY
    pr.contentStatus = STATUS
    pr.keywords = None
    pr.language = "en"
    pr.revision = None
    pr.created = when
    pr.modified = when


def _app_xml(application, extra=""):
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Properties xmlns="{_APP_NS}">'
        f"<Application>{application}</Application>"
        f"{extra}"
        "<Company></Company><Manager></Manager>"
        "<ScaleCrop>false</ScaleCrop><LinksUpToDate>false</LinksUpToDate>"
        "<SharedDoc>false</SharedDoc><HyperlinksChanged>false</HyperlinksChanged>"
        "</Properties>"
    )


PPTX_APP = _app_xml("AI Guardrails deck generator",
                    "<PresentationFormat>Widescreen</PresentationFormat>")
XLSX_APP = _app_xml("AI Guardrails workbook generator")


def scrub(path, app_xml):
    """Rewrite the package: drop the inherited thumbnail, replace the extended
    properties part, and flatten every entry timestamp.

    Done on the saved file rather than through the library because neither
    library exposes the thumbnail or the extended properties part for editing.
    The rewrite preserves every other part byte for byte.
    """
    path = Path(path)
    when = build_date()
    stamp = (when.year, when.month, when.day, when.hour, when.minute, when.second)

    with zipfile.ZipFile(path) as src:
        items = [(info, src.read(info.filename)) for info in src.infolist()]

    dropped = {name for name in (i.filename for i, _ in items) if name in DROP_PARTS}

    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for info, data in items:
            name = info.filename
            if name in dropped:
                continue
            if name == "docProps/app.xml":
                data = app_xml.encode("utf-8")
            elif name == "docProps/core.xml":
                data = _pin_dates(data, when)
            elif name == "_rels/.rels" and dropped:
                data = _drop_relationships(data, dropped)
            elif name == "[Content_Types].xml" and dropped:
                data = _drop_content_types(data, dropped)
            entry = zipfile.ZipInfo(name, date_time=stamp)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = info.external_attr
            out.writestr(entry, data)

    shutil.move(str(tmp), str(path))
    return sorted(dropped)


def audit(path):
    """Check a built package for metadata that should never leave the repo.

    Returns a list of failure strings. Run from both verifiers, so a library
    upgrade that reintroduces its own defaults is caught at build time rather
    than by whoever opens the file properties in a meeting.
    """
    path = Path(path)
    failures = []

    # Anything that would identify the machine or the person who ran the build.
    identity = [r"/home/[a-z0-9_.-]+", r"/Users/[A-Za-z0-9_.-]+", r"[A-Z]:\\\\Users",
                r"\bpython-pptx\b", r"\bopenpyxl\b", r"Steve Canny"]
    for var in ("USER", "USERNAME", "LOGNAME", "HOSTNAME"):
        value = os.environ.get(var, "").strip()
        if len(value) > 2:
            identity.append(re.escape(value))

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        for part in ("docProps/core.xml", "docProps/app.xml"):
            if part not in names:
                failures.append(f"{path.name}: missing {part}")
                continue
            text = zf.read(part).decode("utf-8", "replace")
            for pattern in identity:
                for m in re.finditer(pattern, text, re.I):
                    failures.append(
                        f"{path.name}: {part} carries {m.group(0)!r}")

        for name in names:
            if name.startswith("docProps/thumbnail"):
                failures.append(
                    f"{path.name}: inherited {name} — not content this repo authored")

        if "docProps/core.xml" in names:
            core = zf.read("docProps/core.xml").decode("utf-8", "replace")
            stamp = build_date().strftime("%Y-%m-%dT%H:%M:%SZ")
            for field in ("created", "modified"):
                found = re.search(r"<dcterms:" + field + r"\b[^>]*>([^<]*)<", core)
                if not found:
                    failures.append(f"{path.name}: core.xml has no {field} date")
                elif found.group(1) != stamp:
                    failures.append(
                        f"{path.name}: {field} is {found.group(1)!r}, expected the "
                        f"pinned build date {stamp!r} — a wall-clock stamp makes "
                        f"rebuilds differ")
            if AUTHOR not in core:
                failures.append(f"{path.name}: core.xml does not name this repo as "
                                f"the author")
    return failures


def _pin_dates(data, when):
    """Force both core dates to the build date.

    openpyxl rewrites `modified` to the wall clock as it saves, whatever the
    properties object said — which would put a real timestamp on a document that
    otherwise carries none, and make two builds of unchanged sources differ.
    """
    xml = data.decode("utf-8")
    stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    for field in ("created", "modified"):
        xml = re.sub(
            r"(<dcterms:" + field + r"\b[^>]*>)[^<]*(</dcterms:" + field + r">)",
            r"\g<1>" + stamp + r"\g<2>", xml)
    return xml.encode("utf-8")


def _drop_relationships(data, dropped):
    """Remove the relationship entries pointing at parts we removed."""
    xml = data.decode("utf-8")
    for name in dropped:
        target = name.split("/")[-1]
        xml = re.sub(r"<Relationship\b[^>]*Target=\"[^\"]*"
                     + re.escape(target) + r"\"[^>]*/>", "", xml)
    return xml.encode("utf-8")


def _drop_content_types(data, dropped):
    """Remove overrides and now-unused defaults for parts we removed."""
    xml = data.decode("utf-8")
    for name in dropped:
        xml = re.sub(r"<Override\b[^>]*PartName=\"/" + re.escape(name)
                     + r"\"[^>]*/>", "", xml)
    if not any(name.endswith((".jpeg", ".jpg")) for name in _remaining(xml)):
        xml = re.sub(r"<Default\b[^>]*Extension=\"jpe?g\"[^>]*/>", "", xml)
    return xml.encode("utf-8")


def _remaining(content_types_xml):
    return re.findall(r'PartName="/([^"]+)"', content_types_xml)
