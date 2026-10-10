# Guardrail Coverage

## Trigger Condition

The selected coverage and additional-boundary workstreams require explicit, measurable detector and surface limits. This document is the authority for supported algorithms, languages and transforms; listing a product catalog category does not implement every detector within it.

## Scope Boundary

Local engines operate without internet after preparation. The managed Bedrock adapter is included but external execution remains separately gated. We do not train a model. Multimodal scope is PNG/JPEG text inspection; unsupported audio/video must fail explicitly. Tool safety never grants permission to execute.

## Detector Matrix

| Engine / revision family | Required behavior | Transform | Acceptance |
|---|---|---|---|
| PII / pii-v2 | Preserve proof ASCII email and +international phone grammar; add payment-card candidates 13–19 digits with Luhn checksum, reject repeated digits; add IPv4 only when explicitly enabled | Replace matched text with category marker | Exact positive, negative, Unicode-boundary and overlapping-span cases per entity |
| Secrets / secrets-v1 | PEM private-key begin/end blocks; AWS access-key IDs matching registered 20-character grammar; GitHub token prefix/length patterns frozen in fixtures | Replace whole token/block; never partially expose | At least 20 positive and 20 near-miss cases per detector; synthetic values only |
| Terms / lexical-v1 | Unicode NFKC + casefold literal matching with an original-offset map; no executable regex from policy | Replace matched denied literal if mapping is safe | Mixed case, composed Unicode, punctuation and overlap cases |
| Topics / lexical-v1 | Exact built-in topic lexicons: finance = invoice,budget,payment; medical = diagnosis,prescription,treatment; software = source code,compiler,repository. Match whole normalized words/phrases | No semantic rewrite; block in enforcing mode | All listed terms plus clean near misses; label explicitly as lexical topic coverage |
| Structured output / schema-v1 | JSON Schema subset: type, properties, required, additionalProperties, items, enum, const, minimum, maximum, minLength, maxLength, minItems, maxItems, anyOf. Maximum 32 schema levels and 256 properties | Only validated string-leaf redactions; revalidate | Valid/invalid nested shapes, duplicate keys, additional fields, remote references, schema expansion limits |
| Injection / deberta-v2 | Local pretrained protectai/deberta-v3-base-prompt-injection-v2; pinned revision and artifact hash; sliding token windows; default threshold 0.90 | No safe rewrite: block in Redact/Block | Held-out benchmark plus long-tail, prompt-leakage and multilingual limitations |
| Image text / ocr-v1 | PNG/JPEG orientation-aware local English/Spanish OCR; inspect extracted text through selected rules | No image masking in this increment | Rendered synthetic email/secret/injection fixtures and malformed/resource-limit images |
| Managed / bedrock-v1 | ApplyGuardrail input/output mapping with explicit numbered version and approved region | Accept safe mapped text masking only | Local contract matrix required; live profile separate |

