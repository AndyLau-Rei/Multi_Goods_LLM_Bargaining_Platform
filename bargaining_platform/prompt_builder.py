"""Build role-specific private views and raw text prompts."""

from copy import deepcopy
import json
from pathlib import Path

from .models import GameState, json_safe


_PUBLIC_CATALOG_FIELDS = ("id", "type", "unit", "seller_stock")
_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


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

    transcript = deepcopy(state.public_transcript)
    for entry in transcript:
        if isinstance(entry, dict) and isinstance(entry.get("proposal"), dict):
            entry["proposal"].pop("decision_summary", None)

    return {
        "role": role,
        "private_profile": deepcopy(state.private_profiles[role]),
        "catalog": catalog,
        "active_proposal": active_proposal,
        "remaining_rounds": max(0, state.max_rounds - state.round_number + 1),
        "public_transcript": transcript,
    }


def render_prompt(role: str, private_view: dict) -> str:
    """Render an isolated private view as instructions for one negotiating role."""
    if role not in {"seller", "buyer"}:
        raise ValueError("Unknown negotiation role")
    if not isinstance(private_view, dict) or private_view.get("role") != role:
        raise ValueError("Private view does not match the requested role")

    profile = json_safe(deepcopy(private_view.get("private_profile", {})))
    catalog = json_safe(deepcopy(private_view.get("catalog", [])))
    active_proposal = json_safe(deepcopy(private_view.get("active_proposal")))
    transcript = json_safe(deepcopy(private_view.get("public_transcript", [])))
    objective = (
        "maximize seller utility: effective payment minus fulfillment costs"
        if role == "seller"
        else "maximize buyer utility: received value minus effective payment"
    )
    action_schema = {
        "action_type": "PROPOSE | COUNTER | ACCEPT | WALK_AWAY",
        "proposal_id": "string or null",
        "proposal": "Proposal object for PROPOSE/COUNTER, otherwise null",
    }
    example = {
        "action_type": "ACCEPT",
        "proposal_id": "current-proposal-id",
        "proposal": None,
    }
    template_path = _PROMPT_DIR / f"{role}_prompt.txt"
    if not template_path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {template_path}")
    template = template_path.read_text(encoding="utf-8")
    return template.format(
        objective=objective,
        private_constraints=json.dumps(profile, indent=2, sort_keys=True),
        catalog=json.dumps(catalog, indent=2, sort_keys=True),
        active_proposal=json.dumps(active_proposal, indent=2, sort_keys=True),
        transcript=json.dumps(transcript, indent=2, sort_keys=True),
        remaining_rounds=private_view.get("remaining_rounds"),
        action_schema=json.dumps(action_schema, indent=2, sort_keys=True),
        example=json.dumps(example, indent=2, sort_keys=True),
    )
