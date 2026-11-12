"""Keep an append-only in-memory event log for a negotiation run."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .models import json_safe


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
