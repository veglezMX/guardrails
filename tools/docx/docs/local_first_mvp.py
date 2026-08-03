"""Local-first MVP delivery plan.

Written to be read on its own. A stakeholder should be able to approve or reject
the sequence from this document alone, without opening the documentation set it
was derived from — so no filename, section number or register identifier from
that set appears here. Everything it relies on is restated in plain terms.

verify.py enforces that: a reference that creeps back in fails the build.
"""

from lib import (
    FLAG,
    WASH,
    band,
    bullets,
    callout,
    flow,
    footer_note,
    heading,
    para,
    table,
    title_block,
)

TITLE = "AI Guardrails — Local-First Delivery Plan"
SUBTITLE = "Building the first release with no cloud account, and landing on AWS last"


# ---------------------------------------------------------------- 1. summary

def executive_summary(doc):
    title_block(
        doc,
        TITLE,
        SUBTITLE,
        [
            "Status: draft for approval. Nothing built or piloted.",
            "Decision requested: approve the build sequence in section 2 and "
            "the two records in section 1.",
        ],
    )

    heading(doc, "1. Executive summary", 1)

    heading(doc, "What we are building", 2)

    para(doc,
         "Teams across the organisation each run their own LLM gateway. Today "
         "every team writes its own safety checks, so the same organisational "
         "rule is implemented differently in every place it applies, and "
         "nobody can say with evidence which rule is in force where.")

    para(doc,
         "AI Guardrails makes the safety policy a separate, versioned, reusable "
         "thing. A team keeps its own gateway, its own model provider and its "
         "own request path, and calls out to a shared evaluation service that "
         "answers one question: may this content proceed, and why. The policy, "
         "the evaluation and the audited decision are the product. The gateway "
         "is not.")

    flow(doc,
         ["Application", "Team's own\ngateway", "Check the\nprompt",
          "Model", "Check the\nresponse", "Response"],
         note="The two checks call the shared service. Everything else stays "
              "where it already is.")

    para(doc,
         "The first release exists to prove one claim: that a single policy can "
         "govern two independently operated gateways and produce the same "
         "decision in both. If that holds, the approach scales to every team. "
         "If it does not, nothing built afterwards matters.")

    heading(doc, "The constraint: no cloud account", 2)

    para(doc,
         "We have no AWS account, and getting one is not on a date we control. "
         "The question this plan answers is whether that blocks the work.")

    para(doc,
         "It largely does not. The first release depends on twelve platform "
         "services. Eleven are infrastructure choices — compute, storage, "
         "queues, secrets, metrics — and each has a local stand-in that meets "
         "the actual requirement. Policy storage needs immutable versioning, "
         "not a specific cloud product. Audit needs a durable append-only "
         "record, not a specific queue.")

    para(doc,
         "One dependency has no local substitute: Amazon Bedrock Guardrails, "
         "the hosted detection engine we intend to buy rather than build. It "
         "exists only as a cloud service. That is the honest gap — and a "
         "narrower one than it sounds, because detection itself does not wait "
         "for it. Four detectors are built and tested locally from the start.")

    callout(doc,
            "**Bottom line.** Fourteen of the seventeen capabilities in the "
            "first release can be built and demonstrated locally. Three need a "
            "cloud account: the managed detection engine, hosted deployment, "
            "and multi-region availability. All three are grouped into the "
            "final stage.")

    heading(doc, "What building locally first buys us", 2)

    bullets(doc, [
        "**No spend and no account dependency until the last stage.** Funding "
        "confirmation and account provisioning stop gating the start of work.",
        "**A later phase gets de-risked for free.** Some teams will eventually "
        "require the evaluation runtime to sit inside their own network and "
        "keep working when it cannot reach our control plane. Building while no "
        "control plane exists forces that independence from the first commit, "
        "instead of discovering the coupling months later.",
        "**Vendor independence is proven, not asserted.** We have committed that "
        "no detection vendor's data shapes leak into our own contract. Standing "
        "up two non-vendor engines first makes that boundary structural rather "
        "than a rule someone has to remember.",
        "**Failure behaviour gets tested properly.** What happens when a "
        "detector is down or slow is part of policy, not a hardcoded fallback. "
        "Locally that is tested by stopping a container — cheaper and more "
        "repeatable than provoking the same conditions against a managed "
        "service.",
    ])

    heading(doc, "What building locally first costs us", 2)

    bullets(doc, [
        "**The first release cannot be signed off before the final stage.** The "
        "managed detection engine is part of it, and deferring a committed "
        "deliverable is a change that must be recorded rather than absorbed "
        "quietly.",
        "**One proof stays open.** Confirming that the managed engine's output "
        "maps cleanly onto our own decision format cannot be done without the "
        "service. Until then the contract is treated as settled in every "
        "respect except engine-neutrality.",
        "**Two security controls stay unproven.** Encryption at rest and "
        "cloud-native workload identity are properties of the target platform. "
        "Local substitutes demonstrate the design, not the control. Both are "
        "proven in the final stage.",
        "**Local performance numbers are indicative only.** Measurements taken "
        "on developer hardware, with no network hop to a managed detector, rank "
        "the checks by cost. They do not set a latency budget.",
    ])

    heading(doc, "The decision requested", 2)

    table(doc,
          ["#", "Decision", "Owner"],
          [
              ["1", "Approve the eight-stage sequence in section 2 as the build "
                    "order for the first release.",
               "Delivery lead, technical lead"],
              ["2", "Approve recording two entries in the decision log: that "
                    "development runs locally, and that the managed detection "
                    "engine is deferred to the final stage with sign-off "
                    "conditioned on it.",
               "Product lead, security lead"],
              ["3", "Set a target date for the cloud account request, so the "
                    "final stage is not the point at which we discover "
                    "provisioning takes longer than the build did.",
               "Platform operations lead"],
              ["4", "Confirm that two separately configured gateways satisfy the "
                    "reuse proof, and that recruiting two real adopting teams "
                    "remains a separate open commitment.",
               "Delivery lead"],
          ],
          widths=[4, 62, 24])

    doc.add_page_break()


