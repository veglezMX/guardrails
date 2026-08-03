set shell := ["bash", "-uc"]

docs := "solution-docs"
mvp := "mvp"
tools := "tools/docs"
deck_tools := "tools/deck"
xlsx_tools := "tools/xlsx"
docx_tools := "tools/docx"

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

# Run the MVP runtime and connector test suite.
test:
    @cd {{ mvp }} && python3 -m unittest discover -s tests -t tests

# Validate every policy document in the deployment directory.
policies:
    @PYTHONPATH={{ mvp }} python3 -m guardrails --root {{ mvp }}/deployment validate

# Serve the Evaluation API locally. e.g. `just serve 0.0.0.0 9000`.
serve host="127.0.0.1" port="8080":
    @PYTHONPATH={{ mvp }} python3 -m guardrails --root {{ mvp }}/deployment \
      serve --host {{ host }} --port {{ port }}

# Walk one request through input eval, the model, and output eval.
demo:
    @python3 {{ mvp }}/demo.py

# Print the evaluator capabilities this deployment serves.
caps:
    @PYTHONPATH={{ mvp }} python3 -m guardrails --root {{ mvp }}/deployment capabilities

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

# List the stakeholder documents and the audience each one serves.
reports:
    @python3 {{ docx_tools }}/registry.py

# Build one document into presentations/. e.g. `just docx local-first`.
docx name="local-first":
    @python3 {{ docx_tools }}/build.py {{ name }}

# Check a built document: structure, placeholders, empty cells, metadata.
docx-check name="local-first":
    @python3 {{ docx_tools }}/verify.py {{ name }}

# Build one document and immediately check it.
docx-verify name="local-first": (docx name) (docx-check name)

# Build and check every document in the registry.
docx-all:
    @python3 {{ docx_tools }}/build.py --all
    @python3 {{ docx_tools }}/verify.py --all

# Build and check every artifact, then copy them into assets/.
#
# presentations/ is gitignored, so a build alone leaves nothing anyone else can
# see. assets/ is the tracked, circulated copy — this is the only step that
# publishes one.
publish: deck-all xlsx-all docx-all
    @mkdir -p assets
    @cp presentations/*.pptx presentations/*.xlsx presentations/*.docx assets/
    @ls -1 assets/
