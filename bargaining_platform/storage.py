"""Keep an append-only in-memory event log for a negotiation run."""

import json
import sqlite3
from copy import deepcopy
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import Action, Outcome, Proposal, ValidationResult, json_safe


DEFAULT_DB_PATH = Path("results/runs.db")


@dataclass(frozen=True)
class Event:
    event_id: int
    timestamp: str
    event_type: str
    actor: str | None
    raw_output: Any = None
    parsed_action: dict | None = None
    validation_result: dict | None = None
    state_transition_summary: str | None = None
    private_decision_summary: str | None = None
    metadata: dict | None = None
    round_number: int = 1

    def to_dict(self) -> dict:
        return json_safe(asdict(self))


class EventLogger:
    """Collect immutable events without exposing private fields to the transcript."""

    def __init__(self):
        self._events: list[Event] = []

    @property
    def events(self) -> tuple[Event, ...]:
        return deepcopy(tuple(self._events))

    def append(
        self,
        event_type: str,
        actor: str | None = None,
        *,
        raw_output: Any = None,
        parsed_action: dict | None = None,
        validation_result: dict | None = None,
        state_transition_summary: str | None = None,
        private_decision_summary: str | None = None,
        metadata: dict | None = None,
        round_number: int = 1,
    ) -> Event:
        event = Event(
            event_id=len(self._events) + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            actor=actor,
            raw_output=(
                json_safe(deepcopy(raw_output)) if raw_output is not None else None
            ),
            parsed_action=(
                json_safe(deepcopy(parsed_action))
                if parsed_action is not None
                else None
            ),
            validation_result=(
                json_safe(deepcopy(validation_result))
                if validation_result is not None
                else None
            ),
            state_transition_summary=state_transition_summary,
            private_decision_summary=private_decision_summary,
            metadata=(
                json_safe(deepcopy(metadata)) if metadata is not None else None
            ),
            round_number=round_number,
        )
        self._events.append(event)
        return deepcopy(event)


