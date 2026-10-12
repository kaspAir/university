"""Rules of the controlled research pilot (constitution Annex C, D, F; Art. 11, 17, 19, 20; architecture chapter 20, S5).

Pure rules, no database:
  * a protocol is pre-registered: question, stage, the three conditions of Annex C, repetitions, evaluation rule, hidden evaluation
    material, shared budget, required platform tests; its hash is frozen by a reserved human decision;
  * the pilot release gate (Annex F) checks mandate, roles, limits, platform evidence for the exact build, material and budget, and
    collects every failure instead of stopping at the first;
  * independent evaluation is blinded: the evaluator never sees condition, arm, task, project, institute or model identifiers;
  * the report derives arm facts from data, lists differences between conditions instead of hiding them, and never claims added value;
    with fewer completed repetitions than pre-registered it is a feasibility check and says so.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from statistics import mean

from .errors import CampusError, ErrorCode
from .statuses import MandateState

CONDITIONS = ("single_agent_self_check", "producer_reviewer_pair", "cross_institute")
STAGES = ("reproduction", "follow_up")
CAPABILITIES = ("internet", "sandbox", "factory", "workflow")
TEST_ID = re.compile(r"^[TA]\d{2}$")
FEASIBILITY_SENTENCE = "Machbarkeitsprüfung, kein allgemeiner Wirksamkeitsnachweis: nicht alle vorregistrierten Wiederholungen liegen vor."
NO_ADDED_VALUE_CLAIM = "Dieser Bericht behauptet keinen wissenschaftlichen Mehrwert einer Bedingung; er stellt Fakten je Bedingung und Unterschiede dar."


def validate_protocol(doc: dict) -> list[str]:
    problems: list[str] = []
    if not str(doc.get("question", "")).strip():
        problems.append("question fehlt")
    stage = doc.get("stage")
    if stage not in STAGES:
        problems.append("stage muss reproduction oder follow_up sein")
    if stage == "follow_up" and not doc.get("predecessor_pilot_id"):
        problems.append("follow_up braucht predecessor_pilot_id (abgeschlossene Reproduktion)")
    if stage == "reproduction":
        rep = doc.get("reproduction") or {}
        for key in ("source_ref", "expected_outputs", "tolerances"):
            if not str(rep.get(key, "")).strip():
                problems.append(f"reproduction.{key} fehlt (Datenzugang, erwartete Ausgaben, Toleranzen vorher festlegen)")
    conditions = [a.get("condition") for a in doc.get("arms") or []]
    if sorted(conditions) != sorted(CONDITIONS):
        problems.append("arms müssen genau die drei Bedingungen aus Anhang C je einmal enthalten")
    reps = doc.get("repetitions")
    if not isinstance(reps, int) or reps < 1:
        problems.append("repetitions muss eine ganze Zahl >= 1 sein (vor dem Vergleich festgelegt)")
    ev = doc.get("evaluation") or {}
    if not str(ev.get("suite_ref", "")).strip():
        problems.append("evaluation.suite_ref fehlt (verdeckte Prüffälle, getrennt verwahrt)")
    rule = ev.get("rule") or {}
    if rule.get("aggregation") != "mean_score" or not isinstance(rule.get("threshold_percent"), int) or not 0 <= rule["threshold_percent"] <= 100:
        problems.append("evaluation.rule braucht aggregation mean_score und threshold_percent 0..100")
    budget = doc.get("budget") or {}
    for key in ("total", "evaluation_reserve"):
        if not str(budget.get(key, "")).strip():
            problems.append(f"budget.{key} fehlt (gemeinsames Budget verhindert versteckten Ressourcenvergleich)")
    if not str(doc.get("data_access", "")).strip():
        problems.append("data_access fehlt")
    if not isinstance(doc.get("human_interventions_allowed"), list):
        problems.append("human_interventions_allowed muss eine Liste sein (darf leer sein)")
    tests = doc.get("required_tests")
    if not isinstance(tests, list) or not tests or any(not isinstance(t, str) or not TEST_ID.match(t) for t in tests):
        problems.append("required_tests: nicht leere Liste von Kennungen T01..T17 / A01..A14")
    caps = doc.get("capabilities_in_scope")
    if not isinstance(caps, list) or any(c not in CAPABILITIES for c in caps):
        problems.append(f"capabilities_in_scope muss eine Teilmenge von {list(CAPABILITIES)} sein")
    return problems


def preregistration_sha256(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReleaseContext:
    protocol_problems: list[str]
    mandate_state: MandateState | None
    director_ids: frozenset[str]
    integrity_officer_ids: frozenset[str]
    missing_limits: list[str]
    required_tests: list[str]
    evidence_build_sha: str | None
    evidence_covered: frozenset[str]
    evidence_failed: frozenset[str]
    current_build_sha: str | None
    suite_present: bool
    suite_protected: bool
    shared_budget_present: bool


def release_failures(ctx: ReleaseContext) -> list[str]:
    """Everything that blocks a pilot release, collected (Annex F: mandate, roles, limits, data paths, evidence)."""
    failures = list(ctx.protocol_problems)
    if ctx.mandate_state != MandateState.APPROVED:
        failures.append("Mandat nicht freigegeben (Art. 9)")
    if not ctx.director_ids:
        failures.append("Keine Forschungsleitung benannt")
    if not ctx.integrity_officer_ids:
        failures.append("Keine Integritätsfunktion benannt (Anhang F, F03)")
    if ctx.missing_limits:
        failures.append("Zahlenlimits fehlen in der aktiven Policy: " + ", ".join(ctx.missing_limits))
    if ctx.evidence_build_sha is None:
        failures.append("Kein Plattform-Testnachweis importiert (Anhang D: tragende Tests müssen bestanden sein)")
    else:
        if ctx.current_build_sha and ctx.evidence_build_sha != ctx.current_build_sha:
            failures.append(f"Testnachweis gehört zu Build {ctx.evidence_build_sha[:12]}, laufend ist {ctx.current_build_sha[:12]}")
        missing = sorted(set(ctx.required_tests) - ctx.evidence_covered)
        if missing:
            failures.append("Verlangte Tests ohne Nachweis im Bericht: " + ", ".join(missing) + " (Fähigkeit aus dem Pilotumfang nehmen oder Test liefern)")
        failed = sorted(set(ctx.required_tests) & ctx.evidence_failed)
        if failed:
            failures.append("Verlangte Tests im Bericht nicht bestanden: " + ", ".join(failed))
    if not ctx.suite_present:
        failures.append("Prüfmaterial für die unabhängige Bewertung fehlt")
    elif not ctx.suite_protected:
        failures.append("Prüfmaterial ist nicht geschützt; verdeckte Fälle müssen für Erzeuger unsichtbar sein (Art. 19)")
    if not ctx.shared_budget_present:
        failures.append("Gemeinsames Pilotbudget fehlt")
    return failures


def self_control(ctx: ReleaseContext) -> bool:
    """Research and control held by the same person is self-control and is shown as such (Art. 7, 17)."""
    return bool(ctx.director_ids & ctx.integrity_officer_ids)


def check_release(ctx: ReleaseContext) -> None:
    failures = release_failures(ctx)
    if failures:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Pilotfreigabe nicht möglich: " + "; ".join(failures), blocked_action="pilot.release",
                          details={"failures": failures}, required_resolution="Jeden Punkt beheben oder die betroffene Fähigkeit aus dem Umfang nehmen")


# ---------------------------------------------------------------- platform evidence (junit)

_ID_TOKEN = re.compile(r"(?<![a-z0-9])([ta])(\d{2})(?![0-9])", re.IGNORECASE)


@dataclass(frozen=True)
class EvidenceSummary:
    total: int
    passed: int
    failed: int
    skipped: int
    covered_ids: tuple[str, ...]
    failed_ids: tuple[str, ...]
    report_sha256: str


def parse_junit(xml_text: str) -> EvidenceSummary:
    """Reads a JUnit report: counts and the acceptance ids (T01..T17, A01..A14) named by passed and failed test cases.
    A test case counts as covering an id only when it passed; a skipped case covers nothing."""
    import xml.etree.ElementTree as ET  # stdlib; input is operator-supplied, size-capped and without DOCTYPE

    if len(xml_text) > 5 * 1024 * 1024 or "<!DOCTYPE" in xml_text.upper():
        raise CampusError(ErrorCode.VALIDATION_ERROR, "JUnit-Bericht zu gross oder mit DOCTYPE; abgewiesen")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise CampusError(ErrorCode.VALIDATION_ERROR, f"JUnit-Bericht nicht lesbar: {exc}") from exc
    total = passed = failed = skipped = 0
    covered: set[str] = set()
    failed_ids: set[str] = set()
    for case in root.iter("testcase"):
        total += 1
        name = f"{case.get('classname', '')} {case.get('name', '')}"
        ids = {f"{m.group(1).upper()}{m.group(2)}" for m in _ID_TOKEN.finditer(name)}
        if case.find("skipped") is not None:
            skipped += 1
            continue
        if case.find("failure") is not None or case.find("error") is not None:
            failed += 1
            failed_ids |= ids
            continue
        passed += 1
        covered |= ids
    covered -= failed_ids  # an id with any failing case is not covered
    if total == 0:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "JUnit-Bericht enthält keine Testfälle")
    return EvidenceSummary(total, passed, failed, skipped, tuple(sorted(covered)), tuple(sorted(failed_ids)),
                           hashlib.sha256(xml_text.encode("utf-8")).hexdigest())


# ---------------------------------------------------------------- blinding

def blinding_violations(texts: list[str], forbidden_tokens: list[str]) -> list[str]:
    """Tokens that would tell an evaluator which condition, arm, task, project, institute or model produced the output."""
    joined = "\n".join(texts).lower()
    return sorted({t for t in forbidden_tokens if t and len(t) >= 4 and t.lower() in joined})


# ---------------------------------------------------------------- arm facts, differences, report

@dataclass
class ArmFacts:
    condition: str
    institutes: set[str] = field(default_factory=set)
    producer_models: set[str] = field(default_factory=set)
    reviewer_models: set[str] = field(default_factory=set)
    tasks: int = 0
    completed_repetitions: int = 0
    runs: int = 0
    invocations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_settled_minor: int = 0
    cost_open_entries: int = 0
    runtime_seconds: float = 0.0
    claims_by_kind: dict[str, int] = field(default_factory=dict)
    flagged_claims: int = 0
    reviews: int = 0
    open_findings: int = 0
    experiments_measured: int = 0
    experiments_unmeasured: int = 0
    human_interventions: int = 0
    evaluation_scores: list[int] = field(default_factory=list)
    evaluations_open: int = 0


def arm_differences(arms: list[ArmFacts], human_interventions_allowed: list[str]) -> list[str]:
    """Differences between the conditions that the protocol wanted controlled: models, institutes, review presence, interventions."""
    out: list[str] = []
    by = {a.condition: a for a in arms}
    model_sets = {a.condition: (frozenset(a.producer_models), frozenset(a.reviewer_models)) for a in arms}
    if len(set(model_sets.values())) > 1:
        out.append("Modellzugänge unterscheiden sich zwischen den Bedingungen: " + "; ".join(
            f"{c}: Erzeuger {sorted(p) or '-'} / Prüfer {sorted(r) or '-'}" for c, (p, r) in sorted(model_sets.items())))
    cross = by.get("cross_institute")
    if cross is not None and len(cross.institutes) < 2:
        out.append(f"cross_institute arbeitet mit {len(cross.institutes)} Institut(en); institutsübergreifend verlangt mindestens zwei")
    single = by.get("single_agent_self_check")
    if single is not None and single.reviews > 0:
        out.append(f"single_agent_self_check hat {single.reviews} Gegenprüfung(en) durch ein anderes Modell; die Bedingung «Einzelagent mit Selbstprüfung» "
                   "ist auf dieser Plattform nur mit einer Policy ohne Prüfpflicht herstellbar (Art. 27; vorbehaltener Entscheid)")
    pair = by.get("producer_reviewer_pair")
    if pair is not None and pair.tasks and pair.reviews == 0:
        out.append("producer_reviewer_pair hat keine Gegenprüfung; die Bedingung ist nicht eingelöst")
    reps = {a.condition: a.completed_repetitions for a in arms}
    if len(set(reps.values())) > 1:
        out.append("Abgeschlossene Wiederholungen unterscheiden sich: " + ", ".join(f"{c}={n}" for c, n in sorted(reps.items())))
    interventions = {a.condition: a.human_interventions for a in arms}
    if any(interventions.values()) and not human_interventions_allowed:
        out.append("Menschliche Eingriffe trotz leerer Erlaubnisliste: " + ", ".join(f"{c}={n}" for c, n in sorted(interventions.items()) if n))
    elif len(set(interventions.values())) > 1:
        out.append("Menschliche Eingriffe unterscheiden sich: " + ", ".join(f"{c}={n}" for c, n in sorted(interventions.items())))
    return out


def rule_outcome(arm: ArmFacts, threshold_percent: int) -> dict:
    """The pre-registered rule applied to the independent evaluations of one arm. No evaluations: no outcome."""
    if not arm.evaluation_scores:
        return {"mean_score": None, "meets_threshold": None, "evaluations": 0, "open": arm.evaluations_open}
    m = mean(arm.evaluation_scores)
    return {"mean_score": round(m, 1), "meets_threshold": m >= threshold_percent, "evaluations": len(arm.evaluation_scores), "open": arm.evaluations_open}


def report_flags(repetitions: int, arms: list[ArmFacts]) -> dict:
    feasibility_only = any(a.completed_repetitions < repetitions for a in arms) or not arms
    return {"feasibility_only": feasibility_only, "statement": FEASIBILITY_SENTENCE if feasibility_only else
            "Alle vorregistrierten Wiederholungen liegen vor; die Aussagekraft richtet sich nach der vorregistrierten Regel.",
            "added_value_claim": NO_ADDED_VALUE_CLAIM}
