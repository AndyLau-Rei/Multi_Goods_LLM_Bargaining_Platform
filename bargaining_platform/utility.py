"""Calculate merchandise utility and acceptance reservation checks."""

from decimal import Decimal
from fractions import Fraction

from .models import GameState, Proposal, ValidationResult
from .validator import calculate_effective_totals


def calculate_buyer_utility(proposal: Proposal, state: GameState) -> Decimal:
    """Return buyer value for received goods minus the effective payment."""
    return calculate_utilities(proposal, state)["buyer_utility"]


def calculate_seller_utility(proposal: Proposal, state: GameState) -> Decimal:
    """Return effective payment minus seller fulfillment costs."""
    return calculate_utilities(proposal, state)["seller_utility"]


def calculate_utilities(proposal: Proposal, state: GameState) -> dict[str, Decimal]:
    """Return buyer, seller, and joint merchandise utility exactly."""
    totals = _totals(proposal, state)
    payment = Fraction(Decimal(str(totals["effective_total"])))
    buyer_values = state.private_profiles["buyer"]["unit_value"]
    seller_costs = state.private_profiles["seller"]["unit_cost"]
    value = sum(
        (
            term.quantity * Fraction(Decimal(str(buyer_values[term.item_id])))
            for term in proposal.terms
        ),
        Fraction(0),
    )
    cost = sum(
        (
            term.quantity * Fraction(Decimal(str(seller_costs[term.item_id])))
            for term in proposal.terms
        ),
        Fraction(0),
    )
    buyer = _decimal(value - payment)
    seller = _decimal(payment - cost)
    return {
        "buyer_utility": buyer,
        "seller_utility": seller,
        "joint_utility": _decimal(value - cost),
    }


def validate_reservation_utilities(proposal: Proposal, state: GameState) -> ValidationResult:
    """Reject acceptance when either role misses its configured reservation utility."""
    seller = state.private_profiles["seller"]
    buyer = state.private_profiles["buyer"]
    if "reservation_utility" not in seller or "reservation_utility" not in buyer:
        return ValidationResult(
            False, "UTILITY_NOT_IMPLEMENTED",
            "Acceptance requirements cannot be checked",
        )
    utilities = calculate_utilities(proposal, state)
    if (
        utilities["buyer_utility"] < Decimal(str(buyer["reservation_utility"]))
        or utilities["seller_utility"] < Decimal(str(seller["reservation_utility"]))
    ):
        return ValidationResult(
            False,
            "RESERVATION_UTILITY_NOT_MET",
            "The proposal does not meet acceptance requirements",
        )
    return ValidationResult(ok=True)


def _totals(proposal: Proposal, state: GameState) -> dict:
    totals = calculate_effective_totals(proposal, state)
    if isinstance(totals, ValidationResult):
        raise ValueError(totals.message)
    return totals


def _decimal(value: Fraction) -> Decimal:
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ValueError("Utility is not a terminating decimal")
    places = max(twos, fives)
    scaled = value.numerator * 10**places // value.denominator
    return Decimal(f"{scaled}e-{places}")
