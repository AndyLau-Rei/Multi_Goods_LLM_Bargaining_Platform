import json
import sqlite3
from dataclasses import replace

import pytest

from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.models import Action, Condition, Outcome, Proposal, Term, ValidationResult
from bargaining_platform.scenario import load_scenario
from bargaining_platform.storage import (
    ReplayDivergenceError,
    load_run_from_sqlite,
    replay_run,
    save_run_to_sqlite,
)


def _run(tmp_path):
    scenario = load_scenario("scenarios/shop_bundle.json")
    scenario["private_profiles"]["seller"]["reservation_utility"] = 0
    scenario["private_profiles"]["buyer"]["reservation_utility"] = 0
    engine = NegotiationEngine(scenario)
    proposal = Proposal(
        "replay-bundle",
        "all_or_nothing",
        [
            Term("purchase", "A", 1, 120, unit="piece"),
            Term("purchase", "B", 1, 60, unit="piece"),
        ],
        [Condition("discount_if_item_in_bundle", "A", 20, "B", 1)],
        "",
        {"A": 120, "B": 60},
        {"A": 120, "B": 60},
    )
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))
    engine.step(Action("ACCEPT", proposal.proposal_id))
    database = tmp_path / "replay.db"
    save_run_to_sqlite(
        "run-1", scenario, engine.state.outcome, engine.event_logger.events, database
    )
    return database, engine


def test_deterministic_replay_success(tmp_path):
    database, original = _run(tmp_path)

    replayed = replay_run("run-1", database)

    assert replayed.outcome.to_dict() == original.state.outcome.to_dict()
    assert replayed.public_transcript == original.state.public_transcript
    assert replayed.active_proposal.effective_prices == {"A": 100, "B": 60}


def test_replay_reads_existing_prompt_metadata(tmp_path):
    database, original = _run(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE events SET metadata_json = ? WHERE run_id = ? AND event_id = 1",
            (json.dumps({"prompt": "legacy private prompt"}), "run-1"),
        )

    assert load_run_from_sqlite("run-1", database)["events"][0]["metadata"]["prompt"] == "legacy private prompt"
    assert replay_run("run-1", database).outcome.to_dict() == original.state.outcome.to_dict()


def test_replay_detects_tampering(tmp_path):
    database, _ = _run(tmp_path)
    with sqlite3.connect(database) as connection:
        parsed = json.loads(
            connection.execute(
                "SELECT parsed_action_json FROM events WHERE run_id = ? AND event_id = 1",
                ("run-1",),
            ).fetchone()[0]
        )
        parsed["proposal"]["terms"][0]["unit_price"] = 121
        parsed["proposal"]["original_prices"]["A"] = 121
        connection.execute(
            "UPDATE events SET parsed_action_json = ? WHERE run_id = ? AND event_id = 1",
            (json.dumps(parsed), "run-1"),
        )

    with pytest.raises(ReplayDivergenceError):
        replay_run("run-1", database)


def test_replay_legacy_budget_rejection_checks_raw_action(tmp_path):
    scenario = load_scenario("scenarios/single_product.json")
    engine = NegotiationEngine(scenario)
    proposal = Proposal(
        "legacy-offer", "all_or_nothing",
        [Term("purchase", "A", 1, 1500, unit="piece")],
        [], "", {"A": 1500}, {"A": 1500},
    )
    action = Action("PROPOSE", proposal.proposal_id, proposal, actor="seller")
    rejection = ValidationResult(
        False, "BUDGET_EXCEEDED", "The proposed total exceeds the allowed budget"
    ).to_dict()
    raw_action = action.to_dict()
    raw_action.pop("actor")
    raw = json.dumps(raw_action)
    attempt = engine.event_logger.append(
        "ACTION_ATTEMPT", "seller", raw_output=raw,
        parsed_action=action.to_dict(), validation_result=rejection,
    )
    terminal = engine.event_logger.append(
        "STATE_TRANSITION", "seller",
        state_transition_summary="Game ended: invalid action retry exhausted",
    )
    database = tmp_path / "legacy.db"
    save_run_to_sqlite(
        "legacy", scenario, Outcome("INVALID_ACTION"),
        (attempt, replace(attempt, event_id=2), replace(terminal, event_id=3)), database,
    )

    assert replay_run("legacy", database).outcome.status == "INVALID_ACTION"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE events SET raw_output_json = ? WHERE run_id = ? AND event_id = 1",
            (json.dumps(raw.replace("1500", "1200")), "legacy"),
        )
    with pytest.raises(ReplayDivergenceError):
        replay_run("legacy", database)


def test_replay_legacy_rejected_acceptance_retries(tmp_path):
    scenario = load_scenario("scenarios/single_product.json")
    engine = NegotiationEngine(scenario)
    proposal = Proposal(
        "legacy-accept", "all_or_nothing",
        [Term("purchase", "A", 1, 900, unit="piece")],
        [], "", {"A": 900}, {"A": 900},
    )
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))
    action = Action("ACCEPT", proposal.proposal_id)
    rejection = ValidationResult(
        False, "PRICE_BELOW_FLOOR", "The proposed price is invalid"
    ).to_dict()
    attempt = engine.event_logger.append(
        "ACTION_ATTEMPT", "buyer", raw_output=json.dumps(action.to_dict()),
        parsed_action=action.to_dict(), validation_result=rejection,
    )
    terminal = engine.event_logger.append(
        "STATE_TRANSITION", "buyer",
        state_transition_summary="Game ended: invalid action retry exhausted",
    )
    database = tmp_path / "legacy-accept.db"
    save_run_to_sqlite(
        "legacy-accept", scenario, Outcome("INVALID_ACTION"),
        (*engine.event_logger.events[:2], attempt, replace(attempt, event_id=4),
         replace(terminal, event_id=5)), database
    )

    assert replay_run("legacy-accept", database).outcome.status == "INVALID_ACTION"