# ---------------------------------------------------------------- 2. roadmap

def delivery_roadmap(doc):
    heading(doc, "2. Delivery roadmap by stage", 1)

    para(doc,
         "The order below is not a preference. The domain model and the "
         "evaluation contract have to exist before any gateway integration, "
         "because an integration is only a client of that contract. The single "
         "change this plan makes is to move every cloud-bound item out of its "
         "original position and into the final stage.")

    band(doc,
         ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"],
         [(7, "Runs entirely on local infrastructure — no account, no spend",
           WASH),
          (1, "Cloud", FLAG)])

    table(doc,
          ["Stage", "What it delivers", "Cloud"],
          [
              ["**S0**\nFoundations",
               "Development environment, service skeletons, container topology, "
               "task commands.", "No"],
              ["**S1**\nContract",
               "The six things the system talks about — tenant, workload, "
               "policy, binding, evaluation, decision — and the evaluation "
               "contract: what a caller sends, and the single normalised "
               "decision shape that comes back regardless of which detector "
               "produced it.", "No"],
              ["**S2**\nPolicy model",
               "A versioned, immutable policy format. Changing a policy always "
               "creates a new version; nothing is edited in place. Every policy "
               "must declare what happens when evaluation fails.", "No"],
              ["**S3**\nRuntime",
               "The service that actually evaluates: resolves which policy "
               "applies, runs the configured detectors, normalises their output "
               "into one decision, records the decision, and measures how long "
               "each detector took.", "No"],
              ["**S4**\nIntegration",
               "The gateway plug-in that calls the service before and after the "
               "model, enforces the answer, and reports nothing back that the "
               "policy did not ask for. Runs in observe mode: decisions are "
               "recorded, nothing is blocked.", "No"],
              ["**S5**\nControl plane",
               "Registering a team and a workload, binding a workload to a "
               "policy version, promoting it from observe to enforce, and "
               "rolling it back instantly.", "No"],
              ["**S6**\nProof",
               "Two independently configured gateways against one policy, and "
               "the nine acceptance conditions in section 3 run as an automated "
               "suite.", "No"],
              ["**S7**\nCloud landing",
               "Managed detection engine, hosted deployment, encryption and "
               "identity controls, regional placement, and the whole proof "
               "re-run on real infrastructure.", "**Yes**"],
          ],
          widths=[15, 74, 11])

    heading(doc, "2.1 How each cloud dependency is covered until the final stage", 2)

    para(doc,
         "‘Met’ means the requirement is genuinely satisfied — for policy "
         "storage the requirement is immutable versioning, and a version-"
         "controlled repository provides it.")

    table(doc,
          ["What is needed", "Local stand-in", "Status"],
          [
              ["Somewhere to run the service", "Containers on a developer "
               "machine", "Met"],
              ["An HTTP surface", "Local application server", "Met"],
              ["Immutable policy storage", "Version-controlled repository",
               "Met"],
              ["A workload and tenant registry", "Relational store",
               "Met — the schema is ours"],
              ["A durable audit record", "Local queue and append-only archive",
               "Met"],
              ["Secret handling", "Encrypted local store", "Met for development"],
              ["Latency and decision metrics", "Structured logs and histograms",
               "Met"],
              ["Encryption at rest", "Deferred", "**Proven in S7**"],
              ["Workload identity and least-privilege access",
               "Local tokens or a local identity provider",
               "**Proven in S7**"],
              ["The bought detection engine",
               "Local detectors carry the load; its adapter is stubbed",
               "**Mapping validated in S7**"],
          ],
          widths=[32, 34, 34])

    heading(doc, "2.2 The detection-engine gap", 2)

    para(doc,
         "Bedrock Guardrails is a hosted service that classifies text against "
         "policies you configure — harmful content, prompt attacks, banned "
         "topics, personal data. It is the one dependency with no stand-in, "
         "because what is being bought is the classifier itself: there is no "
         "container to run and no offline edition. It is also, deliberately, "
         "the first detection engine and not a permanent one — our own "
         "contract has to survive replacing it.")

    para(doc,
         "So the detector layer gets built regardless, against engines we do "
         "not intend to keep. Four run locally from the first weeks: a "
         "rule-based detector, a sensitive-data detector, a structured-output "
         "validator, and one called over the network so the remote path is "
         "exercised for real. The bought engine sits behind the same interface "
         "with recorded responses standing in for the service.")

    callout(doc,
            "**Why this is an advantage, not a workaround.** Because the "
            "contract never meets the eventual vendor during the entire local "
            "build, nothing vendor-shaped can leak into it. Independence "
            "becomes structural rather than a rule someone has to keep "
            "remembering. What waits for a cloud account is one thing only: "
            "confirming this vendor's output maps cleanly onto our decision "
            "format.")

    heading(doc, "2.3 What this plan does not change", 2)

    bullets(doc, [
        "**Scope.** Reordering work is not a change of scope. Nothing is added, "
        "removed or pulled forward.",
        "**The acceptance bar.** All nine conditions in section 3 still apply, "
        "unamended.",
        "**Observe before enforce.** No workload starts out blocking traffic. "
        "Promotion is a deliberate, recorded act.",
        "**Later phases.** Everything beyond the first release stays behind it.",
    ])

    doc.add_page_break()


