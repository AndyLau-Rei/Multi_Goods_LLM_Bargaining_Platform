"""Build role-specific private views and raw text prompts."""

from copy import deepcopy
import json

from .models import GameState, json_safe


_PUBLIC_CATALOG_FIELDS = ("id", "type", "unit", "seller_stock")


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

    return f"""You are the {role} in a structured multi-product negotiation.

ROLE AND OBJECTIVE
Your role is {role}. You must {objective} while obeying every hard constraint.

PRIVATE CONSTRAINTS
These values belong only to you and must guide your decision:
{json.dumps(profile, indent=2, sort_keys=True)}

PUBLIC CATALOG
{json.dumps(catalog, indent=2, sort_keys=True)}

RULES AND BUNDLE SEMANTICS
- Proposals are all-or-nothing: ACCEPT accepts every term and condition in the current proposal.
- ACCEPT may reference only the current proposal_id and must not include a replacement proposal.
- A typed discount_if_item_in_bundle condition applies only when its required item is in the same proposal with at least its minimum quantity.
- If you propose a discount for A conditioned on B, the engine will evaluate it strictly against the catalog, inventory, legal limits, scenario discount cap, and seller price floors.
- Use only the permitted actions listed in your private constraints. Do not invent items, quantities, benefits, or private information.

CURRENT STATE
Remaining rounds: {private_view.get("remaining_rounds")}
Active proposal:
{json.dumps(active_proposal, indent=2, sort_keys=True)}
Public transcript:
{json.dumps(transcript, indent=2, sort_keys=True)}

OUTPUT FORMAT
Output ONLY one valid JSON object matching this Action schema. Do not add Markdown, explanations, or private reasoning.
{json.dumps(action_schema, indent=2, sort_keys=True)}

Minimal example:
{json.dumps(example, indent=2, sort_keys=True)}
"""
