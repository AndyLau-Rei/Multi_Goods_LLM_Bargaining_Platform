from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.models import Action, Proposal, Term
from bargaining_platform.scenario import load_scenario


def _engine() -> NegotiationEngine:
    scenario = load_scenario("scenarios/shop_bundle.json")
    scenario["private_profiles"]["buyer"]["reservation_utility"] = 0
    scenario["private_profiles"]["seller"]["reservation_utility"] = 0
    return NegotiationEngine(scenario)


def _proposal(summary: str = "") -> Proposal:
    return Proposal(
        "event-1",
        "all_or_nothing",
        [Term("purchase", "A", 1, 120, unit="piece")],
        [],
        summary,
        {"A": 120},
        {"A": 120},
    )


def test_event_log_append_only():
    engine = _engine()
    proposal = _proposal()
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))
    engine.step(Action("ACCEPT", proposal.proposal_id))

    events = engine.event_logger.events

    assert [event.event_id for event in events] == [1, 2, 3, 4]
    assert [event.event_type for event in events] == [
        "ACTION_ATTEMPT",
        "STATE_TRANSITION",
        "ACTION_ATTEMPT",
        "STATE_TRANSITION",
    ]
    assert events[1].state_transition_summary == "Proposal updated; turn switched to buyer"
    assert events[-1].state_transition_summary == "Proposal accepted; game ended"
    assert isinstance(events, tuple)


def test_invalid_action_logged():
    engine = _engine()
    proposal = _proposal()

    result = engine.step(Action("PROPOSE", proposal.proposal_id, proposal, actor="buyer"))

    assert result.error_code == "WRONG_TURN"
    events = engine.event_logger.events
    assert len(events) == 1
    assert events[0].event_type == "ACTION_ATTEMPT"
    assert events[0].validation_result["ok"] is False
    assert events[0].validation_result["error_code"] == "WRONG_TURN"


def test_private_summary_isolation():
    engine = _engine()
    proposal = _proposal("seller-only decision")
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    event = engine.event_logger.events[0]

    assert event.private_decision_summary == "seller-only decision"
    assert "decision_summary" not in event.parsed_action["proposal"]
    assert "decision_summary" not in engine.state.public_transcript[0]["proposal"]
    assert "seller-only decision" not in str(engine.state.public_transcript)
