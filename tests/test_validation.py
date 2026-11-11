import copy

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


def test_invalid_turn_ownership():
    engine = _engine()
    offer = _proposal("offer-1")
    result = engine.step(Action("PROPOSE", offer.proposal_id, offer, actor="buyer"))

    assert result.error_code == "WRONG_TURN"
    assert engine.state.active_proposal is None
    assert engine.state.current_actor == "seller"


def test_stale_proposal_id():
    engine = _engine()
    first = _proposal("offer-1")
    second = _proposal("offer-2")
    engine.step(Action("PROPOSE", first.proposal_id, first))
    engine.step(Action("COUNTER", first.proposal_id, second))
    before = copy.deepcopy(engine.state.active_proposal)

    result = engine.step(Action("ACCEPT", first.proposal_id))

    assert result.error_code == "STALE_PROPOSAL_ID"
    assert engine.state.active_proposal == before
    assert engine.state.current_actor == "seller"


def test_missing_item_id_or_invalid_quantity():
    engine = _engine()
    missing_item = _proposal("offer-1")
    missing_item.terms[0].item_id = "missing"
    result = engine.step(Action("PROPOSE", missing_item.proposal_id, missing_item))
    assert result.error_code == "INVALID_ITEM_ID"

    engine = _engine()
    invalid_quantity = _proposal("offer-1")
    invalid_quantity.terms[0] = Term("purchase", "A", 0, 120, unit="piece")
    result = engine.step(Action("PROPOSE", invalid_quantity.proposal_id, invalid_quantity))
    assert result.error_code == "INVALID_QUANTITY"


def test_malformed_nested_term_returns_validation_error():
    engine = _engine()
    offer = _proposal("offer-1")
    offer.terms = [None]

    result = engine.step(Action("PROPOSE", offer.proposal_id, offer))

    assert result.error_code == "INVALID_TERM"
    assert engine.state.active_proposal is None


def test_retry_policy_allows_one_invalid_action():
    engine = _engine()
    offer = _proposal("offer-1")
    invalid = engine.step(Action("PROPOSE", offer.proposal_id, offer, actor="buyer"))
    valid = engine.step(Action("PROPOSE", offer.proposal_id, offer))

    assert invalid.error_code == "WRONG_TURN"
    assert valid.active_proposal is offer
    assert valid.outcome is None
    assert valid.invalid_attempts == 0
    assert len(valid.public_transcript) == 1
    assert len(valid.private_attempt_log) == 2


def test_retry_exhaustion_terminates():
    engine = _engine()
    offer = _proposal("offer-1")
    engine.step(Action("PROPOSE", offer.proposal_id, offer, actor="buyer"))
    result = engine.step(Action("PROPOSE", offer.proposal_id, offer, actor="buyer"))

    assert result.error_code == "WRONG_TURN"
    assert engine.state.outcome.status == "INVALID_ACTION"
    assert engine.state.active_proposal is None
    assert engine.state.public_transcript == []
    assert len(engine.state.private_attempt_log) == 2
