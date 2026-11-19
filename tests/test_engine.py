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


def _engine_with_feasible_reservations() -> NegotiationEngine:
    scenario = load_scenario("scenarios/shop_bundle.json")
    scenario["private_profiles"]["seller"]["reservation_utility"] = 0
    scenario["private_profiles"]["buyer"]["reservation_utility"] = 0
    return NegotiationEngine(scenario)


def test_agreement():
    engine = _engine_with_feasible_reservations()
    offer = _proposal("offer-1")
    state = _run(
        engine,
        [Action("PROPOSE", offer.proposal_id, offer)],
        [Action("ACCEPT", offer.proposal_id)],
    )

    assert state.outcome.status == "AGREEMENT"
    assert state.outcome.final_proposal_id == offer.proposal_id
    assert state.active_proposal == offer
    assert state.active_proposal is not offer
    assert [entry["actor"] for entry in state.public_transcript] == ["seller", "buyer"]
    assert "decision_summary" not in state.public_transcript[0]["proposal"]
    with pytest.raises(RuntimeError, match="ended"):
        engine.step(Action("WALK_AWAY"))


def test_counteroffer():
    engine = _engine_with_feasible_reservations()
    first = _proposal("offer-1")
    counter = _proposal("offer-2")
    state = _run(
        engine,
        [Action("PROPOSE", first.proposal_id, first), Action("ACCEPT", counter.proposal_id)],
        [Action("COUNTER", first.proposal_id, counter)],
    )

    assert state.outcome.status == "AGREEMENT"
    assert state.outcome.final_proposal_id == counter.proposal_id
    assert state.active_proposal == counter
    assert state.active_proposal is not counter
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
    assert state.active_proposal == offers[3]
    assert state.active_proposal is not offers[3]
    assert len(state.public_transcript) == 4


def test_event_round_numbers_advance_only_after_two_valid_responses():
    engine = _engine_with_feasible_reservations()
    opening = _proposal("offer-1")
    counter = _proposal("offer-2")

    engine.step(Action("PROPOSE", opening.proposal_id, opening))
    invalid = engine.step(Action("ACCEPT", "stale-id"))
    assert invalid.error_code == "STALE_PROPOSAL_ID"
    engine.step(Action("COUNTER", opening.proposal_id, counter))
    engine.step(Action("ACCEPT", counter.proposal_id))

    events = engine.event_logger.events
    assert [event.round_number for event in events] == [1, 1, 1, 1, 1, 2, 2]


def test_offer_is_snapshotted_before_acceptance():
    engine = _engine_with_feasible_reservations()
    offer = _proposal("offer-1")
    engine.step(Action("PROPOSE", offer.proposal_id, offer))
    offer.terms[0].unit_price = 80

    state = engine.step(Action("ACCEPT", offer.proposal_id))

    assert state.outcome.status == "AGREEMENT"
    assert engine.state.active_proposal.terms[0].unit_price == 120
    assert engine.state.public_transcript[0]["proposal"]["terms"][0]["unit_price"] == 120