Freeze these deliberately bounded secret grammars: AWS key IDs match `(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])`; classic GitHub token candidates match `(?<![A-Za-z0-9_])ghp_[A-Za-z0-9]{36}(?![A-Za-z0-9_])`; private keys match a complete PEM BEGIN/END block with identical type PRIVATE KEY, RSA PRIVATE KEY, EC PRIVATE KEY or OPENSSH PRIVATE KEY, with at most 64 KiB body. An unmatched private-key BEGIN marker is a finding and is untransformable, so enforcing modes block. These are the selected detection grammars, not a claim of all provider-issued formats. Commit exact positive/near-miss fixtures before enabling the engine. Provider prefix references: [AWS key types](https://docs.aws.amazon.com/IAM/latest/UserGuide/security-creds-programmatic-access.html), [GitHub token types](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github). The AWS detector covers key IDs, not arbitrary unknown secret values. Payment-card detection is format/checksum detection, not verification of an issued card.

## Four Allow and Deny Categories

| Category | Matching | Default | Enforcement owner |
|---|---|---|---|
| Destination | Registered destination ID; exact HTTPS host/port, path prefix boundary and allowed resolved CIDRs | Empty allowed set denies | Runtime/evaluator outbound client and infrastructure egress; team gateway operator must install corresponding route restrictions |
| Provider/model | Registered provider ID with exact vendor/model/zone/artifact revision | Empty allowed set denies | Runtime eligibility plus connector check of actual normalized gateway route |
| Tool/action | Exact versioned ToolSpec name and digest, case-sensitive | Empty allowed set denies tool use | Connector/application hook before tool dispatch; app still authorizes business access |
| Content terms/topics | `term:literal` or `topic:finance`, `topic:medical`, `topic:software`; NFKC/casefold matching | Allow restriction disabled, deny empty | Declared stage's rules |

A deny match wins even if the same entry is allowed. Across policy layers, enabled allows intersect and denies union. An enabled content allowlist requires at least one allow match in each inspected text part and no deny match; it is a positive-content constraint, not a detector bypass. Blank parts count as no allow match. Unknown topic IDs reject policy publication. An allowed term never exempts a PII/secret/injection rule. Destination/provider/tool restrictions are structural controls and apply even in Disabled or Observe. Content-list findings follow the enforcement state.

## Enforcement State Matrix

| Binding state | Detector execution | Successful finding | Operational detector failure |
|---|---|---|---|
| Disabled | No content detectors; eligibility checks still run | Not evaluated, action allow with explicit disabled reason | Structural/residency failure blocks |
| Observe | All selected content checks | action allow, would_be_action reflects compiled rules | Approved observe failure permitted only for nonmandatory checks, logged explicitly |
| Warn | All selected checks | action warn, unchanged content | Mandatory failure blocks; otherwise approved policy rule |
| Redact | All selected checks | Safe text/JSON transform; untransformable findings block | Never release a failed/partial transform |
| Block | All selected checks | Any enforcing finding blocks | Approved fail-closed default |

Block dominates redact, then warn, then allow when checks disagree. Mandatory rules may impose a higher effective state than the binding. No state bypasses authentication, residency, quotas, unsupported-surface rejection or revocation. A clean result from one evaluator cannot override another evaluator's finding.

## Media and Structured Boundaries

Supported chat transport is `/v1/chat/completions` through the pinned LiteLLM connector: text messages, inline PNG/JPEG, function tool calls and buffered SSE. The standalone Evaluation API additionally supports retrieval/tool hooks. It does not retrieve URLs, execute tools or transcribe audio. Gateway support for another endpoint does not imply guardrail support.

Image inspections return `inspection_scope=image_text`, not a clean bill of visual safety. Nonblank images with mean OCR word confidence below 60 or no recognized words return inspection_incomplete when image text inspection is mandatory; blank-image fixtures pass only the declared text-scope check. Image dimensions and decoded bytes are checked before OCR. Do not claim accurate detection of text hidden in every image. English/Spanish OCR is supported; injection model language limits are reported separately.

## Quality Dataset and Thresholds

Commit a licensed/synthetic held-out corpus with stable case IDs, provenance, category, expected result and split; never include real customer data. At least 200 benign and 200 malicious injection cases, 25% indirect/retrieval cases, 25% benign quoted security discussions, and a separately reported Spanish subset. Dataset authors label before scoring; no threshold tuning on the held-out split. Default gate: English injection recall ≥0.90 and false-positive rate ≤0.05 on the declared corpus, all deterministic cases exact. Spanish injection results are reported and unsupported enforcement claims are prohibited until the same thresholds pass on a separate minimum 100+100 Spanish set. English-only injection policy rejects unsupported language profiles rather than promising general language coverage.

If the frozen model misses the gate, the phase remains incomplete. Improve preprocessing/fixtures only with justified error analysis, or propose a different pretrained model/threshold with new held-out evidence; do not lower the threshold of acceptance or relabel failures as unsupported after seeing results. Record confusion matrices, sample counts, CPU/latency and model/fixture hashes. These thresholds are engineering defaults for this increment, not population-level guarantees.

## Cross-References

Models and capability limits: (see data-schema.md). Execution: (see component-specs.md §RuleEvaluators), (see component-specs.md §ClassifierWorker), (see component-specs.md §MediaInspector). Evidence gates: (see quality-gates.md).