# ------------------------------------------------------- 3. milestones/gates

def milestones_and_gates(doc):
    heading(doc, "3. Milestones and decision gates", 1)

    para(doc,
         "Each stage closes at a gate, passed on evidence rather than on "
         "elapsed effort. Dates are deliberately absent: no duration, headcount "
         "or delivery date has been agreed yet, and inventing one here would "
         "give it a status it has not earned.")

    table(doc,
          ["Gate", "Passed when", "Decided by"],
          [
              ["**G0**", "One command brings the whole stack up locally.",
               "Technical lead"],
              ["**G1**", "The contract is specified, typed models are generated "
                         "from it, and the conformance suite is scaffolded.",
               "Architecture lead"],
              ["**G2**", "Two policy versions exist, both retrievable, neither "
                         "editable in place, and each declares its behaviour for "
                         "every way evaluation can fail.",
               "Technical lead, policy owner"],
              ["**G3**", "An evaluation returns one normalised decision drawn "
                         "from several detectors; stopping a detector produces "
                         "the behaviour the policy declared, not an undocumented "
                         "fallback.",
               "Technical lead"],
              ["**G4**", "A gateway runs in observe mode with no change to its "
                         "behaviour, and decisions are being recorded.",
               "Technical lead, integration lead"],
              ["**G5**", "A workload can be registered, bound, promoted to "
                         "enforce, and rolled back — with rollback requiring no "
                         "rebuild of anything.",
               "Product lead, security lead"],
              ["**G6**", "All nine acceptance conditions pass locally as an "
                         "automated suite.",
               "Delivery lead"],
              ["**G7**", "The same suite passes on real infrastructure with the "
                         "managed engine in place, and the security controls are "
                         "proven. The first release can be signed off.",
               "Architecture lead, security lead"],
          ],
          widths=[8, 62, 30])

    heading(doc, "3.1 Decisions this plan needs, and when", 2)

    para(doc,
         "Eight decisions must be made rather than assumed. Local-first work "
         "closes two of them earlier than a cloud-first sequence would, and "
         "moves four to a single checkpoint at the final stage.")

    table(doc,
          ["Decision", "Closes at", "Note"],
          [
              ["What a blocked response returns to the caller", "**G4**",
               "Brought forward — building the integration forces the question."],
              ["Which parts of a shared policy a team may override", "**G5**",
               "Brought forward — the binding model surfaces it."],
              ["Latency budget and availability target", "G6 → **G7**",
               "Ranked locally; the figure itself comes from real "
               "measurement."],
              ["Which container platform hosts the service", "**S7 entry**",
               "Genuinely deferrable — the deliverable is a container either "
               "way."],
              ["Target region, and whether the managed engine is fully "
               "available there", "**S7 entry**", "No local path."],
              ["Funding commitment", "**S7 entry**",
               "No longer gates the start of the build."],
              ["Cloud account request filed", "**S7 entry**",
               "Should be filed well before S7 — see the ask in section 1."],
              ["Two named teams willing to adopt", "Unchanged",
               "Local work proves the technical claim; the adoption commitment "
               "is separate and stays open."],
          ],
          widths=[38, 16, 46])

    heading(doc, "3.2 The nine acceptance conditions", 2)

    para(doc,
         "The first release is accepted only when all nine hold at once — not "
         "when the components merely exist. Eight are provable without a cloud "
         "account.")

    table(doc,
          ["#", "Condition", "Proven at"],
          [
              ["1", "Two separate gateways run the same policy", "**G6**"],
              ["2", "Neither gateway shares its model credentials with the "
                    "guardrail service", "**G6**"],
              ["3", "Both get the same decision for the same content", "**G6**"],
              ["4", "One workload runs in observe mode", "**G4**"],
              ["5", "Another runs in enforce mode", "**G5**"],
              ["6", "A policy can be upgraded and rolled back", "**G5**"],
              ["7", "Storing raw prompts and responses can be switched off",
               "**G3**"],
              ["8", "A failed evaluation behaves the way the policy said it "
                    "would", "**G3**"],
              ["9", "The service can later be packaged to run inside a team's "
                    "own network without changing the integration", "**G6**"],
          ],
          widths=[5, 60, 35])

    callout(doc,
            "**Worth being precise.** The detection-engine gap blocks none of "
            "these nine conditions. It blocks one capability and one proof. The "
            "demonstration can pass locally; sign-off still waits for the final "
            "stage.")

    doc.add_page_break()


