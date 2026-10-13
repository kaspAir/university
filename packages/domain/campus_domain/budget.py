"""Budget rules (architecture chapter 16). Reservation, call and settlement are separate phases.

Pure arithmetic and checks live here; the locking transaction lives in the infrastructure service.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from .errors import CampusError, ErrorCode
from .money import Money


@dataclass(frozen=True)
class Pricing:
    """A versioned price snapshot. Amounts per million tokens in minor units."""

    pricing_ref: str
    currency: str
    input_per_million_minor: int
    output_per_million_minor: int


def estimate_cost(pricing: Pricing | None, input_tokens: int, output_tokens: int) -> Money:
    """Conservative reservation estimate. Unknown price is an error, never zero (A14)."""
    if pricing is None:
        raise CampusError(
            ErrorCode.PRICING_UNKNOWN,
            "Für dieses Deployment liegt keine gültige Preisfassung vor; Kosten bleiben offen",
            blocked_action="budget.reserve",
            required_resolution="Preisfassung (pricing_ref) im Katalog hinterlegen",
        )
    if input_tokens < 0 or output_tokens < 0:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Tokenzahlen dürfen nicht negativ sein")
    amount = ceil(input_tokens * pricing.input_per_million_minor / 1_000_000) + ceil(
        output_tokens * pricing.output_per_million_minor / 1_000_000
    )
    return Money(int(amount), pricing.currency)


def available_minor(limit_minor: int, reserved_minor: int, settled_minor: int) -> int:
    return limit_minor - reserved_minor - settled_minor


def check_reservation(account_id: str, available: int, amount_minor: int) -> None:
    if amount_minor <= 0:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Reservierungsbetrag muss positiv sein")
    if amount_minor > available:
        raise CampusError(
            ErrorCode.BUDGET_EXHAUSTED,
            f"Budgetkonto {account_id}: verfügbar {available}, verlangt {amount_minor} (Minor-Einheiten)",
            blocked_action="budget.reserve",
            required_resolution="Mittel freigeben, Mandat erweitern (menschlicher Entscheid) oder Aufgabe verkleinern",
        )


def check_child_limit(parent_account_id: str, parent_available: int, child_limit_minor: int) -> None:
    """A child budget is a covered allocation, never new money (Art. 10, T03)."""
    if child_limit_minor <= 0:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Kindbudget muss positiv sein")
    if child_limit_minor > parent_available:
        raise CampusError(
            ErrorCode.BUDGET_EXHAUSTED,
            f"Kindbudget {child_limit_minor} übersteigt den freien Betrag {parent_available} des Elternkontos {parent_account_id}",
            blocked_action="budget.create_child",
            required_resolution="Kindbudget verkleinern oder Elternbudget durch Mandatsänderung erhöhen",
        )


def affordable_output_tokens(pricing: Pricing, budget_minor: int, input_tokens: int) -> int | None:
    """Output tokens the given budget can still pay for after the input, at the bound price. This is the runtime upper bound for a
    model answer (computed from measured quantities, never a fixed number). None when output is free of charge (no monetary bound);
    zero when the input alone exhausts the budget."""
    if input_tokens < 0 or budget_minor < 0:
        raise CampusError(ErrorCode.VALIDATION_ERROR, "Budget und Tokenzahlen dürfen nicht negativ sein")
    if pricing.output_per_million_minor <= 0:
        return None
    remaining = budget_minor - ceil(input_tokens * pricing.input_per_million_minor / 1_000_000)
    return max(0, int(remaining * 1_000_000 // pricing.output_per_million_minor))

