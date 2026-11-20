"""Run a negotiation using role-scoped prompts and raw model adapters."""

import hashlib
import json
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Callable

from .engine import NegotiationEngine
from .model_adapter import BaseLLMAdapter, GenerationResult
from .models import Action, GameState, Outcome, ValidationResult
from .prompt_builder import (
    default_prompt_path,
    make_private_view,
    render_platform_instruction,
    render_prompt,
)
from .public_events import public_events
from .storage import DEFAULT_DB_PATH, save_run_to_sqlite
from .validator import parse_action


_MAX_INVALID_ACTION_ATTEMPTS = 5


class LLMAgent:
    """A role-scoped model client with private turn history."""

    def __init__(self, role: str, adapter: BaseLLMAdapter, prompt_path: str | Path | None = None):
        self.role = role
        self.adapter = adapter
        self.prompt_path = prompt_path if prompt_path is not None else default_prompt_path(role)
        self.messages = []
        self.logged_message_count = 0

    def initialize(self, state: GameState) -> None:
        """Create this role's persistent history with its private background once."""
        view = make_private_view(self.role, state)
        view["active_proposal"] = None
        view["public_transcript"] = []
        view["remaining_rounds"] = state.max_rounds
        self.messages.append({
            "role": "system",
            "content": render_prompt(self.role, view, template_path=self.prompt_path),
        })

    def decide(
        self, state: GameState, action_schema: dict
    ) -> tuple[Action | ValidationResult, str, list[dict[str, str]], GenerationResult]:
        if not self.messages:
            self.initialize(state)
        if self.role == "seller" and not any(
            message["role"] == "user" for message in self.messages
        ):
            self.messages.append({"role": "user", "content": render_platform_instruction()})
        logged_prompt = "\n\n".join(
            f"{message['role']}: {message['content']}" for message in self.messages
        )
        new_messages = deepcopy(self.messages[self.logged_message_count:])
        generated = self.adapter.generate(self.messages, action_schema)
        parsed = parse_action(generated.text, self.role)
        history_output = generated.text
        if isinstance(parsed, Action):
            action_data = parsed.to_dict()
            action_data.pop("actor", None)
            action_data.pop("proposal_id", None)
            if isinstance(action_data.get("proposal"), dict):
                action_data["proposal"].pop("proposal_id", None)
            history_output = json.dumps(action_data)
        self.messages.append({"role": "assistant", "content": history_output})
        self.logged_message_count = len(self.messages)
        return parsed, logged_prompt, new_messages, generated

    def report_validation(self, result: ValidationResult) -> None:
        self.messages.append({
            "role": "user",
            "content": (
                "Your previous action was rejected.\n"
                f"Error code: {result.error_code}\n"
                f"Error message: {result.message}\n"
                "Correct the invalid fields and output exactly one valid JSON action."
            ),
        })


