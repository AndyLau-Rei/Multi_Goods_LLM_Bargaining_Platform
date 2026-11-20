import copy
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


def test_missing_item_id_is_rejected():
    engine = _engine()
    missing_item = _proposal("offer-1")
    missing_item.terms[0].item_id = "missing"
    result = engine.step(Action("PROPOSE", missing_item.proposal_id, missing_item))
    assert result.error_code == "INVALID_ITEM_ID"

@pytest.mark.parametrize("quantity", [0, -1])
def test_invalid_quantity_is_rejected(quantity):
    engine = _engine()
    invalid_quantity = _proposal("offer-1")
    invalid_quantity.terms[0] = Term("purchase", "A", quantity, 120, unit="piece")
    result = engine.step(Action("PROPOSE", invalid_quantity.proposal_id, invalid_quantity))
    assert result.error_code == "INVALID_QUANTITY"
    assert engine.state.active_proposal is None


def test_term_validation_preserves_error_precedence():
    engine = _engine()
    offer = _proposal("offer-1")
    offer.terms[0] = Term("service", "missing", 0, -1, unit="wrong")

    result = engine.step(Action("PROPOSE", offer.proposal_id, offer))

    assert result.error_code == "INVALID_TERM_TYPE"


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
    assert valid.active_proposal == offer
    assert valid.active_proposal is not offer
    assert valid.outcome is None
    assert valid.invalid_attempts == 0
    assert len(valid.public_transcript) == 1
    assert len(valid.private_attempt_log) == 2


def test_repeated_invalid_actions_do_not_end_or_count_against_the_game():
    engine = _engine()
    offer = _proposal("offer-1")
    engine.step(Action("PROPOSE", offer.proposal_id, offer, actor="buyer"))
    result = engine.step(Action("PROPOSE", offer.proposal_id, offer, actor="buyer"))

    assert result.error_code == "WRONG_TURN"
    assert engine.state.outcome is None
    assert engine.state.invalid_attempts == 0
    assert engine.state.active_proposal is None
    assert engine.state.public_transcript == []
    assert len(engine.state.private_attempt_log) == 2
    assert not any(event.event_type == "STATE_TRANSITION" for event in engine.event_logger.events)


def test_action_must_be_allowed_for_role():
    scenario = load_scenario("scenarios/shop_bundle.json")
    scenario["private_profiles"]["seller"]["allowed_actions"] = ["PROPOSE"]
    engine = NegotiationEngine(scenario)
    first = _proposal("offer-1")
    second = _proposal("offer-2")
    third = _proposal("offer-3")
    engine.step(Action("PROPOSE", first.proposal_id, first))
    engine.step(Action("COUNTER", first.proposal_id, second))

    result = engine.step(Action("COUNTER", second.proposal_id, third))

    assert result.error_code == "ACTION_NOT_ALLOWED"
    assert engine.state.active_proposal == second
    assert engine.state.current_actor == "seller"


def test_acceptance_rejects_unmet_or_missing_reservation():
    engine = _engine()
    offer = _proposal("offer-1")
    engine.step(Action("PROPOSE", offer.proposal_id, offer))

    result = engine.step(Action("ACCEPT", offer.proposal_id))

    assert result.error_code == "RESERVATION_UTILITY_NOT_MET"
    assert "100" not in result.message and "150" not in result.message
    assert engine.state.outcome.status == "NO_DEAL"

    engine = _engine()
    engine.step(Action("PROPOSE", offer.proposal_id, offer))
    del engine.state.private_profiles["buyer"]["reservation_utility"]
    result = engine.step(Action("ACCEPT", offer.proposal_id))
    assert result.error_code == "UTILITY_NOT_IMPLEMENTED"
