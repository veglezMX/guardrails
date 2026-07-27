set shell := ["bash", "-uc"]

docs := "solution-docs"
tools := "tools/docs"

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
