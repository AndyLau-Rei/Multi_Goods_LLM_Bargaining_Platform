from decimal import Decimal

import pytest

from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.models import Action, Condition, Proposal, Term
from bargaining_platform.scenario import load_scenario
from bargaining_platform.validator import calculate_original_totals


def _proposal(*terms: Term) -> Proposal:
    prices = {term.item_id: term.unit_price for term in terms}
    return Proposal("offer-1", "all_or_nothing", list(terms), [], "", prices.copy(), prices.copy())


def test_single_item_original_total():
    proposal = _proposal(Term("purchase", "A", 1, 120, unit="piece"))

    assert calculate_original_totals(proposal, {"A": 5, "B": 5}) == {
        "line_totals": [Decimal("120")],
        "original_total": Decimal("120"),
    }


def test_multiple_items_have_exact_line_and_package_totals():
    proposal = _proposal(
        Term("purchase", "A", 2, 120, unit="piece"),
        Term("purchase", "B", 1, 60, unit="piece"),
    )

    assert calculate_original_totals(proposal, {"A": 5, "B": 5}) == {
        "line_totals": [Decimal("240"), Decimal("60")],
        "original_total": Decimal("300"),
    }


def test_decimal_prices_are_exact_and_no_discount_is_applied():
    proposal = _proposal(
        Term("purchase", "A", 1, 0.1, unit="piece"),
        Term("purchase", "B", 1, 0.2, unit="piece"),
    )
    proposal.conditions = [Condition("discount_if_item_in_bundle", "A", 0.05, "B", 1)]
    before = proposal.to_dict()

    totals = calculate_original_totals(proposal, {"A": 5, "B": 5})

    assert totals["original_total"] == Decimal("0.3")
    assert proposal.to_dict() == before
    assert proposal.effective_prices == proposal.original_prices


def test_legal_quantity_limit_applies_across_lines():
    proposal = _proposal(
        Term("purchase", "A", 3, 120, unit="piece"),
        Term("purchase", "A", 3, 120, unit="piece"),
    )

    with pytest.raises(ValueError, match="Quantity limit exceeded"):
        calculate_original_totals(proposal, {"A": 5, "B": 5})

    engine = NegotiationEngine(load_scenario("scenarios/shop_bundle.json"))
    result = engine.step(Action("PROPOSE", proposal.proposal_id, proposal))
    assert result.error_code == "QUANTITY_LIMIT_EXCEEDED"
    assert engine.state.active_proposal is None


def test_large_integer_and_mixed_decimal_totals_stay_exact():
    price = 10**28 + 1
    proposal = _proposal(Term("purchase", "A", 2, price, unit="piece"))

    totals = calculate_original_totals(proposal, {"A": 5, "B": 5})

    assert totals["line_totals"] == [2 * price]
    assert totals["original_total"] == 2 * price

    proposal.terms.append(Term("purchase", "B", 1, 0.1, unit="piece"))
    assert calculate_original_totals(
        proposal, {"A": 5, "B": 5}
    )["original_total"] == Decimal(f"{2 * price}.1")
