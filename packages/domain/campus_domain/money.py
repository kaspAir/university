"""Money as integer minor units plus ISO currency code (ADR-0003). No binary floats."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .errors import CampusError, ErrorCode

MINOR_UNITS: dict[str, int] = {"CHF": 2, "EUR": 2, "USD": 2, "GBP": 2}
NARROW_NBSP = " "


@dataclass(frozen=True)
class Money:
    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        if self.currency not in MINOR_UNITS:
            raise CampusError(ErrorCode.VALIDATION_ERROR, f"Unbekannte Währung: {self.currency}")
        if not isinstance(self.amount_minor, int) or isinstance(self.amount_minor, bool):
            raise CampusError(ErrorCode.VALIDATION_ERROR, "Betrag muss eine Ganzzahl in Minor-Einheiten sein")

    def __add__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def _same(self, other: Money) -> None:
        if other.currency != self.currency:
            raise CampusError(
                ErrorCode.VALIDATION_ERROR,
                f"Währungen verschieden: {self.currency} vs. {other.currency}; "
                "eine Umrechnung muss mit Kurs protokolliert werden",
            )

    def as_decimal(self) -> Decimal:
        return Decimal(self.amount_minor).scaleb(-MINOR_UNITS[self.currency])

    def format(self) -> str:
        """UI convention of the handoff: «CHF 6 800», narrow no-break space as thousands separator."""
        units = MINOR_UNITS[self.currency]
        sign = "-" if self.amount_minor < 0 else ""
        major, minor = divmod(abs(self.amount_minor), 10**units)
        major_text = f"{major:,}".replace(",", NARROW_NBSP)
        if minor:
            return f"{sign}{self.currency} {major_text}.{minor:0{units}d}"
        return f"{sign}{self.currency} {major_text}"


def parse_money(text: str | Decimal | int, currency: str) -> Money:
    """Parse a decimal string such as '1200.50' exactly; excess decimals are rejected, never rounded away."""
    if currency not in MINOR_UNITS:
        raise CampusError(ErrorCode.VALIDATION_ERROR, f"Unbekannte Währung: {currency}")
    try:
        value = Decimal(str(text))
    except InvalidOperation as exc:
        raise CampusError(ErrorCode.VALIDATION_ERROR, f"Kein gültiger Betrag: {text!r}") from exc
    scaled = value.scaleb(MINOR_UNITS[currency])
    if scaled != scaled.to_integral_value(rounding=ROUND_HALF_UP):
        raise CampusError(
            ErrorCode.VALIDATION_ERROR, f"Betrag {text!r} hat mehr Nachkommastellen als {currency} erlaubt"
        )
    return Money(int(scaled), currency)