# ------------------------------------------------------------ 4. workstreams

def deliverable_plan(doc):
    heading(doc, "4. Deliverable plan", 1)

    para(doc,
         "Twenty-six tasks, each sized for one software engineer — the plan "
         "assumes a single fungible role, so any engineer can pick up any task. "
         "Sizes are a deliberate three-step ladder of one, three or five days. "
         "They are a sizing judgement, not a bottom-up estimate, and are shown "
         "so the shape of the work can be argued with rather than guessed at.")

    para(doc,
         "The work falls into three waves. Wave 2 is where the parallelism "
         "lives: five tracks that share nothing once the contract exists.")

    flow(doc,
         ["Wave 1\nsequential", "Wave 2\n5 parallel tracks", "Wave 3\nsequential"],
         note="Waves 1 and 3 are chains — no amount of people makes them "
              "shorter.")

    heading(doc, "4.1 Wave 1 — foundations (11 days, blocks everything)", 2)

    table(doc,
          ["ID", "Task", "Days"],
          [
              ["T1", "Development environment, service skeletons, container "
                     "topology, task commands", "3"],
              ["T2", "The six entities and the evaluation contract: what a "
                     "caller sends, and the one decision shape that comes back",
               "5"],
              ["T3", "Typed models generated from the contract; conformance "
                     "suite scaffolded", "3"],
          ],
          widths=[8, 80, 12])

    para(doc,
         "These three run strictly in order. Nothing else can start until the "
         "contract exists, because every other task is a client of it.",
         size=9, space_after=10)

    heading(doc, "4.2 Wave 2 — five independent tracks (58 days)", 2)

    table(doc,
          ["Track", "ID", "Task", "Days"],
          [
              ["**A**\nPolicy\n**9 d**", "T4", "Policy schema: stages, detector "
               "configuration, thresholds, outcomes", "3"],
              ["", "T5", "Immutable versioned store — a change is always a new "
                         "version", "3"],
              ["", "T6", "Declared failure behaviour for every way evaluation "
                         "can fail", "3"],

              ["**B**\nRuntime\n**12 d**", "T7", "Policy resolution from a local "
               "cache, never a blocking call out", "3"],
              ["", "T8", "Decision engine — normalise every detector's output "
                         "into one shape", "5"],
              ["", "T9", "Audit record and archive, raw content off by default",
               "3"],
              ["", "T10", "Timing captured per detector and per stage", "1"],

              ["**C**\nDetectors\n**13 d**", "T11", "Rule-based detector for "
               "organisation-specific checks", "3"],
              ["", "T12", "Sensitive-data detector", "3"],
              ["", "T13", "Structured-output validator", "1"],
              ["", "T14", "A detector called over the network, exercising "
                          "timeout and unavailability for real", "3"],
              ["", "T15", "Managed-engine adapter, stubbed with recorded "
                          "responses", "3"],

              ["**D**\nIntegration\n**12 d**", "T16", "Gateway hooks before and "
               "after the model call", "5"],
              ["", "T17", "Enforce the outcome; decide what a blocked response "
                          "returns", "3"],
              ["", "T18", "One correlation identifier across both checks; "
                          "timeout handling", "3"],
              ["", "T19", "Integration health reporting", "1"],

              ["**E**\nControl plane\n**12 d**", "T20", "Tenant and workload "
               "registry", "3"],
              ["", "T21", "Bind a workload to a policy version; promote observe "
                          "to enforce", "5"],
              ["", "T22", "Rollback by repointing a binding", "1"],
              ["", "T23", "Minimal management interface", "3"],
          ],
          widths=[14, 6, 68, 12], font=9,
          vmerge=[(0, 0, 3), (0, 3, 4), (0, 7, 5), (0, 12, 4), (0, 16, 4)])

    heading(doc, "4.3 Wave 3 — proof (9 days, sequential)", 2)

    table(doc,
          ["ID", "Task", "Days"],
          [
              ["T24", "Two gateways with distinct configurations, model "
                      "backends, identities and credentials", "3"],
              ["T25", "The nine acceptance conditions automated as a repeatable "
                      "suite", "5"],
              ["T26", "Observe-mode metrics: false-positive rate, latency, "
                      "spread of decisions", "1"],
          ],
          widths=[8, 80, 12])

    heading(doc, "4.4 What this means for staffing", 2)

    para(doc,
         "Seventy-eight engineer-days of work in total. Twenty of them — waves "
         "1 and 3 — are chains that cannot be shortened by adding people. Only "
         "wave 2 absorbs extra engineers, and it does so in whole tracks: an "
         "engineer takes a track, not a slice of one.")

    table(doc,
          ["Engineers", "Elapsed", "What the extra person buys"],
          [
              ["1", "78 days", "—"],
              ["2", "53 days", "25 days. The largest single gain."],
              ["3", "44 days", "9 days."],
              ["4", "41 days", "3 days — one engineer still carries two tracks."],
              ["**5**", "**33 days**", "**8 days. One engineer per track — the "
                                       "natural size of this plan.**"],
              ["6 or more", "33 days", "Nothing. Wave 1, the longest track and "
                                       "wave 3 set the floor."],
          ],
          widths=[14, 16, 70])

    callout(doc,
            "**The decision in one line.** Five engineers is the natural team "
            "for this plan and finishes in about a third of the elapsed time "
            "one engineer would need. A sixth adds nothing. Two engineers is "
            "the best value per head if capacity is scarce.")

    para(doc,
         "One caution on the model: it assumes a clean barrier between waves, "
         "so no track starts before the contract is finished and nothing in "
         "wave 3 starts before every track is done. Real delivery overlaps at "
         "the edges, which would shorten every figure above. Treat these as the "
         "conservative end.",
         size=9)

    footer_note(doc,
                "AI Guardrails — Local-First Delivery Plan · Draft for approval")


SECTIONS = [
    executive_summary,
    delivery_roadmap,
    milestones_and_gates,
    deliverable_plan,
]
