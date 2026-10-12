"""Pilot rules without a database (Annex C, D, F; Art. 11, 17, 19)."""
from __future__ import annotations

import pytest

from campus_domain.errors import CampusError
from campus_domain.pilot import (
    FEASIBILITY_SENTENCE,
    ArmFacts,
    ReleaseContext,
    arm_differences,
    blinding_violations,
    check_release,
    parse_junit,
    preregistration_sha256,
    release_failures,
    report_flags,
    rule_outcome,
    self_control,
    validate_protocol,
)
from campus_domain.statuses import MandateState


def protocol(**over) -> dict:
    doc = {"question": "Welche zusätzliche Aussagequalität erzielt getrennte Prüfung?", "stage": "reproduction",
           "reproduction": {"source_ref": "fixture-source-1", "expected_outputs": "Kennzahl X = 42 ± 1", "tolerances": "±1 absolut"},
           "arms": [{"condition": c} for c in ("single_agent_self_check", "producer_reviewer_pair", "cross_institute")], "repetitions": 2,
           "evaluation": {"suite_ref": "pilot-eval", "rule": {"aggregation": "mean_score", "threshold_percent": 60}},
           "budget": {"total": "30.00", "evaluation_reserve": "6.00"}, "data_access": "nur Fixture-Quellen, öffentlich",
           "human_interventions_allowed": [], "required_tests": ["T04", "T05", "T12"], "capabilities_in_scope": ["workflow"]}
    doc.update(over)
    return doc


def test_protocol_validation_names_every_gap() -> None:
    assert validate_protocol(protocol()) == []
    problems = validate_protocol(protocol(arms=[{"condition": "producer_reviewer_pair"}], repetitions=0, required_tests=["X1"], stage="follow_up",
                                          capabilities_in_scope=["magic"]))
    joined = " ".join(problems)
    for needle in ("drei Bedingungen", "repetitions", "required_tests", "predecessor_pilot_id", "capabilities_in_scope"):
        assert needle in joined, (needle, problems)


def test_preregistration_hash_changes_with_the_protocol() -> None:
    a, b = protocol(), protocol(repetitions=3)
    assert preregistration_sha256(a) == preregistration_sha256(protocol()) and preregistration_sha256(a) != preregistration_sha256(b)


def _ctx(**over) -> ReleaseContext:
    base = {"protocol_problems": [], "mandate_state": MandateState.APPROVED, "director_ids": frozenset({"u1"}), "integrity_officer_ids": frozenset({"u2"}),
            "missing_limits": [], "required_tests": ["T04", "T05"], "evidence_build_sha": "abc1234", "evidence_covered": frozenset({"T04", "T05", "T12"}),
            "evidence_failed": frozenset(), "current_build_sha": "abc1234", "suite_present": True, "suite_protected": True, "shared_budget_present": True}
    base.update(over)
    return ReleaseContext(**base)


def test_release_gate_collects_all_failures() -> None:
    assert release_failures(_ctx()) == []
    failures = release_failures(_ctx(mandate_state=MandateState.DRAFT, integrity_officer_ids=frozenset(), evidence_build_sha=None, suite_protected=False,
                                     missing_limits=["max_task_cost"]))
    assert len(failures) == 5, failures
    with pytest.raises(CampusError) as e:
        check_release(_ctx(evidence_covered=frozenset({"T04"})))
    assert "T05" in e.value.message and e.value.details["failures"]
    assert any("nicht bestanden" in f for f in release_failures(_ctx(evidence_failed=frozenset({"T04"}))))
    assert any("Build" in f for f in release_failures(_ctx(evidence_build_sha="other12")))


def test_self_control_is_shown_not_hidden() -> None:
    assert not self_control(_ctx())
    assert self_control(_ctx(integrity_officer_ids=frozenset({"u1"})))
    assert release_failures(_ctx(integrity_officer_ids=frozenset({"u1"}))) == [], "self-control blocks nothing; it is disclosed"


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="5">
<testcase classname="tests.integration.test_s3_factory" name="test_t12_candidate_cannot_certify_itself" time="0.1"/>
<testcase classname="tests.integration.test_s2_flow" name="test_t04_measurement_without_run" time="0.1"/>
<testcase classname="tests.integration.test_s3_factory" name="test_t05_protected_material" time="0.1"><failure message="x">boom</failure></testcase>
<testcase classname="tests.live" name="test_t15_live" time="0"><skipped message="no key"/></testcase>
<testcase classname="tests.unit" name="test_a10_tool_scopes" time="0.1"/>
</testsuite></testsuites>"""


def test_junit_parsing_covers_only_passed_ids() -> None:
    s = parse_junit(JUNIT)
    assert (s.total, s.passed, s.failed, s.skipped) == (5, 3, 1, 1)
    assert s.covered_ids == ("A10", "T04", "T12") and s.failed_ids == ("T05",), "a skipped test covers nothing, a failed id is not covered"
    with pytest.raises(CampusError):
        parse_junit("<!DOCTYPE x [<!ENTITY a 'b'>]><testsuites/>")
    with pytest.raises(CampusError):
        parse_junit("<testsuites></testsuites>")


def test_blinding_detects_condition_and_identifier_leaks() -> None:
    texts = ["Aussage A über Methode", "entstanden unter producer_reviewer_pair mit fixture/model-a@1"]
    leaks = blinding_violations(texts, ["producer_reviewer_pair", "fixture/model-a@1", "T-123", "ab"])
    assert leaks == ["fixture/model-a@1", "producer_reviewer_pair"], "short tokens are ignored, real identifiers are found"
    assert blinding_violations(["nur Inhalt"], ["cross_institute"]) == []


def test_differences_name_what_the_protocol_wanted_controlled() -> None:
    single = ArmFacts("single_agent_self_check", institutes={"i1"}, producer_models={"m-a"}, reviewer_models={"m-b"}, tasks=1, reviews=1, completed_repetitions=1)
    pair = ArmFacts("producer_reviewer_pair", institutes={"i1"}, producer_models={"m-a"}, reviewer_models={"m-b"}, tasks=1, reviews=1, completed_repetitions=1)
    cross = ArmFacts("cross_institute", institutes={"i1"}, producer_models={"m-a"}, reviewer_models={"m-b"}, tasks=1, reviews=1, completed_repetitions=1, human_interventions=1)
    diffs = arm_differences([single, pair, cross], human_interventions_allowed=[])
    joined = " ".join(diffs)
    assert "single_agent_self_check hat 1 Gegenprüfung" in joined and "cross_institute arbeitet mit 1 Institut" in joined and "Menschliche Eingriffe trotz" in joined
    assert "Modellzugänge unterscheiden" not in joined
    pair.reviews = 0
    assert any("keine Gegenprüfung" in d for d in arm_differences([pair], []))


def test_report_never_claims_added_value_and_flags_feasibility() -> None:
    arms = [ArmFacts("producer_reviewer_pair", completed_repetitions=1, evaluation_scores=[100, 50])]
    flags = report_flags(2, arms)
    assert flags["feasibility_only"] and flags["statement"] == FEASIBILITY_SENTENCE and "keinen wissenschaftlichen Mehrwert" in flags["added_value_claim"]
    assert rule_outcome(arms[0], 60) == {"mean_score": 75.0, "meets_threshold": True, "evaluations": 2, "open": 0}
    assert rule_outcome(ArmFacts("cross_institute"), 60)["meets_threshold"] is None
    assert not report_flags(1, arms)["feasibility_only"]