def _json_text(value: Any) -> str:
    """Encode structured values after converting Decimal amounts to strings."""
    return json.dumps(json_safe(value), ensure_ascii=False, separators=(",", ":"))


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            condition_id TEXT,
            scenario_id TEXT NOT NULL,
            max_rounds INTEGER NOT NULL,
            outcome_status TEXT,
            created_at TEXT NOT NULL,
            scenario_json TEXT NOT NULL,
            outcome_json TEXT,
            model_config_json TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            run_id TEXT NOT NULL,
            event_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT,
            raw_output_json TEXT,
            parsed_action_json TEXT,
            validation_result_json TEXT,
            state_transition_summary TEXT,
            private_decision_summary TEXT,
            metadata_json TEXT,
            round_number INTEGER,
            PRIMARY KEY (run_id, event_id),
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        """
    )
    run_columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
    if "condition_id" not in run_columns:
        connection.execute("ALTER TABLE runs ADD COLUMN condition_id TEXT")
    if "model_config_json" not in run_columns:
        connection.execute("ALTER TABLE runs ADD COLUMN model_config_json TEXT")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
    if "metadata_json" not in columns:
        connection.execute("ALTER TABLE events ADD COLUMN metadata_json TEXT")
    if "round_number" not in columns:
        connection.execute("ALTER TABLE events ADD COLUMN round_number INTEGER")


def save_run_to_sqlite(
    run_id: str,
    scenario: dict,
    outcome: Outcome | dict | None,
    events: list[Event] | tuple[Event, ...],
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    condition_id: str | None = None,
    model_config: dict | None = None,
) -> None:
    """Persist one completed run and its append-only event snapshot."""
    database = Path(db_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    outcome_data = outcome.to_dict() if isinstance(outcome, Outcome) else outcome
    timestamp = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _create_schema(connection)
        with connection:
            connection.execute(
                """
                INSERT INTO runs
                    (run_id, condition_id, scenario_id, max_rounds, outcome_status, created_at,
                     scenario_json, outcome_json, model_config_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    condition_id,
                    scenario["scenario_id"],
                    scenario["max_rounds"],
                    outcome_data.get("status") if outcome_data else None,
                    timestamp,
                    _json_text(scenario),
                    _json_text(outcome_data) if outcome_data is not None else None,
                    _json_text(model_config) if model_config is not None else None,
                ),
            )
            connection.executemany(
                """
                INSERT INTO events
                    (run_id, event_id, timestamp, event_type, actor,
                     raw_output_json, parsed_action_json, validation_result_json,
                     state_transition_summary, private_decision_summary, metadata_json,
                     round_number)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        run_id,
                        event.event_id,
                        event.timestamp,
                        event.event_type,
                        event.actor,
                        _json_text(event.raw_output) if event.raw_output is not None else None,
                        _json_text(event.parsed_action)
                        if event.parsed_action is not None
                        else None,
                        _json_text(event.validation_result)
                        if event.validation_result is not None
                        else None,
                        event.state_transition_summary,
                        event.private_decision_summary,
                        _json_text(event.metadata) if event.metadata is not None else None,
                        event.round_number,
                    )
                    for event in events
                ),
            )


def load_run_from_sqlite(
    run_id: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict:
    """Read persisted run data for inspection; replay is intentionally out of scope."""
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        _create_schema(connection)
        run = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if run is None:
            raise KeyError(f"Unknown run: {run_id}")
        event_rows = connection.execute(
            "SELECT * FROM events WHERE run_id = ? ORDER BY event_id", (run_id,)
        ).fetchall()

    def decode(value: str | None) -> Any:
        return json.loads(value) if value is not None else None

    return {
        "run_id": run["run_id"],
        "condition_id": run["condition_id"],
        "scenario_id": run["scenario_id"],
        "max_rounds": run["max_rounds"],
        "outcome_status": run["outcome_status"],
        "model_config": decode(run["model_config_json"]),
        "created_at": run["created_at"],
        "scenario": decode(run["scenario_json"]),
        "outcome": decode(run["outcome_json"]),
        "events": [
            {
                "run_id": row["run_id"],
                "event_id": row["event_id"],
                "timestamp": row["timestamp"],
                "event_type": row["event_type"],
                "actor": row["actor"],
                "raw_output": decode(row["raw_output_json"]),
                "parsed_action": decode(row["parsed_action_json"]),
                "validation_result": decode(row["validation_result_json"]),
                "state_transition_summary": row["state_transition_summary"],
                "private_decision_summary": row["private_decision_summary"],
                "metadata": decode(row["metadata_json"]),
                "round_number": row["round_number"],
            }
            for row in event_rows
        ],
    }


def export_run_to_jsonl(
    run_id: str,
    events: list[Event] | tuple[Event, ...],
    filepath: str | Path,
) -> None:
    """Write one JSON-safe event record per line."""
    output = Path(filepath)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for event in events:
            record = {"run_id": run_id, **event.to_dict()}
            stream.write(_json_text(record))
            stream.write("\n")


class ReplayDivergenceError(RuntimeError):
    """Raised when a persisted action log no longer reproduces its run."""


def replay_run(run_id: str, db_path: str | Path = DEFAULT_DB_PATH):
    """Replay persisted actions in a fresh engine and verify deterministic results."""
    from .engine import NegotiationEngine
    from .utility import validate_reservation_utilities
    from .validator import calculate_effective_totals, parse_action, validate_action

    run = load_run_from_sqlite(run_id, db_path)
    engine = NegotiationEngine(run["scenario"])
    action_events = [event for event in run["events"] if event["event_type"] == "ACTION_ATTEMPT"]
    transition_events = [
        event for event in run["events"] if event["event_type"] == "STATE_TRANSITION"
    ]
    transitions_by_id = {event["event_id"]: event for event in transition_events}
    transitions_seen = 0
    valid_actions = []

    for event in action_events:
        try:
            if event["parsed_action"] is None:
                engine.step(event["raw_output"], role=event["actor"])
            else:
                action_data = _restore_action_numbers(event["parsed_action"])
                proposal = action_data.get("proposal")
                if proposal is not None:
                    proposal.setdefault("decision_summary", "")
                action = Action.from_dict(action_data)
                raw_action = (
                    parse_action(event["raw_output"], event["actor"])
                    if isinstance(event["raw_output"], str) else None
                )
                legacy_budget_rejection = (
                    event["validation_result"] == {
                        "ok": False,
                        "error_code": "BUDGET_EXCEEDED",
                        "message": "The proposed total exceeds the allowed budget",
                    }
                    and action.action_type in {"PROPOSE", "COUNTER"}
                    and isinstance(raw_action, Action)
                    and validate_action(event["actor"], action, engine.state).ok
                )
                if legacy_budget_rejection:
                    raw_action_data = raw_action.to_dict()
                    raw_action_data["proposal"].pop("decision_summary", None)
                    stored_action = action.to_dict()
                    stored_action["proposal"].pop("decision_summary", None)
                    legacy_budget_rejection = (
                        raw_action_data == stored_action
                        and calculate_effective_totals(action.proposal, engine.state)["effective_total"]
                        > Decimal(str(engine.state.private_profiles["buyer"]["budget"]))
                    )
                legacy_acceptance_rejection = (
                    action.action_type == "ACCEPT"
                    and not event["validation_result"]["ok"]
                    and not (
                        event["event_id"] + 1 in transitions_by_id
                        and transitions_by_id[event["event_id"] + 1]["state_transition_summary"]
                        in {"Maximum rounds reached", "Acceptance rejected; turn switched to seller",
                                "Acceptance rejected; turn switched to buyer",
                                "Game ended: accepted proposal failed validation"}
                    )
                )
                if legacy_acceptance_rejection:
                    current_result = validate_action(event["actor"], action, engine.state)
                    if current_result.ok:
                        current_result = validate_reservation_utilities(
                            engine.state.active_proposal, engine.state
                        )
                    if current_result.to_dict() != event["validation_result"]:
                        raise ReplayDivergenceError("Legacy acceptance validation diverged")
                engine.step(
                    ValidationResult(**event["validation_result"])
                    if legacy_acceptance_rejection else
                    ValidationResult(False, "BUDGET_EXCEEDED", "The proposed total exceeds the allowed budget")
                    if legacy_budget_rejection else action,
                    role=event["actor"],
                )
                legacy_retry_transition = transitions_by_id.get(event["event_id"] + 1)
                if (
                    legacy_retry_transition is not None
                    and legacy_retry_transition["state_transition_summary"]
                    == "Game ended: invalid action retry exhausted"
                    and engine.state.outcome is None
                    and engine.event_logger.events[-1].event_type != "STATE_TRANSITION"
                ):
                    engine.state.outcome = Outcome("INVALID_ACTION")
                    engine.event_logger.append(
                        "STATE_TRANSITION", event["actor"],
                        state_transition_summary="Game ended: invalid action retry exhausted",
                    )
        except Exception as exc:
            raise ReplayDivergenceError(
                f"Replay failed at event {event['event_id']}"
            ) from exc

        replay_events = engine.event_logger.events
        replay_attempt = next(
            event_record
            for event_record in reversed(replay_events)
            if event_record.event_type == "ACTION_ATTEMPT"
        )
        if replay_attempt.validation_result != event["validation_result"]:
            raise ReplayDivergenceError(f"Validation diverged at event {event['event_id']}")
        if (
            event.get("round_number") is not None
            and replay_attempt.round_number != event["round_number"]
        ):
            raise ReplayDivergenceError(f"Round number diverged at event {event['event_id']}")
        if replay_attempt.validation_result and replay_attempt.validation_result.get("ok"):
            valid_actions.append(event)

        if replay_events[-1].event_type == "STATE_TRANSITION":
            if transitions_seen >= len(transition_events):
                raise ReplayDivergenceError("Replay produced an unexpected state transition")
            expected = transition_events[transitions_seen]["state_transition_summary"]
            if replay_events[-1].state_transition_summary != expected:
                raise ReplayDivergenceError(
                    f"State transition diverged after event {event['event_id']}"
                )
            expected_round = transition_events[transitions_seen].get("round_number")
            if expected_round is not None and replay_events[-1].round_number != expected_round:
                raise ReplayDivergenceError(
                    f"Round number diverged after event {event['event_id']}"
                )
            transitions_seen += 1

    if transitions_seen != len(transition_events):
        raise ReplayDivergenceError("Replay event count diverged")
    actual_outcome = engine.state.outcome.to_dict() if engine.state.outcome else None
    if actual_outcome != run["outcome"]:
        raise ReplayDivergenceError("Final outcome diverged from stored outcome")

    expected_transcript = []
    for event in valid_actions:
        action = event["parsed_action"]
        public = dict(action)
        public["actor"] = event["actor"]
        if public.get("proposal") is not None:
            public["proposal"] = dict(public["proposal"])
            public["proposal"].pop("decision_summary", None)
        expected_transcript.append(public)
    actual_transcript = engine.state.public_transcript
    if len(expected_transcript) != len(actual_transcript):
        raise ReplayDivergenceError("Public transcript length diverged")
    for expected, actual in zip(expected_transcript, actual_transcript):
        if (
            expected["action_type"] != actual["action_type"]
            or expected["proposal_id"] != actual["proposal_id"]
        ):
            raise ReplayDivergenceError("Public transcript diverged")
        if expected.get("proposal") is not None:
            expected_proposal = expected["proposal"]
            actual_proposal = actual["proposal"]
            for field in ("terms", "conditions", "original_prices"):
                if expected_proposal.get(field) != actual_proposal.get(field):
                    raise ReplayDivergenceError("Public proposal diverged")

    proposal_events = [
        event for event in valid_actions
        if event["parsed_action"].get("action_type") in {"PROPOSE", "COUNTER"}
    ]
    if proposal_events:
        expected_data = _restore_action_numbers(proposal_events[-1]["parsed_action"]["proposal"])
        expected_data.setdefault("decision_summary", "")
        expected_proposal = Proposal.from_dict(expected_data)
        totals = calculate_effective_totals(expected_proposal, engine.state)
        if not isinstance(totals, dict):
            raise ReplayDivergenceError("Final proposal could not be evaluated")
        expected_proposal.effective_prices = totals["effective_prices"]
        if engine.state.active_proposal.to_dict() != expected_proposal.to_dict():
            raise ReplayDivergenceError("Active proposal diverged")

    return engine.state


def _restore_action_numbers(action_data: dict) -> dict:
    """Restore JSON string money values to numeric values accepted by the models."""
    action = json.loads(json.dumps(action_data))
    proposal = action.get("proposal")
    if proposal is None:
        return action
    for term in proposal.get("terms", []):
        term["unit_price"] = _restore_number(term.get("unit_price"))
    for condition in proposal.get("conditions", []):
        condition["discount_per_unit"] = _restore_number(condition.get("discount_per_unit"))
    for name in ("original_prices", "effective_prices"):
        proposal[name] = {
            key: _restore_number(value)
            for key, value in proposal.get(name, {}).items()
        }
    return action


def _restore_number(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return Decimal(value) if "." in value else int(value)
    except (ValueError, ArithmeticError):
        return value
