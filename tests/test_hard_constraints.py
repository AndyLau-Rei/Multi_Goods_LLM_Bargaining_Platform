import pytest

from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.models import Action, Proposal, Term
from bargaining_platform.scenario import load_scenario


def _proposal(proposal_id: str) -> Proposal:
    return Proposal(
        proposal_id=proposal_id,
        acceptance_mode="all_or_nothing",
        terms=[Term(type="purchase", item_id="A", quantity=1, unit_price=120, unit="piece")],
        conditions=[],
        decision_summary="Private seller note",
        original_prices={"A": 120},
        effective_prices={"A": 120},
    )


def _engine() -> NegotiationEngine:
    return NegotiationEngine(load_scenario("scenarios/shop_bundle.json"))


@pytest.mark.parametrize("quantities", [(21,), (11, 10)])
def test_rejects_quantity_above_stock(quantities):
    engine = _engine()
    offer = _proposal("offer-1")
    offer.terms = [Term("purchase", "A", quantity, 120, unit="piece") for quantity in quantities]

    result = engine.step(Action("PROPOSE", offer.proposal_id, offer))

    assert result.error_code == "INSUFFICIENT_STOCK"
    assert engine.state.active_proposal is None


def test_rejects_price_below_floor_without_leaking_floor():
    engine = _engine()
    offer = _proposal("offer-1")
    offer.terms[0].unit_price = 79
    offer.original_prices["A"] = 79
    offer.effective_prices["A"] = 79

    result = engine.step(Action("PROPOSE", offer.proposal_id, offer))

    assert result.error_code == "PRICE_BELOW_FLOOR"
    assert "80" not in result.message
    assert engine.state.active_proposal is None


def test_rejects_total_above_budget_without_leaking_budget():
    engine = _engine()
    offer = _proposal("offer-1")
    offer.terms[0] = Term("purchase", "A", 2, 120, unit="piece")
    offer.original_prices["A"] = 120
    offer.effective_prices["A"] = 120

    result = engine.step(Action("PROPOSE", offer.proposal_id, offer))

    assert result.error_code == "BUDGET_EXCEEDED"
    assert "200" not in result.message
    assert engine.state.active_proposal is None


def test_rejects_unowned_catalog_item():
    engine = _engine()
    offer = _proposal("offer-1")
    offer.terms[0].item_id = "C"

    result = engine.step(Action("PROPOSE", offer.proposal_id, offer))

    assert result.error_code == "INVALID_ITEM_ID"


def test_rejects_uncomputed_effective_price():
    engine = _engine()
    offer = _proposal("offer-1")
    offer.effective_prices["A"] = 1

    result = engine.step(Action("PROPOSE", offer.proposal_id, offer))

    assert result.error_code == "INVALID_PRICE"
    assert engine.state.active_proposal is None


def test_acceptance_rechecks_hard_constraints():
    engine = _engine()
    offer = _proposal("offer-1")
    engine.step(Action("PROPOSE", offer.proposal_id, offer))
    engine.state.active_proposal.terms[0].quantity = 21

    result = engine.step(Action("ACCEPT", offer.proposal_id))

    assert result.error_code == "INSUFFICIENT_STOCK"
    assert engine.state.outcome is None
    assert engine.state.current_actor == "buyer"
    assert len(engine.state.public_transcript) == 1
