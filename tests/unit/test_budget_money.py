from __future__ import annotations

import pytest

from campus_domain.budget import Pricing, check_child_limit, check_reservation, estimate_cost
from campus_domain.errors import CampusError, ErrorCode
from campus_domain.money import Money, parse_money


def test_parse_and_format_chf() -> None:
    assert parse_money("6800", "CHF") == Money(680000, "CHF")
    assert parse_money("1200.50", "CHF").format() == "CHF 1 200.50"
    assert Money(680000, "CHF").format() == "CHF 6 800"


def test_excess_decimals_are_rejected_not_rounded() -> None:
    with pytest.raises(CampusError) as excinfo:
        parse_money("1.005", "CHF")
    assert excinfo.value.code == ErrorCode.VALIDATION_ERROR


def test_currency_mix_is_an_error() -> None:
    with pytest.raises(CampusError):
        Money(1, "CHF") + Money(1, "EUR")


def test_a14_unknown_price_is_never_zero() -> None:
    with pytest.raises(CampusError) as excinfo:
        estimate_cost(None, 1000, 100)
    assert excinfo.value.code == ErrorCode.PRICING_UNKNOWN


def test_estimate_rounds_up_per_component() -> None:
    pricing = Pricing("p", "CHF", input_per_million_minor=1000, output_per_million_minor=5000)
    assert estimate_cost(pricing, 1, 1) == Money(2, "CHF")  # 0.001 + 0.005 minor -> 1 + 1
    assert estimate_cost(pricing, 1_000_000, 200_000) == Money(2000, "CHF")


def test_t03_child_limit_cannot_exceed_parent_free_amount() -> None:
    check_child_limit("B-root", 1000, 1000)
    with pytest.raises(CampusError) as excinfo:
        check_child_limit("B-root", 1000, 1001)
    assert excinfo.value.code == ErrorCode.BUDGET_EXHAUSTED
    assert excinfo.value.blocked_action == "budget.create_child"


def test_reservation_beyond_available_is_blocked() -> None:
    check_reservation("B-1", 500, 500)
    with pytest.raises(CampusError) as excinfo:
        check_reservation("B-1", 500, 501)
    assert excinfo.value.code == ErrorCode.BUDGET_EXHAUSTED
