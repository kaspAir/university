"""S2 and S1.1 domain rules: internet targets (T07, T15, T16), claim classification (T04), dossier binding (T08),
common invocation guard, endpoint binding, review consistency."""
from __future__ import annotations

import pytest

from campus_contracts.models import ReviewReport
from campus_domain import sources
from campus_domain.claims import classify
from campus_domain.dossier import check_approval_binding, document_hash, release_allowed
from campus_domain.errors import CampusError, ErrorCode
from campus_domain.guards import InvocationContext, check_invocation
from campus_domain.statuses import EpistemicStatus, MandateState
from campus_gateway.endpoints import check_api_base

# ---------------------------------------------------------------- sources

@pytest.mark.parametrize("ip, reason", [
    ("127.0.0.1", "LOOPBACK"), ("10.0.0.5", "PRIVATE_NETWORK"), ("192.168.1.10", "PRIVATE_NETWORK"), ("172.16.0.1", "PRIVATE_NETWORK"),
    ("169.254.169.254", "LINK_LOCAL_OR_METADATA"), ("::1", "LOOPBACK"), ("fd00::1", "PRIVATE_NETWORK"), ("::ffff:10.1.1.1", "PRIVATE_NETWORK"),
    ("0.0.0.0", "RESERVED"), ("100.64.0.1", "CARRIER_GRADE_NAT"), ("224.0.0.1", "MULTICAST"),
])
def test_t07_internal_addresses_are_blocked(ip, reason):
    assert sources.classify_address(ip) == reason


def test_public_addresses_pass():
    assert sources.classify_address("93.184.216.34") is None
    assert sources.classify_address("2606:2800:220:1:248:1893:25c8:1946") is None


def test_url_normalization_and_scheme_port_rules():
    t = sources.normalize_url("HTTPS://Example.ORG:443/Pfad?x=1#frag")
    assert t.url == "https://example.org/Pfad?x=1" and t.port == 443
    for bad in ("file:///etc/passwd", "ftp://example.org/x", "https://user:pw@example.org/", "http://example.org:8080/"):
        with pytest.raises(CampusError) as excinfo:
            sources.normalize_url(bad)
        assert excinfo.value.code in {ErrorCode.DATA_EXPORT_DENIED, ErrorCode.VALIDATION_ERROR}


def test_t07_hostnames_and_resolution():
    with pytest.raises(CampusError):
        sources.check_target(sources.normalize_url("http://localhost/"), ["93.184.216.34"])
    with pytest.raises(CampusError):
        sources.check_target(sources.normalize_url("https://db.internal/"), ["93.184.216.34"])
    with pytest.raises(CampusError, match="T07"):
        sources.check_target(sources.normalize_url("https://public.example/"), ["93.184.216.34", "10.0.0.9"])  # any private address blocks
    sources.check_target(sources.normalize_url("https://public.example/"), ["93.184.216.34"])


def test_t16_query_export_depends_on_task_labels():
    target = sources.normalize_url("https://public.example/search?q=geheim")
    sources.export_check(target, ["public"])
    with pytest.raises(CampusError) as excinfo:
        sources.export_check(target, ["public", "confidential"])
    assert excinfo.value.code == ErrorCode.DATA_EXPORT_DENIED
    sources.export_check(sources.normalize_url("https://public.example/page"), ["confidential"])  # no query, nothing leaves


# ---------------------------------------------------------------- claims

def test_t04_measurement_without_evidence_becomes_flagged_hypothesis():
    c = classify("computation", valid_evidence_count=0, invalid_evidence_count=0)
    assert c.stored_kind == "hypothesis" and c.epistemic_status == EpistemicStatus.HYPOTHESIS and "EVIDENCE_MISSING" in c.flags
    c = classify("observation", 1, 0)
    assert c.stored_kind == "observation" and c.epistemic_status == EpistemicStatus.OBSERVATION and not c.flags
    c = classify("observation", 0, 2)
    assert "INVALID_EVIDENCE_REF" in c.flags and "EVIDENCE_MISSING" in c.flags
    assert classify("legal_assessment", 3, 0).epistemic_status == EpistemicStatus.UNRESOLVED  # consensus is not law


