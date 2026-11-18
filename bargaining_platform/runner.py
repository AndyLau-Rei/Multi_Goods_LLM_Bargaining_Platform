"""Run a negotiation using role-scoped prompts and raw model adapters."""

import hashlib
import json
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Callable

from .engine import NegotiationEngine
from .model_adapter import BaseLLMAdapter, GenerationResult, MockLLMAdapter
from .models import Action, GameState, ValidationResult
from .prompt_builder import make_private_view, render_prompt
from .storage import DEFAULT_DB_PATH, save_run_to_sqlite
from .validator import parse_action


class LLMAgent:
    """A role-scoped model client with no persistent conversation state."""

    def __init__(self, role: str, adapter: BaseLLMAdapter, prompt_path: str | Path | None = None):
        self.role = role
        self.adapter = adapter
        self.prompt_path = prompt_path

    def decide(
        self, state: GameState, action_schema: dict
    ) -> tuple[Action | ValidationResult, str, GenerationResult]:
        view = make_private_view(self.role, state)
        prompt = render_prompt(self.role, view, template_path=self.prompt_path)
        generated = self.adapter.generate(prompt, action_schema)
        return parse_action(generated.text, self.role), prompt, generated


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
    model_config: dict | None = None,
) -> NegotiationEngine:
    """Run role-scoped generation until a terminal outcome and persist the run."""
    if mock_demo:
        scenario = deepcopy(scenario)
        for profile in scenario["private_profiles"].values():
            profile["reservation_utility"] = 0
    engine = NegotiationEngine(scenario)
    run_id = run_id or f"run-{uuid.uuid4().hex}"
    engine.run_id = run_id
    role_adapters = dict(adapters) if isinstance(adapters, dict) else {
        "seller": deepcopy(adapters), "buyer": deepcopy(adapters)
    }
    if role_adapters["seller"] is role_adapters["buyer"]:
        role_adapters["buyer"] = deepcopy(role_adapters["buyer"])
    if role_adapters["seller"] is role_adapters["buyer"]:
        raise ValueError("Seller and buyer require independent adapters")
    agents = {
        "seller": LLMAgent("seller", role_adapters["seller"], seller_prompt_path),
        "buyer": LLMAgent("buyer", role_adapters["buyer"], buyer_prompt_path),
    }
    persisted_model_config = deepcopy(model_config or {"version": None, "roles": {}})
    persisted_model_config.setdefault("roles", {})
    for role, agent in agents.items():
        role_config = persisted_model_config["roles"].setdefault(
            role, _adapter_model_config(agent.adapter)
        )
        role_config["prompt_version"] = _prompt_version(role, agent.prompt_path)
        agent.model_metadata = deepcopy(role_config)
    while engine.state.outcome is None:
        role = engine.state.current_actor
        agent = agents[role]
        parsed, prompt, generated = agent.decide(engine.state, _action_schema())
        event_count = len(engine.event_logger.events)
        transcript_count = len(engine.state.public_transcript)
        engine.step(
            parsed,
            role=role,
            raw_output=generated.text,
            metadata={"role": role, **agent.model_metadata, "prompt": prompt, **generated.metadata},
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
        model_config=persisted_model_config,
    )
    return engine


def _adapter_model_config(adapter: BaseLLMAdapter) -> dict:
    provider = getattr(adapter, "provider", "mock" if isinstance(adapter, MockLLMAdapter) else "custom")
    model_id = getattr(adapter, "model", getattr(adapter, "model_version", type(adapter).__name__))
    return {
        "public_alias": model_id,
        "provider": provider,
        "model_id": model_id,
        "base_url": getattr(adapter, "base_url", None),
        "adapter_version": "builtin-mock-v1" if isinstance(adapter, MockLLMAdapter) else "1",
    }


def _prompt_version(role: str, prompt_path: str | Path | None) -> str:
    path = Path(prompt_path) if prompt_path is not None else (
        Path(__file__).resolve().parent.parent / "prompts" / f"{role}_prompt_multi.txt"
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
