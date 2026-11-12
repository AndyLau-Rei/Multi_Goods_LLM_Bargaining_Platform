"""Build role-specific private views."""

from copy import deepcopy

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
