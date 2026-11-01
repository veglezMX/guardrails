"""Actual HTTPS/PG Rules coverage of inherited content constraints.

Parent-only integration execution. No deployed cluster/model quality claim.
The ancestors here are tenant-owned, never a fabricated global publication.
"""
import pytest
from composition_support import (
    KEY, MAIL, SLOTS, STAGES, approve, audited_isolation, clients,
    evaluate, fingerprint, fresh_pair, layered, listing, rule, text_parts,
)
from guardrails.evolution.errors import EvolutionError


@pytest.mark.parametrize("slot", SLOTS)
@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("scenario", (
    "intersection", "normalized-intersection", "individual-allows-are-not-union",
    "root-deny", "tenant-deny", "workload-deny", "all-denies", "allow-deny-overlap", "blank-part",
))
def test_inherited_intersection_union_and_per_part_constraints(tmp_path, slot, stage, scenario):
    with fresh_pair(tmp_path / "scopes") as pair:
        entries = {name: layered(system) for name, system in pair.items()}
        system, lists = pair[slot], entries[slot]
        assert system["policy"]["rules"] == []
        inputs = {
            "intersection": "common ordinary", "normalized-intersection": "ＣＯＭＭＯＮ ordinary",
            "individual-allows-are-not-union": "parent-only tenant-only workload-only",
            "root-deny": "common rootdeny", "tenant-deny": "common tenantdeny",
            "workload-deny": "common workloaddeny", "all-denies": "common rootdeny tenantdeny workloaddeny",
            "allow-deny-overlap": "overlap", "blank-part": "common ordinary",
        }
        parts = text_parts(inputs[scenario], stage, second="" if scenario == "blank-part" else None)
        with clients(pair, tmp_path / "tls") as opened:
            decision = evaluate(opened[slot], system, stage, parts)
            clean = scenario in {"intersection", "normalized-intersection"}
            assert decision.action == decision.would_be_action == ("allow" if clean else "block")
            assert decision.evaluator_revisions == ("lexical-v1",)
            assert decision.transformed_parts is None
            if clean:
                assert decision.findings == () and decision.reason_codes == ()
            else:
                allows = scenario in {"individual-allows-are-not-union", "blank-part"}
                assert decision.reason_codes == (("content_not_allowed",) if allows else ("content_denied",))
                indices = {"root-deny": (0,), "tenant-deny": (1,), "workload-deny": (2,),
                           "allow-deny-overlap": (2,)}.get(scenario, (0, 1, 2))
                assert {found.rule_id for found in decision.findings} == {
                    "content-list:" + lists[index].list_id for index in indices}
                assert len(decision.findings) == len(indices)
                assert all(found.part_id == ("second" if scenario == "blank-part" else "content")
                           and found.evaluator_revision == "lexical-v1" for found in decision.findings)
            audited_isolation(pair, slot, opened[slot], decision)


@pytest.mark.parametrize("slot", SLOTS)
@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("state", ("disabled", "observe", "warn", "redact", "block"))
def test_zero_authored_rules_follow_binding_state_with_persisted_audit(tmp_path, slot, stage, state):
    with fresh_pair(tmp_path / "scopes") as pair:
        entries = {name: layered(system, state=state) for name, system in pair.items()}
        with clients(pair, tmp_path / "tls") as opened:
            decision = evaluate(opened[slot], pair[slot], stage, text_parts("common rootdeny", stage))
            assert pair[slot]["policy"]["rules"] == []
            assert decision.action == {"disabled": "allow", "observe": "allow"}.get(state, state)
            assert decision.would_be_action == ("allow" if state == "disabled" else "block")
            if state == "disabled":
                assert decision.findings == () and decision.evaluator_revisions == ()
                assert decision.reason_codes == ("disabled",)
            else:
                assert len(decision.findings) == 1
                assert decision.findings[0].rule_id == "content-list:" + entries[slot][0].list_id
                assert decision.evaluator_revisions == ("lexical-v1",)
                assert decision.reason_codes == ("content_denied",)
            if state == "redact":
                assert fingerprint([part.model_dump(mode="json", exclude_none=True) for part in decision.transformed_parts]) == fingerprint(text_parts("common [REDACTED:term]", stage))
            else:
                assert decision.transformed_parts is None
            audited_isolation(pair, slot, opened[slot], decision)


@pytest.mark.parametrize("slot", SLOTS)
@pytest.mark.parametrize("stage", STAGES)
def test_allowed_content_never_exempts_real_pii_or_secret_detectors(tmp_path, slot, stage):
    chosen = (rule("mail", "pii", "block", stage, {"entities": ["email"]}),
              rule("secret", "secret", "block", stage, {"detectors": ["aws_access_key"]}))
    with fresh_pair(tmp_path / "scopes") as pair:
        for system in pair.values():
            layered(system, rules=chosen)
        with clients(pair, tmp_path / "tls") as opened:
            decision = evaluate(opened[slot], pair[slot], stage, text_parts("common " + MAIL + " " + KEY, stage))
            assert decision.action == decision.would_be_action == "block"
            assert {found.rule_id for found in decision.findings} == {"mail", "secret"}
            assert {found.category for found in decision.findings} == {"email", "aws_access_key"}
            assert decision.reason_codes == ("pii_detected", "secret_detected")
            assert decision.evaluator_revisions == ("lexical-v1", "pii-v2", "secrets-v1")
            assert decision.transformed_parts is None
            audited_isolation(pair, slot, opened[slot], decision, forbidden=(MAIL, KEY))


@pytest.mark.parametrize("slot", SLOTS)
@pytest.mark.parametrize("scenario", ("empty-enabled-allow", "disjoint-inherited-allows"))
def test_unusable_content_intersection_rejects_before_policy_or_evaluation(tmp_path, slot, scenario):
    with fresh_pair(tmp_path / "scopes") as pair:
        system = pair[slot]
        parent = listing(system, allow=("term:parent",), enabled=True)
        ancestor = approve(system, "nonempty-parent", lists=(parent,), content_ids=(parent.list_id,))
        child = listing(system, allow=() if scenario == "empty-enabled-allow" else ("term:other",), enabled=True)
        with pytest.raises(EvolutionError) as refused:
            approve(system, "unusable-child", lists=(child,),
                    content_ids=(parent.list_id, child.list_id), parents=(ancestor[1],))
        assert refused.value.code == "composition_conflict"
        with system["repo"].transaction(system["tenant"]) as tx:
            assert tx.get("policies", "unusable-child:1") is None
            assert len(tx.list("quota_leases")) == len(tx.list("evaluation_contexts")) == 0
            assert len([row for row in tx.list("audit") if row["event_type"] == "evaluation"]) == 0

