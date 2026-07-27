set shell := ["bash", "-uc"]

docs := "solution-docs"
tools := "tools/docs"
deck_tools := "tools/deck"
xlsx_tools := "tools/xlsx"

# List available recipes.
default:
    @just --list

# Run every validator. Non-zero exit on any failure.
check:
    @python3 {{ tools }}/validate.py {{ docs }}

# Regenerate every machine-owned region in place.
gen:
    @python3 {{ tools }}/generate.py {{ docs }}

# Regenerate tables of contents only.
toc:
    @python3 {{ tools }}/generate.py {{ docs }} --only toc

# Reverse-index report. Optional single ID, e.g. `just trace AGR-009`.
trace id="":
    @python3 {{ tools }}/trace.py {{ docs }} {{ id }}

# Validate, then prove every generated region is already up to date.
verify: check
    @python3 {{ tools }}/generate.py {{ docs }} --check \
      || { echo "generated content is stale — run 'just gen'" >&2; exit 1; }

# List the audience decks and the decision each one asks for.
decks:
    @python3 {{ deck_tools }}/registry.py

# Build one deck into presentations/. e.g. `just deck security`.
deck name="internal":
    @python3 {{ deck_tools }}/build.py {{ name }}

# Check a built deck: geometry, speaker notes, text fit, claims, source-ID leaks.
deck-check name="internal":
    @python3 {{ deck_tools }}/verify.py {{ name }}

# Build one deck and immediately check it.
deck-verify name="internal": (deck name) (deck-check name)

# Build and check every deck in the registry.
deck-all:
    @python3 {{ deck_tools }}/build.py --all
    @python3 {{ deck_tools }}/verify.py --all

# List the workbooks and the audience each one serves.
books:
    @python3 {{ xlsx_tools }}/registry.py

# Build one workbook into presentations/. e.g. `just xlsx delivery`.
xlsx name="delivery":
    @python3 {{ xlsx_tools }}/build.py {{ name }}

# Check a built workbook: structure, model integrity, formulas, anchors, dates.
xlsx-check name="delivery":
    @python3 {{ xlsx_tools }}/verify.py {{ name }}

# Build one workbook and immediately check it.
xlsx-verify name="delivery": (xlsx name) (xlsx-check name)

# Build and check every workbook in the registry.
xlsx-all:
    @python3 {{ xlsx_tools }}/build.py --all
    @python3 {{ xlsx_tools }}/verify.py --all
