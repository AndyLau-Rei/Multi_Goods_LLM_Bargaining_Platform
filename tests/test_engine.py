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


def _run(engine: NegotiationEngine, seller: list[Action], buyer: list[Action]):
    scripts = {"seller": iter(seller), "buyer": iter(buyer)}
    while engine.state.outcome is None:
        engine.step(next(scripts[engine.state.current_actor]))
    return engine.state


def test_agreement():
    engine = NegotiationEngine(load_scenario("scenarios/shop_bundle.json"))
    offer = _proposal("offer-1")
    state = _run(engine, [Action("PROPOSE", offer.proposal_id, offer)], [Action("ACCEPT", offer.proposal_id)])

    assert state.outcome.status == "AGREEMENT"
    assert state.outcome.final_proposal_id == offer.proposal_id
    assert state.active_proposal is offer
    assert [entry["actor"] for entry in state.public_transcript] == ["seller", "buyer"]
    assert "decision_summary" not in state.public_transcript[0]["proposal"]
    with pytest.raises(RuntimeError, match="ended"):
        engine.step(Action("WALK_AWAY"))


def test_counteroffer():
    engine = NegotiationEngine(load_scenario("scenarios/shop_bundle.json"))
    first = _proposal("offer-1")
    counter = _proposal("offer-2")
    state = _run(
        engine,
        [Action("PROPOSE", first.proposal_id, first), Action("ACCEPT", counter.proposal_id)],
        [Action("COUNTER", first.proposal_id, counter)],
    )

    assert state.outcome.status == "AGREEMENT"
    assert state.outcome.final_proposal_id == counter.proposal_id
    assert state.active_proposal is counter
    assert state.round_number == 2
    assert [entry["actor"] for entry in state.public_transcript] == ["seller", "buyer", "seller"]


def test_walk_away():
    engine = NegotiationEngine(load_scenario("scenarios/shop_bundle.json"))
    offer = _proposal("offer-1")
    state = _run(engine, [Action("PROPOSE", offer.proposal_id, offer)], [Action("WALK_AWAY")])

    assert state.outcome.status == "NO_DEAL"
    assert len(state.public_transcript) == 2


def test_timeout():
    scenario = load_scenario("scenarios/shop_bundle.json")
    scenario["max_rounds"] = 2
    engine = NegotiationEngine(scenario)
    offers = [_proposal(f"offer-{number}") for number in range(1, 5)]
    state = _run(
        engine,
        [Action("PROPOSE", "offer-1", offers[0]), Action("COUNTER", "offer-2", offers[2])],
        [Action("COUNTER", "offer-1", offers[1]), Action("COUNTER", "offer-3", offers[3])],
    )

    assert state.outcome.status == "MAX_ROUNDS"
    assert state.round_number == 2
    assert state.active_proposal is offers[3]
    assert len(state.public_transcript) == 4
