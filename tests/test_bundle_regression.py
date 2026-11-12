import json

from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.models import Action, Condition, Proposal, Term, ValidationResult
from bargaining_platform.scenario import load_scenario
from bargaining_platform.validator import calculate_effective_totals


def _engine() -> NegotiationEngine:
    return NegotiationEngine(load_scenario("scenarios/shop_bundle.json"))


def _proposal(
    terms: list[Term],
    conditions: list[Condition] | None = None,
) -> Proposal:
    prices = {term.item_id: term.unit_price for term in terms}
    return Proposal(
        "regression-1",
        "all_or_nothing",
        terms,
        conditions or [],
        "",
        prices.copy(),
        prices.copy(),
    )


def _a_term(quantity: int = 1, price: int = 120) -> Term:
    return Term("purchase", "A", quantity, price, unit="piece")


def _b_term(quantity: int = 1, price: int = 60) -> Term:
    return Term("purchase", "B", quantity, price, unit="piece")


def _discount(discount: int = 20, required_quantity: int = 1) -> Condition:
    return Condition("discount_if_item_in_bundle", "A", discount, "B", required_quantity)


def test_missing_required_item_in_bundle():
    engine = _engine()
    proposal = _proposal([_a_term()], [_discount()])

    result = engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    assert result.error_code == "CONDITION_REFERENCES_MISSING_ITEM"


def test_nonexistent_item_in_proposal():
    engine = _engine()
    proposal = _proposal([Term("purchase", "C", 1, 120, unit="piece")])

    result = engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    assert result.error_code == "INVALID_ITEM_ID"


def test_excess_quantity_exceeds_stock_or_legal_range():
    engine = _engine()
    proposal = _proposal([_a_term(quantity=6)])

    result = engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    assert result.error_code == "QUANTITY_LIMIT_EXCEEDED"


def test_discount_below_seller_floor():
    engine = _engine()
    proposal = _proposal(
        [_a_term(price=100), _b_term()],
        [_discount(discount=25)],
    )

    result = engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    assert result.error_code == "DISCOUNT_BELOW_FLOOR"


def test_scenario_cap_violation():
    engine = _engine()
    proposal = _proposal([_a_term(), _b_term()], [_discount(discount=31)])

    result = engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    assert result.error_code == "DISCOUNT_EXCEEDS_CAP"


def test_stale_proposal_id():
    engine = _engine()
    proposal = _proposal([_a_term()])
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    result = engine.step(Action("ACCEPT", "old-proposal-id"))

    assert result.error_code == "STALE_PROPOSAL_ID"


def test_duplicate_conditions():
    """Duplicate conditions aggregate per item and the aggregate cap rejects them."""
    engine = _engine()
    proposal = _proposal([_a_term(), _b_term()], [_discount(), _discount()])

    result = engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    assert result.error_code == "DISCOUNT_EXCEEDS_CAP"


def test_zero_or_negative_quantity():
    for quantity in (0, -1):
        engine = _engine()
        proposal = _proposal([_a_term(quantity=quantity)])

        result = engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

        assert result.error_code == "INVALID_QUANTITY"


def test_malformed_term_returns_controlled_validation_result():
    engine = _engine()
    proposal = _proposal([Term("purchase", None, None, 120, unit="piece")])

    result = calculate_effective_totals(proposal, engine.state)

    assert isinstance(result, ValidationResult)
    assert result.ok is False
    assert result.error_code == "MALFORMED_TERM"


def test_decimal_prices_are_json_serializable_as_strings():
    engine = _engine()
    proposal = Proposal(
        "decimal-1",
        "all_or_nothing",
        [
            Term("purchase", "A", 1, 100.0, unit="piece"),
            Term("purchase", "B", 1, 60.0, unit="piece"),
        ],
        [Condition("discount_if_item_in_bundle", "A", 0.5, "B", 1)],
        "",
        {"A": 100.0, "B": 60.0},
        {"A": 100.0, "B": 60.0},
    )

    state = engine.step(Action("PROPOSE", proposal.proposal_id, proposal))
    serialized = json.dumps(state.public_transcript)

    assert "99.5" in serialized
    assert json.loads(serialized)[0]["proposal"]["effective_prices"]["A"] == "99.5"
