from copy import deepcopy

from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.models import Action, Condition, Proposal, Term
from bargaining_platform.scenario import load_scenario
from bargaining_platform.validator import calculate_effective_totals


def _proposal(discount=20, required_quantity=1, terms=None) -> Proposal:
    terms = terms or [
        Term("purchase", "A", 1, 120, unit="piece"),
        Term("purchase", "B", 1, 60, unit="piece"),
    ]
    prices = {term.item_id: term.unit_price for term in terms}
    return Proposal(
        "bundle-1",
        "all_or_nothing",
        terms,
        [Condition("discount_if_item_in_bundle", "A", discount, "B", required_quantity)],
        "",
        prices.copy(),
        prices.copy(),
    )


def _engine() -> NegotiationEngine:
    return NegotiationEngine(load_scenario("scenarios/shop_bundle.json"))


def test_condition_satisfied_applies_discount():
    engine = _engine()
    proposal = _proposal()
    before = deepcopy(proposal)

    totals = calculate_effective_totals(proposal, engine.state)

    assert totals["original_total"] == 180
    assert totals["effective_prices"] == {"A": 100, "B": 60}
    assert totals["effective_line_totals"] == [100, 60]
    assert totals["effective_total"] == 160
    assert proposal == before


def test_condition_missing_required_item_is_rejected():
    engine = _engine()
    proposal = _proposal(terms=[Term("purchase", "A", 1, 120, unit="piece")])
    proposal.effective_prices = proposal.original_prices.copy()

    result = engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    assert result.error_code == "CONDITION_REFERENCES_MISSING_ITEM"
    assert engine.state.active_proposal is None
    assert engine.state.public_transcript == []


def test_condition_required_quantity_is_insufficient():
    engine = _engine()
    proposal = _proposal(required_quantity=2)

    result = engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    assert result.error_code == "CONDITION_NOT_SATISFIED"
    assert engine.state.active_proposal is None


def test_discount_exceeding_scenario_cap_is_rejected():
    engine = _engine()
    proposal = _proposal(discount=31)

    result = engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    assert result.error_code == "DISCOUNT_EXCEEDS_CAP"
    assert engine.state.active_proposal is None
    assert "30" not in result.message


def test_discount_below_seller_floor_is_rejected():
    engine = _engine()
    proposal = _proposal(discount=25)
    proposal.terms[0].unit_price = 100
    proposal.original_prices["A"] = 100
    proposal.effective_prices["A"] = 100

    result = engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    assert result.error_code == "DISCOUNT_BELOW_FLOOR"
    assert engine.state.active_proposal is None
    assert "80" not in result.message


def test_engine_stores_computed_effective_prices():
    engine = _engine()
    proposal = _proposal()

    state = engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    assert state.active_proposal.effective_prices == {"A": 100, "B": 60}
    assert state.public_transcript[0]["proposal"]["effective_prices"] == {"A": 100, "B": 60}
    assert proposal.effective_prices == {"A": 120, "B": 60}