# ---------------------------------------------------------------- dossier / approvals

def test_t08_approval_binds_the_exact_hash():
    doc = {"b": 2, "a": [1, 2], "nested": {"x": "y"}}
    h = document_hash(doc)
    assert h == document_hash({"nested": {"x": "y"}, "a": [1, 2], "b": 2})  # canonical: order does not matter
    check_approval_binding(h, h)
    with pytest.raises(CampusError) as excinfo:
        check_approval_binding(h, document_hash({**doc, "b": 3}))
    assert excinfo.value.code == ErrorCode.VERSION_CONFLICT
    release_allowed(approval_hash=h, artifact_hash=h, approval_option="accept_limited")
    with pytest.raises(CampusError):
        release_allowed(approval_hash=None, artifact_hash=h, approval_option=None)
    with pytest.raises(CampusError):
        release_allowed(approval_hash=h, artifact_hash="0" * 64, approval_option="accept")
    with pytest.raises(CampusError):
        release_allowed(approval_hash=h, artifact_hash=h, approval_option="reject")


# ---------------------------------------------------------------- common invocation guard

def _ctx(**over) -> InvocationContext:
    base = dict(purpose="selector", workspace_stopped=False, mandate_state=MandateState.APPROVED, task_hold_reason=None,
                route_state="authorized", reservation_present=True)
    base.update(over)
    return InvocationContext(**base)


@pytest.mark.parametrize("over, code", [
    ({"workspace_stopped": True}, ErrorCode.WORKSPACE_STOPPED),
    ({"mandate_state": MandateState.DRAFT}, ErrorCode.MANDATE_NOT_APPROVED),
    ({"task_hold_reason": "Schutzverweigerung ungeklärt"}, ErrorCode.TASK_HELD),
    ({"route_state": "proposed"}, ErrorCode.ROUTE_NOT_AUTHORIZED),
    ({"reservation_present": False}, ErrorCode.BUDGET_EXHAUSTED),
])
def test_every_model_call_shares_the_same_guard(over, code):
    check_invocation(_ctx())
    with pytest.raises(CampusError) as excinfo:
        check_invocation(_ctx(**over))
    assert excinfo.value.code == code and excinfo.value.blocked_action == "llm.invoke:selector"


# ---------------------------------------------------------------- endpoint binding

def test_keys_only_travel_to_approved_endpoints(monkeypatch):
    check_api_base("mock", "https://anything.example")
    check_api_base("anthropic", None)
    check_api_base("anthropic", "https://api.anthropic.com/")
    with pytest.raises(CampusError) as excinfo:
        check_api_base("anthropic", "https://evil.example/v1")
    assert excinfo.value.code == ErrorCode.ENDPOINT_NOT_APPROVED
    with pytest.raises(CampusError):
        check_api_base("openai_compatible", None)
    monkeypatch.setenv("CAMPUS_APPROVED_API_HOSTS", "llm.intern.example")
    check_api_base("openai_compatible", "https://llm.intern.example/v1")
    with pytest.raises(CampusError):
        check_api_base("openai_compatible", "http://llm.intern.example/v1")  # https required
    with pytest.raises(CampusError):
        check_api_base("openai_compatible", "https://other.example/v1")


# ---------------------------------------------------------------- review consistency

def test_contradictory_review_report_is_detected():
    from campus_api.services_research import review_consistency

    ok = ReviewReport(verdict="no_objection", checked_aspects=["x"], findings=[], uncertainties=[])
    assert review_consistency(ok) == (True, None)
    bad = ReviewReport(verdict="no_objection", checked_aspects=["x"], uncertainties=[],
                       findings=[{"subject": "s", "argument": "a", "severity": "blocking", "scope": "all"}])
    consistent, why = review_consistency(bad)
    assert consistent is False and "no_objection" in why
    empty = ReviewReport(verdict="objections", checked_aspects=["x"], findings=[], uncertainties=[])
    assert review_consistency(empty)[0] is False
