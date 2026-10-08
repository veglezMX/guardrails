# User and system boundaries

## Management API and CLI

Management accepts UTF-8 JSON over verified HTTPS with bearer credentials and strict schemas. Every lifecycle command is an HTTP client of the same API; it cannot open SQLite files or rewrite deployment JSON. Wire shapes, authorization and endpoint-to-command mappings are defined once in api-endpoints.md. Core models are in data-schema.md.

API changes return committed desired state and an operation ID. CLI mutation commands wait for application by default; `--no-wait` returns pending status without claiming activation. A wait timeout reports the committed operation and nonzero exit status. Read operations are JSON by default, with optional concise table rendering. Secret issuance uses `--credential-out PATH`, fails if that file exists, writes mode 0600 atomically and reports only its path to stdout. API token values appear only in issuance responses; never in normal CLI output.

CLI common options: `--management-url`, `--runtime-url`, `--token-file`, `--ca-file`, `--json` (default), `--wait-timeout 5`. Do not accept bearer secrets on the command line. Exit codes: 0 success/applied or explicitly no-wait committed; 2 syntax/schema rejection; 3 authentication/authorization; 4 conflict/not found/governance rejection; 5 network/service failure; 6 committed but application wait timed out. Error stderr is a safe Problem projection with correlation ID, never an input echo. Help and argument parsing never require network access.

## Supported gateway input

`POST /v1/chat/completions` accepts exactly `model`, `messages`, optional `stream=false`, optional `n=1`, optional `temperature=0`, optional `max_tokens` integer 1–256. Unknown fields fail with unsupported_surface. `model` must equal that gateway's configured alias. Messages contain only `role` and string `content`; roles are system/developer/user/assistant, at least one user message, maximum 32. No names, tool calls, content arrays, images/audio, provider keys, metadata overrides, response_format, functions, parallel tools or alternate inference paths. Optional fields omitted use the listed defaults; max_tokens defaults to 128. Evaluate all messages independently as a single TextBatch (see data-schema.md §TextBatch).

`stream=true`, multiple choices, unknown roles and reserved future stages are explicitly unsupported. Reject before model invocation; errors are 400 and do not echo the supplied body. Exceeding body/content limits yields 413. Malformed JSON is 400; invalid supported-field types are 422. Invalid gateway authentication is 401; attempted override/identity mismatch is 403. Clients authenticate to their gateway using a distinct local gateway key; the server chooses the workload credential. The caller cannot select another workload or runtime policy.

## Gateway output

Successful output remains a non-streaming chat-completion envelope with one assistant string message. Add a top-level `guardrails` metadata object: `correlation_id`, `input_decision_id`, `output_decision_id` (nullable when no runtime evaluation), `input_source`, `output_source`, `policy_id`, `policy_version`, `binding_revision`, `snapshot_generation`, `enforcement_state`, `input_action`, `output_action`, `warning_codes`, `audit_status`. These reference canonical Decision fields (see data-schema.md §Decision). Warn emits namespaced reason codes in warning_codes; failures expose a separate operational_codes array and connector origin. This extension is part of the proof contract and must survive actual LiteLLM serialization.

A policy block returns HTTP 403 with the safe gateway error envelope `{ "error": { "code": "guardrail_blocked", "message": "Request blocked by configured guardrails", "correlation_id": "UUID", "decision_id": "UUID" } }`. No unsafe content or original validation value is included. Dependency/fail-closed outage returns 503; model error 502; model timeout 504. HTTP status and exact envelope normalization are mandatory integration assertions against the selected image. Input blocking produces no upstream model call. Output blocking happens before any response body is delivered.

## Detector formats

The local detector recognizes ASCII dot-atom email addresses with local part length 1–64 and overall length <=254: alphanumerics and `.!#$%&'*+/=?^_` plus backtick, `{|}~-`; dots cannot lead, trail or repeat. Domain labels are ASCII alphanumeric with interior hyphens, length 1–63; at least two labels; final label alphabetic length 2–63. Match whole tokens using explicit surrounding-character rules; do not return a valid substring inside a malformed longer email token. Matching is case-insensitive without changing original text. Quoted addresses, Unicode mailboxes, comments and domain literals are outside this format and are labeled unsupported examples, not claimed safe data.

Phone format is a literal `+`, a first digit 1–9, then 7–14 additional ASCII digits: 8–15 digits total, no separators/extensions. Reject a match embedded in a longer digit sequence or malformed `+` sequence. This is a detectable textual format, not proof of an allocated or valid national phone number. Space/dash/parenthesis variants are outside this format. Punctuation/whitespace around a complete token is permitted; test negative boundaries and Unicode surroundings.

Fixtures specify exact span offsets, namespaced reason codes, and transformed text. Merge overlaps by union and replace each union with the literal `[REDACTED]`. Never run sequential offset mutation against already transformed text. Required detector category is GR-003 (Sensitive Data and Privacy); no real-world recall or false-positive-rate claim is made.

## Artifacts and privacy

The acceptance command emits a metadata-only EvidenceReport, effective sanitized configuration, artifact/version checksums and ten row results. Raw spy captures and sensitive fixture bodies remain in memory; persist fixture IDs and expected-action labels instead. The repository's authored synthetic fixture corpus is an intentional test input, not retained runtime traffic. The privacy scan covers actual SQLite rows, connector spools, Docker logs, generated reports and error output; do not fail merely because authored fixture files contain their own test values.

## Compatibility

The new local contract is explicitly tagged `local-proof-v1`. It preserves six entities, four actions, namespaced reasons and the seven-stage vocabulary, while advertising only input/output support. The prototype's unversioned text-body projection is deliberately migrated before release; it is not silently accepted as an alternative on the new route. No deployed hosted client compatibility is claimed. Migration steps and retained regression semantics are specified in migration-map.md.
