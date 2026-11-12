import json
import sqlite3

import pytest

from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.models import Action, Condition, Proposal, Term
from bargaining_platform.scenario import load_scenario
from bargaining_platform.storage import (
    ReplayDivergenceError,
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
