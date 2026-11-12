import json

import pytest

from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.models import Action, Proposal, Term
from bargaining_platform.prompt_builder import make_private_view
from bargaining_platform.scenario import load_scenario


def _proposal() -> Proposal:
    return Proposal(
        proposal_id="offer-1",
        acceptance_mode="all_or_nothing",
        terms=[Term("purchase", "A", 1, 120, unit="piece")],
        conditions=[],
        decision_summary="Hidden seller decision summary",
        original_prices={"A": 120},
        effective_prices={"A": 120},
    )


def _state():
    engine = NegotiationEngine(load_scenario("scenarios/shop_bundle.json"))
    proposal = _proposal()
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))
    return engine.state


def test_buyer_view_excludes_seller_profile():
    view = make_private_view("buyer", _state())
    serialized = json.dumps(view, sort_keys=True)

    assert view["private_profile"]["budget"] == 200
    assert "unit_cost" not in serialized
    assert "minimum_unit_price" not in serialized
    assert "70" not in serialized
    assert "80" not in serialized


def test_seller_view_excludes_buyer_profile():
    view = make_private_view("seller", _state())
    serialized = json.dumps(view, sort_keys=True)

    assert view["private_profile"]["unit_cost"]["A"] == 70
    assert "budget" not in serialized
    assert "unit_value" not in serialized
    assert "200" not in serialized
    assert "130" not in serialized


def test_views_contain_detached_public_state_without_private_summary():
    state = _state()
    buyer_view = make_private_view("buyer", state)
    seller_view = make_private_view("seller", state)

    for view in (buyer_view, seller_view):
        assert view["catalog"] == state.catalog
        assert view["active_proposal"]["proposal_id"] == "offer-1"
        assert view["public_transcript"]
        assert view["remaining_rounds"] == 8
        assert "decision_summary" not in view["active_proposal"]
        assert "decision_summary" not in json.dumps(view["public_transcript"])

    buyer_view["private_profile"]["budget"] = 0
    buyer_view["catalog"][0]["id"] = "changed"
    buyer_view["active_proposal"]["proposal_id"] = "changed"
    buyer_view["public_transcript"][0]["actor"] = "changed"

    assert state.private_profiles["buyer"]["budget"] == 200
    assert state.catalog[0]["id"] == "A"
    assert state.active_proposal.proposal_id == "offer-1"
    assert state.public_transcript[0]["actor"] == "seller"


def test_purchase_term_rejects_arbitrary_private_details():
    with pytest.raises(TypeError):
        Term("purchase", "A", 1, 120, details={"unit_cost": 70}, unit="piece")
    with pytest.raises(TypeError):
        Term.from_dict({"type": "purchase", "item_id": "A", "quantity": 1,
                        "unit_price": 120, "unit": "piece", "details": {"unit_cost": 70}})
