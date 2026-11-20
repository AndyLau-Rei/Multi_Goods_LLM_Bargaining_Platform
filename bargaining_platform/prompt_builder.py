"""Build role-specific private views and raw text prompts."""

from copy import deepcopy
import json
from pathlib import Path

from .models import GameState, json_safe


_PUBLIC_CATALOG_FIELDS = ("id", "type", "unit", "seller_stock")
_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def default_prompt_path(role: str) -> Path:
    return _PROMPT_DIR / f"{role}_multi.txt"


def _prompt_json(value: object) -> str:
    return json.dumps(json_safe(deepcopy(value)), indent=2, sort_keys=True)


def make_private_view(role: str, state: GameState) -> dict:
    """Return a detached role-scoped view of the public negotiation state."""
    if role not in {"seller", "buyer"}:
        raise ValueError("Unknown negotiation role")
    if role not in state.private_profiles:
        raise ValueError("Private profile is unavailable for this role")

    catalog = [
        {field: deepcopy(item[field]) for field in _PUBLIC_CATALOG_FIELDS if field in item}
        for item in state.catalog
    ]
    active_proposal = None
    if state.active_proposal is not None:
        active_proposal = deepcopy(json_safe(state.active_proposal.to_dict()))
        active_proposal.pop("decision_summary", None)
        active_proposal.pop("proposal_id", None)

    transcript = deepcopy(state.public_transcript)
    for entry in transcript:
        if isinstance(entry, dict):
            entry.pop("proposal_id", None)
        if isinstance(entry, dict) and isinstance(entry.get("proposal"), dict):
            entry["proposal"].pop("decision_summary", None)
            entry["proposal"].pop("proposal_id", None)

    return {
        "role": role,
        "private_profile": deepcopy(state.private_profiles[role]),
        "catalog": catalog,
        "active_proposal": active_proposal,
        "remaining_rounds": max(0, state.max_rounds - state.round_number + 1),
        "public_transcript": transcript,
    }


def render_prompt(
    role: str,
    private_view: dict,
    template_path: str | Path | None = None,
) -> str:
    """Render an isolated private view as instructions for one negotiating role."""
    if role not in {"seller", "buyer"}:
        raise ValueError("Unknown negotiation role")
    if not isinstance(private_view, dict) or private_view.get("role") != role:
        raise ValueError("Private view does not match the requested role")

    objective = (
        "maximize seller utility: effective payment minus fulfillment costs"
        if role == "seller"
        else "maximize buyer utility: received value minus effective payment"
    )
    action_schema = {
        "action_type": "PROPOSE | COUNTER | ACCEPT | WALK_AWAY",
        "proposal": "price/package object for PROPOSE/COUNTER, otherwise null",
    }
    example = {
        "action_type": "ACCEPT",
        "proposal": None,
    }
    selected_template = Path(template_path) if template_path is not None else default_prompt_path(role)
    if not selected_template.is_file():
        raise FileNotFoundError(f"Prompt template not found: {selected_template}")
    template = selected_template.read_text(encoding="utf-8")
    return template.format(
        objective=objective,
        private_constraints=_prompt_json(private_view.get("private_profile", {})),
        catalog=_prompt_json(private_view.get("catalog", [])),
        active_proposal=_prompt_json(private_view.get("active_proposal")),
        transcript=_prompt_json(private_view.get("public_transcript", [])),
        remaining_rounds=private_view.get("remaining_rounds"),
        action_schema=_prompt_json(action_schema),
        example=_prompt_json(example),
    )


def render_platform_instruction(public_action: dict | None = None) -> str:
    """Render a platform message for the opening turn or a public proposal."""
    if public_action is None:
        return "Please make the opening proposal now. Output exactly one valid JSON action."
    return (
        "The following proposal is now public. Review it and choose "
        "COUNTER, ACCEPT, or WALK_AWAY.\n\n" + _prompt_json(public_action)
    )
