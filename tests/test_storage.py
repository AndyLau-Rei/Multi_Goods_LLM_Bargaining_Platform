import json
from decimal import Decimal

from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.models import Action, Condition, Proposal, Term, ValidationResult
from bargaining_platform.scenario import load_scenario
from bargaining_platform.storage import (
    export_run_to_jsonl,
    load_run_from_sqlite,
    save_run_to_sqlite,
)


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


def test_sqlite_persistence(tmp_path):
    engine = _engine()
    proposal = _proposal()
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))
    engine.step(Action("ACCEPT", proposal.proposal_id))

    database = tmp_path / "runs.db"
    save_run_to_sqlite(
        "run-1", load_scenario("scenarios/shop_bundle.json"), engine.state.outcome,
        engine.event_logger.events, database,
    )
    saved = load_run_from_sqlite("run-1", database)

    assert saved["outcome_status"] == "AGREEMENT"
    assert len(saved["events"]) == len(engine.event_logger.events)
    assert [event["event_id"] for event in saved["events"]] == [1, 2, 3, 4]
    assert saved["events"][0]["parsed_action"]["proposal"]["terms"][0]["item_id"] == "A"


def test_jsonl_export_preserves_details(tmp_path):
    engine = _engine()
    proposal = Proposal(
        "bundle-1",
        "all_or_nothing",
        [
            Term("purchase", "A", 1, 100, unit="piece"),
            Term("purchase", "B", 1, 60, unit="piece"),
        ],
        [Condition("discount_if_item_in_bundle", "A", 0.5, "B", 1)],
        "",
        {"A": 100, "B": 60},
        {"A": Decimal("99.5"), "B": 60},
    )
    engine.event_logger.append(
        "ACTION_ATTEMPT",
        "seller",
        parsed_action={"proposal": proposal.to_dict()},
        validation_result=ValidationResult(False, "CONDITION_NOT_SATISFIED", "invalid").to_dict(),
    )

    output = tmp_path / "run.jsonl"
    export_run_to_jsonl("run-2", engine.event_logger.events, output)
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    record = records[-1]
    assert record["run_id"] == "run-2"
    assert record["parsed_action"]["proposal"]["conditions"][0]["type"] == (
        "discount_if_item_in_bundle"
    )
    assert record["parsed_action"]["proposal"]["effective_prices"]["A"] == "99.5"
    assert record["validation_result"]["error_code"] == "CONDITION_NOT_SATISFIED"


def test_decimal_prices_are_json_serializable_in_public_transcript():
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
    assert json.loads(serialized)[0]["proposal"]["original_prices"]["A"] == 100.0
    assert state.active_proposal.effective_prices["A"] == Decimal("99.5")
