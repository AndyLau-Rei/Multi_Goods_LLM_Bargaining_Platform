import pytest

from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.models import Action, Proposal, Term
from bargaining_platform.prompt_builder import make_private_view, render_prompt
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


def test_allows_price_below_floor_until_acceptance():
    engine = _engine()
    offer = _proposal("offer-1")
    offer.terms[0].unit_price = 79
    offer.original_prices["A"] = 79
    offer.effective_prices["A"] = 79

    result = engine.step(Action("PROPOSE", offer.proposal_id, offer))

    assert result.active_proposal.terms[0].unit_price == 79
    assert engine.state.current_actor == "buyer"


def test_invalid_acceptance_returns_to_proposer_for_revision():
    engine = _engine()
    opening = _proposal("opening")
    counter = _proposal("counter")
    counter.terms[0].unit_price = 79
    counter.original_prices["A"] = 79
    counter.effective_prices["A"] = 79

    engine.step(Action("PROPOSE", opening.proposal_id, opening))
    engine.step(Action("COUNTER", opening.proposal_id, counter))
    result = engine.step(Action("ACCEPT", counter.proposal_id))

    assert result.error_code == "PRICE_BELOW_FLOOR"
    assert engine.state.outcome is None
    assert engine.state.current_actor == "buyer"
    assert engine.state.active_proposal.proposal_id == "counter"


def test_seller_offer_above_buyer_budget_is_public_but_acceptance_is_rejected():
    engine = NegotiationEngine(load_scenario("scenarios/single_product.json"))
    offer = Proposal(
        "offer-1500", "all_or_nothing",
        [Term("purchase", "A", 1, 1500, unit="piece")],
        [], "Private seller note", {"A": 1500}, {"A": 1500},
    )

    proposed = engine.step(Action("PROPOSE", offer.proposal_id, offer))

    assert proposed is engine.state
    assert engine.state.current_actor == "buyer"
    assert engine.state.active_proposal.proposal_id == offer.proposal_id
    assert engine.state.public_transcript[0]["proposal"]["terms"][0]["unit_price"] == 1500
    assert "1300" not in render_prompt("seller", make_private_view("seller", engine.state))

    rejected = engine.step(Action("ACCEPT", offer.proposal_id))

    assert rejected.error_code == "BUDGET_EXCEEDED"
    assert "1300" not in rejected.message
    assert "1300" not in str(engine.state.public_transcript)
    assert engine.state.outcome is None
    assert engine.state.current_actor == "seller"
    assert len(engine.state.public_transcript) == 1


def test_seller_acceptance_does_not_reveal_buyer_budget():
    engine = NegotiationEngine(load_scenario("scenarios/single_product.json"))
    first = Proposal(
        "offer-1", "all_or_nothing",
        [Term("purchase", "A", 1, 1200, unit="piece")],
        [], "", {"A": 1200}, {"A": 1200},
    )
    counter = Proposal(
        "offer-2", "all_or_nothing",
        [Term("purchase", "A", 1, 1500, unit="piece")],
        [], "", {"A": 1500}, {"A": 1500},
    )
    engine.step(Action("PROPOSE", first.proposal_id, first))
    engine.step(Action("COUNTER", first.proposal_id, counter))

    rejected = engine.step(Action("ACCEPT", counter.proposal_id))

    assert rejected.error_code == "ACCEPTANCE_REQUIREMENTS_NOT_MET"
    assert "budget" not in rejected.message.lower()
    assert "1300" not in rejected.message
    assert engine.state.outcome is None


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
    assert engine.state.current_actor == "seller"
    assert len(engine.state.public_transcript) == 1
