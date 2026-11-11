"""Validate schemas, inventory, conditions, and hard bounds."""

from math import isfinite

from .models import Action, Condition, GameState, Proposal, Term, ValidationResult


def validate_action(role: str, action: Action, state: GameState) -> ValidationResult:
    if not isinstance(action, Action):
        return ValidationResult(False, "INVALID_ACTION_SCHEMA", "Action has an invalid schema")
    if action.actor is not None and action.actor != role:
        return ValidationResult(False, "WRONG_ACTOR", "Action actor does not match the acting role")
    if role != state.current_actor:
        return ValidationResult(False, "WRONG_TURN", "It is not this actor's turn")
    if not isinstance(action.action_type, str) or action.action_type not in {"PROPOSE", "COUNTER", "ACCEPT", "WALK_AWAY"}:
        return ValidationResult(False, "INVALID_ACTION_TYPE", "Unsupported action type")

    active = state.active_proposal
    if active is None and action.action_type != "PROPOSE":
        return ValidationResult(False, "NO_ACTIVE_PROPOSAL", "A proposal is required first")
    if active is not None and action.action_type == "PROPOSE":
        return ValidationResult(False, "INVALID_ACTION_TYPE", "Use COUNTER to replace a proposal")
    if action.action_type in {"ACCEPT", "COUNTER"} and action.proposal_id != active.proposal_id:
        return ValidationResult(False, "STALE_PROPOSAL_ID", "Proposal ID is not current")
    if action.action_type == "WALK_AWAY":
        if action.proposal is not None:
            return ValidationResult(False, "INVALID_ACTION_SCHEMA", "WALK_AWAY cannot contain a proposal")
        return ValidationResult(ok=True)
    if action.action_type == "ACCEPT":
        if action.proposal is not None:
            return ValidationResult(False, "INVALID_ACTION_SCHEMA", "ACCEPT cannot contain a proposal")
        return ValidationResult(ok=True)

    proposal = action.proposal
    if not isinstance(proposal, Proposal) or not isinstance(proposal.proposal_id, str) or not proposal.proposal_id:
        return ValidationResult(False, "INVALID_ACTION_SCHEMA", "A proposal with an ID is required")
    if action.action_type == "PROPOSE" and action.proposal_id != proposal.proposal_id:
        return ValidationResult(False, "INVALID_PROPOSAL_ID", "Action and proposal IDs must match")
    if proposal.acceptance_mode != "all_or_nothing" or not isinstance(proposal.terms, list) or not proposal.terms:
        return ValidationResult(False, "INVALID_PROPOSAL", "A complete all-or-nothing proposal is required")
    if not isinstance(proposal.conditions, list):
        return ValidationResult(False, "INVALID_CONDITION", "Conditions must be a list")

    catalog = {entry["id"]: entry for entry in state.catalog}
    for term in proposal.terms:
        if not isinstance(term, Term):
            return ValidationResult(False, "INVALID_TERM", "Term has an invalid schema")
        if term.type != "purchase":
            return ValidationResult(False, "INVALID_TERM_TYPE", "Term type is not permitted")
        item = catalog.get(term.item_id) if isinstance(term.item_id, str) else None
        if item is None or item["type"] != "product":
            return ValidationResult(False, "INVALID_ITEM_ID", "Item is not in the catalog")
        if term.unit != item["unit"]:
            return ValidationResult(False, "INVALID_UNIT", "Unit does not match the catalog")
        if type(term.quantity) is not int or term.quantity <= 0:
            return ValidationResult(False, "INVALID_QUANTITY", "Quantity must be a positive integer")
        if not _nonnegative_number(term.unit_price):
            return ValidationResult(False, "INVALID_PRICE", "Unit price must be non-negative and finite")

    for condition in proposal.conditions:
        if not isinstance(condition, Condition):
            return ValidationResult(False, "INVALID_CONDITION", "Condition has an invalid schema")
        if condition.type != "discount_if_item_in_bundle":
            return ValidationResult(False, "INVALID_CONDITION", "Condition type is not permitted")
        if (
            not isinstance(condition.discounted_item_id, str)
            or not isinstance(condition.required_item_id, str)
            or condition.discounted_item_id not in catalog
            or condition.required_item_id not in catalog
        ):
            return ValidationResult(False, "INVALID_ITEM_ID", "Condition item is not in the catalog")
        if type(condition.minimum_required_quantity) is not int or condition.minimum_required_quantity <= 0:
            return ValidationResult(False, "INVALID_QUANTITY", "Required quantity must be a positive integer")
        if not _nonnegative_number(condition.discount_per_unit):
            return ValidationResult(False, "INVALID_CONDITION", "Discount must be non-negative and finite")
    return ValidationResult(ok=True)


def _nonnegative_number(value: object) -> bool:
    return (type(value) is int and value >= 0) or (
        type(value) is float and isfinite(value) and value >= 0
    )
