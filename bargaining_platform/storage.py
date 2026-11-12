"""Keep an append-only in-memory event log for a negotiation run."""

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Outcome, json_safe


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

    def to_dict(self) -> dict:
        return json_safe(asdict(self))


class EventLogger:
    """Collect immutable events without exposing private fields to the transcript."""

    def __init__(self):
        self._events: list[Event] = []

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

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
    ) -> Event:
        event = Event(
            event_id=len(self._events) + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            actor=actor,
            raw_output=json_safe(raw_output) if raw_output is not None else None,
            parsed_action=json_safe(parsed_action) if parsed_action is not None else None,
            validation_result=json_safe(validation_result) if validation_result is not None else None,
            state_transition_summary=state_transition_summary,
            private_decision_summary=private_decision_summary,
        )
        self._events.append(event)
        return event


def _json_text(value: Any) -> str:
    """Encode structured values after converting Decimal amounts to strings."""
    return json.dumps(json_safe(value), ensure_ascii=False, separators=(",", ":"))


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            max_rounds INTEGER NOT NULL,
            outcome_status TEXT,
            created_at TEXT NOT NULL,
            scenario_json TEXT NOT NULL,
            outcome_json TEXT
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
            PRIMARY KEY (run_id, event_id),
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        """
    )


def save_run_to_sqlite(
    run_id: str,
    scenario: dict,
    outcome: Outcome | dict | None,
    events: list[Event] | tuple[Event, ...],
    db_path: str | Path = DEFAULT_DB_PATH,
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
                    (run_id, scenario_id, max_rounds, outcome_status, created_at,
                     scenario_json, outcome_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    scenario["scenario_id"],
                    scenario["max_rounds"],
                    outcome_data.get("status") if outcome_data else None,
                    timestamp,
                    _json_text(scenario),
                    _json_text(outcome_data) if outcome_data is not None else None,
                ),
            )
            connection.executemany(
                """
                INSERT INTO events
                    (run_id, event_id, timestamp, event_type, actor,
                     raw_output_json, parsed_action_json, validation_result_json,
                     state_transition_summary, private_decision_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        "scenario_id": run["scenario_id"],
        "max_rounds": run["max_rounds"],
        "outcome_status": run["outcome_status"],
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