def run_llm_negotiation(
    scenario: dict,
    adapters: BaseLLMAdapter | dict[str, BaseLLMAdapter],
    *,
    run_id: str | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    condition_id: str | None = None,
    seller_prompt_path: str | Path | None = None,
    buyer_prompt_path: str | Path | None = None,
    progress_callback: Callable[[dict], None] | None = None,
    private_progress_callback: Callable[[list[dict]], None] | None = None,
    model_config: dict | None = None,
) -> NegotiationEngine:
    """Run role-scoped generation until a terminal outcome and persist the run."""
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
    for agent in agents.values():
        agent.initialize(engine.state)
    persisted_model_config = deepcopy(model_config or {"version": None, "roles": {}})
    prompt_records = []
    persisted_model_config.setdefault("roles", {})
    for role, agent in agents.items():
        role_config = persisted_model_config["roles"].setdefault(
            role, _adapter_model_config(agent.adapter)
        )
        role_config["prompt_version"] = _prompt_version(agent.prompt_path)
        agent.model_metadata = deepcopy(role_config)
    if progress_callback is not None:
        progress_callback({
            "round_number": engine.state.round_number,
            "events": public_events(engine.event_logger.events, engine.state.public_transcript),
        })
    if private_progress_callback is not None:
        private_progress_callback(_private_summaries(engine.event_logger.events))
    while engine.state.outcome is None:
        role = engine.state.current_actor
        agent = agents[role]
        action_schema = _action_schema()
        event_count = len(engine.event_logger.events)
        try:
            parsed, prompt, new_messages, generated = agent.decide(engine.state, action_schema)
        except Exception:
            engine.state.outcome = Outcome("ERROR")
            try:
                save_run_to_sqlite(
                    run_id,
                    scenario,
                    engine.state.outcome,
                    engine.event_logger.events,
                    db_path,
                    condition_id=condition_id,
                    model_config=persisted_model_config,
                    prompts=prompt_records,
                )
            except Exception as exc:
                raise RuntimeError("Could not persist failed run") from exc
            raise
        prompt_records.append({
            "event_id": event_count + 1,
            "role": role,
            "prompt": new_messages,
            "action_schema": action_schema,
            "output": generated.text,
        })
        result = engine.step(
            parsed,
            role=role,
            raw_output=generated.text,
            metadata={
                "role": role,
                "prompt_template_id": Path(agent.prompt_path).name,
                **{
                    key: agent.model_metadata[key]
                    for key in ("public_alias", "provider", "model_id", "prompt_version", "adapter_version")
                    if key in agent.model_metadata
                },
                "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "prompt_length": len(prompt),
                **{
                    key: generated.metadata[key]
                    for key in ("model_version", "prompt_tokens", "completion_tokens", "latency_ms", "api_attempts")
                    if key in generated.metadata
                },
            },
        )
        if (
            isinstance(parsed, Action)
            and parsed.action_type in {"PROPOSE", "COUNTER"}
            and not isinstance(result, ValidationResult)
        ):
            public_proposal = engine.state.active_proposal.to_dict()
            public_proposal.pop("proposal_id", None)
            public_proposal.pop("decision_summary", None)
            public_action = {
                "action_type": parsed.action_type,
                "proposal": public_proposal,
            }
            update = render_platform_instruction(public_action)
            for recipient in agents.values():
                if recipient is not agent:
                    recipient.messages.append({"role": "user", "content": update})
                elif role == "buyer":
                    recipient.messages.append({"role": "user", "content": update})
        if isinstance(result, ValidationResult) and not result.ok:
            if engine.state.outcome is None and engine.state.current_actor != role:
                agents[engine.state.current_actor].messages.append({
                    "role": "user",
                    "content": "The latest proposal could not be accepted. Review its terms and counter or walk away.",
                })
            else:
                agent.report_validation(result)
                if engine.state.outcome is None:
                    engine.state.invalid_attempts += 1
                    if engine.state.invalid_attempts >= _MAX_INVALID_ACTION_ATTEMPTS:
                        engine.state.outcome = Outcome("INVALID_ACTION")
                        engine.event_logger.append(
                            "STATE_TRANSITION",
                            role,
                            state_transition_summary="Game ended: invalid action retry exhausted",
                            round_number=engine.state.round_number,
                        )
        if progress_callback is not None:
            progress_callback({
                "round_number": engine.state.round_number,
                "events": public_events(
                    engine.event_logger.events,
                    engine.state.public_transcript,
                    engine.state.outcome.to_dict() if engine.state.outcome else None,
                ),
            })
        if private_progress_callback is not None:
            private_progress_callback(_private_summaries(engine.event_logger.events))
    save_run_to_sqlite(
        run_id,
        scenario,
        engine.state.outcome,
        engine.event_logger.events,
        db_path,
        condition_id=condition_id,
        model_config=persisted_model_config,
        prompts=prompt_records,
    )
    return engine


def _private_summaries(events: list) -> list[dict]:
    return [
        {
            "event_id": event.event_id,
            "actor": event.actor,
            "decision_summary": event.private_decision_summary,
        }
        for event in events
        if event.event_type == "ACTION_ATTEMPT"
        and event.validation_result
        and event.validation_result.get("ok")
        and event.private_decision_summary is not None
    ]


def _adapter_model_config(adapter: BaseLLMAdapter) -> dict:
    provider = getattr(adapter, "provider", "custom")
    model_id = getattr(adapter, "model", getattr(adapter, "model_version", type(adapter).__name__))
    return {
        "public_alias": model_id,
        "provider": provider,
        "model_id": model_id,
        "base_url": getattr(adapter, "base_url", None),
        "adapter_version": "1",
    }


def _prompt_version(prompt_path: str | Path) -> str:
    return hashlib.sha256(Path(prompt_path).read_bytes()).hexdigest()


def _action_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action_type"],
        "properties": {
            "action_type": {
                "type": "string",
                "enum": ["PROPOSE", "COUNTER", "ACCEPT", "WALK_AWAY"],
            },
            "proposal": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "required": ["terms", "decision_summary"],
                "properties": {
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
