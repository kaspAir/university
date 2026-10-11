from __future__ import annotations

import pytest

from campus_domain.errors import CampusError, ErrorCode
from campus_domain.qualification import CaseResult, activation_allowed, aggregate, check_independence, check_tool_scopes, is_regression


def _r(cid, passed):
    return CaseResult(cid, passed, 100 if passed else 0, ("x",), "low", "0" * 64, "fixture/model-b@1")


def test_aggregate_share_and_threshold():
    assert aggregate([], 50) == (0, False)
    assert aggregate([_r("a", True), _r("b", False)], 50) == (50, True)
    assert aggregate([_r("a", True), _r("b", False)], 51) == (50, False)
    assert aggregate([_r("a", True)], 100) == (100, True)


def test_t12_independence_rules():
    with pytest.raises(CampusError) as excinfo:
        check_independence(candidate_canonical="m/a", evaluator_canonical="m/a", evaluator_qualified=True)
    assert excinfo.value.code == ErrorCode.REVIEW_INDEPENDENCE_VIOLATED
    with pytest.raises(CampusError) as excinfo:
        check_independence(candidate_canonical="m/a", evaluator_canonical="m/b", evaluator_qualified=False)
    assert excinfo.value.code == ErrorCode.QUALIFICATION_MISSING
    with pytest.raises(CampusError):
        check_independence(candidate_canonical=None, evaluator_canonical="m/b", evaluator_qualified=True)
    check_independence(candidate_canonical="m/a", evaluator_canonical="m/b", evaluator_qualified=True)


def test_t17_regression_and_activation():
    assert is_regression(50, 100) is True and is_regression(100, 100) is False and is_regression(50, None) is False
    assert activation_allowed(passed=True, regression=False) == (True, "bestanden ohne Rückschritt")
    assert activation_allowed(passed=False, regression=False)[0] is False
    assert "T17" in activation_allowed(passed=True, regression=True)[1]


def test_a10_tool_scopes():
    check_tool_scopes(["sources.read", "claims.propose"], what="x")
    with pytest.raises(CampusError) as excinfo:
        check_tool_scopes(["sources.read", "secrets.read"], what="Skill")
    assert excinfo.value.code == ErrorCode.FORBIDDEN and "A10" in excinfo.value.message
