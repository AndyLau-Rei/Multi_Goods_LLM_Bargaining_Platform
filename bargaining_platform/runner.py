"""Run a negotiation using role-scoped prompts and raw model adapters."""

import json
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Callable

from .engine import NegotiationEngine
from .model_adapter import BaseLLMAdapter, MockLLMAdapter
from .prompt_builder import make_private_view, render_prompt
from .storage import DEFAULT_DB_PATH, save_run_to_sqlite
from .validator import parse_action


def run_llm_negotiation(
    scenario: dict,
    adapters: BaseLLMAdapter | dict[str, BaseLLMAdapter],
    *,
    run_id: str | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    mock_demo: bool = False,
    condition_id: str | None = None,
    seller_prompt_path: str | Path | None = None,
    buyer_prompt_path: str | Path | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> NegotiationEngine:
    """Run role-scoped generation until a terminal outcome and persist the run."""
    if mock_demo:
        scenario = deepcopy(scenario)
        for profile in scenario["private_profiles"].values():
            profile["reservation_utility"] = 0
    engine = NegotiationEngine(scenario)
    run_id = run_id or f"run-{uuid.uuid4().hex}"
    engine.run_id = run_id
    while engine.state.outcome is None:
        role = engine.state.current_actor
        adapter = adapters[role] if isinstance(adapters, dict) else adapters
        private_view = make_private_view(role, engine.state)
        prompt_path = seller_prompt_path if role == "seller" else buyer_prompt_path
        prompt = render_prompt(role, private_view, template_path=prompt_path)
        generated = adapter.generate(prompt, _action_schema())
        parsed = parse_action(generated.text, role)
        event_count = len(engine.event_logger.events)
        transcript_count = len(engine.state.public_transcript)
        engine.step(
            parsed,
            role=role,
            raw_output=generated.text,
            metadata={"prompt": prompt, **generated.metadata},
        )
        if progress_callback is not None:
            public_actions = iter(engine.state.public_transcript[transcript_count:])
            public_events = []
            for event in engine.event_logger.events[event_count:]:
                record = {
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "actor": event.actor,
                }
                if event.event_type == "ACTION_ATTEMPT" and event.validation_result["ok"]:
                    record["public_transcript"] = deepcopy(next(public_actions))
                elif event.event_type == "STATE_TRANSITION":
                    record["state_transition_summary"] = event.state_transition_summary
                else:
                    continue
                public_events.append(record)
            progress_callback({"round_number": engine.state.round_number, "events": public_events})
    save_run_to_sqlite(
        run_id,
        scenario,
        engine.state.outcome,
        engine.event_logger.events,
        db_path,
        condition_id=condition_id,
    )
    return engine


def default_mock_adapters() -> dict[str, MockLLMAdapter]:
    """Return deterministic role queues for local, network-free demonstrations."""
    standalone = {
        "proposal_id": "mock-a",
        "acceptance_mode": "all_or_nothing",
        "terms": [
            {"type": "purchase", "item_id": "A", "quantity": 1, "unit_price": 120, "unit": "piece"},
        ],
        "conditions": [],
        "decision_summary": "",
        "original_prices": {"A": 120},
        "effective_prices": {"A": 120},
    }
    bundle = {
        "proposal_id": "mock-a",
        "acceptance_mode": "all_or_nothing",
        "terms": [
            {"type": "purchase", "item_id": "A", "quantity": 1, "unit_price": 120, "unit": "piece"},
            {"type": "purchase", "item_id": "B", "quantity": 1, "unit_price": 60, "unit": "piece"},
        ],
        "conditions": [
            {
                "type": "discount_if_item_in_bundle",
                "discounted_item_id": "A",
                "discount_per_unit": 20,
                "required_item_id": "B",
                "minimum_required_quantity": 1,
            }
        ],
        "decision_summary": "",
        "original_prices": {"A": 120, "B": 60},
        "effective_prices": {"A": 120, "B": 60},
    }
    return {
        "seller": MockLLMAdapter([
            json.dumps({"action_type": "PROPOSE", "proposal_id": "mock-a", "proposal": standalone}),
            json.dumps({"action_type": "COUNTER", "proposal_id": "mock-a", "proposal": bundle}),
        ]),
        "buyer": MockLLMAdapter([
            json.dumps({"action_type": "COUNTER", "proposal_id": "mock-a", "proposal": bundle}),
            json.dumps({"action_type": "ACCEPT", "proposal_id": "mock-a"}),
        ]),
    }


def _action_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action_type", "proposal_id"],
        "properties": {
            "action_type": {
                "type": "string",
                "enum": ["PROPOSE", "COUNTER", "ACCEPT", "WALK_AWAY"],
            },
            "proposal_id": {"type": ["string", "null"]},
            "proposal": {
                "type": ["object", "null"],
                "properties": {
                    "proposal_id": {"type": "string"},
                    "acceptance_mode": {"type": "string", "enum": ["all_or_nothing"]},
                    "terms": {"type": "array"},
                    "conditions": {"type": "array"},
                    "decision_summary": {"type": "string"},
                    "original_prices": {"type": "object"},
                    "effective_prices": {"type": "object"},
                },
            },
        },
    }
