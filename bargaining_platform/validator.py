"""Validate schemas, inventory, conditions, and hard bounds."""

from decimal import Decimal, localcontext
from fractions import Fraction
from math import isfinite

from .models import Action, GameState, Proposal, Term, ValidationResult


def validate_action(role: str, action: Action, state: GameState) -> ValidationResult:
    if not isinstance(action, Action):
        return ValidationResult(False, "INVALID_ACTION_SCHEMA", "Action has an invalid schema")
    if action.actor is not None and action.actor != role:
        return ValidationResult(False, "WRONG_ACTOR", "Action actor does not match the acting role")
    if role != state.current_actor:
        return ValidationResult(False, "WRONG_TURN", "It is not this actor's turn")
    if not isinstance(action.action_type, str) or action.action_type not in {"PROPOSE", "COUNTER", "ACCEPT", "WALK_AWAY"}:
        return ValidationResult(False, "INVALID_ACTION_TYPE", "Unsupported action type")
    if action.action_type not in state.private_profiles[role]["allowed_actions"]:
        return ValidationResult(False, "ACTION_NOT_ALLOWED", "Action is not permitted for this role")

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
        if active.conditions:
            return ValidationResult(False, "CONDITIONS_NOT_SUPPORTED_YET", "Conditional offers are not supported yet")
        constraints = _validate_hard_constraints(active, state)
        return constraints if not constraints.ok else _validate_reservations(active, state)

    proposal = action.proposal
    if not isinstance(proposal, Proposal) or not isinstance(proposal.proposal_id, str) or not proposal.proposal_id:
        return ValidationResult(False, "INVALID_ACTION_SCHEMA", "A proposal with an ID is required")
    if action.action_type == "PROPOSE" and action.proposal_id != proposal.proposal_id:
        return ValidationResult(False, "INVALID_PROPOSAL_ID", "Action and proposal IDs must match")
    if proposal.acceptance_mode != "all_or_nothing" or not isinstance(proposal.terms, list) or not proposal.terms:
        return ValidationResult(False, "INVALID_PROPOSAL", "A complete all-or-nothing proposal is required")
    if not isinstance(proposal.conditions, list):
        return ValidationResult(False, "INVALID_CONDITION", "Conditions must be a list")
    if proposal.conditions:
        return ValidationResult(False, "CONDITIONS_NOT_SUPPORTED_YET", "Conditional offers are not supported yet")

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

    return _validate_hard_constraints(proposal, state)


def _validate_hard_constraints(proposal: Proposal, state: GameState) -> ValidationResult:
    catalog = {entry["id"]: entry for entry in state.catalog}

    quantities: dict[str, int] = {}
    for term in proposal.terms:
        if not isinstance(term, Term):
            return ValidationResult(False, "INVALID_TERM", "Term has an invalid schema")
        if not isinstance(term.item_id, str) or term.item_id not in catalog:
            return ValidationResult(False, "INVALID_ITEM_ID", "Item is not in the catalog")
        if term.unit != catalog[term.item_id]["unit"]:
            return ValidationResult(False, "INVALID_UNIT", "Unit does not match the catalog")
        if type(term.quantity) is not int or term.quantity <= 0:
            return ValidationResult(False, "INVALID_QUANTITY", "Quantity must be a positive integer")
        if not _nonnegative_number(term.unit_price):
            return ValidationResult(False, "INVALID_PRICE", "Unit price must be non-negative and finite")
        quantities[term.item_id] = quantities.get(term.item_id, 0) + term.quantity
    for item_id, quantity in quantities.items():
        if quantity > catalog[item_id]["seller_stock"]:
            return ValidationResult(False, "INSUFFICIENT_STOCK", "The requested quantity is unavailable")

    minimum_prices = state.private_profiles["seller"]["minimum_unit_price"]
    if (
        not isinstance(proposal.original_prices, dict)
        or not isinstance(proposal.effective_prices, dict)
        or proposal.original_prices.keys() != quantities.keys()
        or proposal.effective_prices.keys() != quantities.keys()
    ):
        return ValidationResult(False, "INVALID_PRICE", "The proposed price is invalid")
    for term in proposal.terms:
        if (
            proposal.original_prices.get(term.item_id) != term.unit_price
            or proposal.effective_prices.get(term.item_id) != term.unit_price
        ):
            return ValidationResult(False, "INVALID_PRICE", "The proposed price is invalid")
        if term.unit_price < minimum_prices[term.item_id]:
            return ValidationResult(False, "PRICE_BELOW_FLOOR", "The proposed price is invalid")
    try:
        totals = calculate_original_totals(proposal, state.legal_ranges["max_quantity"])
    except ValueError:
        return ValidationResult(False, "QUANTITY_LIMIT_EXCEEDED", "The requested quantity is not permitted")
    if totals["original_total"] > Decimal(str(state.private_profiles["buyer"]["budget"])):
        return ValidationResult(False, "BUDGET_EXCEEDED", "The proposed total exceeds the allowed budget")
    return ValidationResult(ok=True)


def _validate_reservations(proposal: Proposal, state: GameState) -> ValidationResult:
    seller = state.private_profiles["seller"]
    buyer = state.private_profiles["buyer"]
    if ("reservation_utility" not in seller or "reservation_utility" not in buyer
            or any(term.type != "purchase" for term in proposal.terms)):
        return ValidationResult(False, "UTILITY_NOT_IMPLEMENTED", "Acceptance requirements cannot be checked")
    seller_utility = Fraction(0)
    buyer_utility = Fraction(0)
    for term in proposal.terms:
        price = Fraction(Decimal(str(proposal.effective_prices[term.item_id])))
        seller_utility += term.quantity * (price - Fraction(Decimal(str(seller["unit_cost"][term.item_id]))))
        buyer_utility += term.quantity * (Fraction(Decimal(str(buyer["unit_value"][term.item_id]))) - price)
    if (seller_utility < Fraction(Decimal(str(seller["reservation_utility"])))
            or buyer_utility < Fraction(Decimal(str(buyer["reservation_utility"])))):
        return ValidationResult(False, "RESERVATION_NOT_MET", "The proposal does not meet acceptance requirements")
    return ValidationResult(ok=True)


def calculate_original_totals(proposal: Proposal, max_quantity: dict[str, int]) -> dict:
    """Return exact purchase line totals and total, enforcing per-item quantity limits."""
    quantities: dict[str, int] = {}
    prices: list[Decimal] = []
    for term in proposal.terms:
        if (
            not isinstance(term, Term)
            or term.type != "purchase"
            or type(term.quantity) is not int
            or term.quantity <= 0
            or not _nonnegative_number(term.unit_price)
            or not isinstance(term.item_id, str)
            or term.item_id not in max_quantity
        ):
            raise ValueError("Invalid purchase term")
        quantities[term.item_id] = quantities.get(term.item_id, 0) + term.quantity
        if quantities[term.item_id] > max_quantity[term.item_id]:
            raise ValueError("Quantity limit exceeded")
        prices.append(Decimal(str(term.unit_price)))
    whole_digits = max((price.adjusted() + 1 + len(str(term.quantity))
                        for term, price in zip(proposal.terms, prices)), default=1)
    fractional_digits = max((-price.as_tuple().exponent for price in prices), default=0)
    with localcontext() as context:
        context.prec = max(context.prec, whole_digits + fractional_digits + len(str(len(prices))) + 1)
        line_totals = [term.quantity * (term.unit_price if type(term.unit_price) is int else price)
                       for term, price in zip(proposal.terms, prices)]
        total = sum(line_totals, 0)
    return {"line_totals": line_totals, "original_total": total}


def _nonnegative_number(value: object) -> bool:
    return (type(value) is int and value >= 0) or (
        type(value) is float and isfinite(value) and value >= 0
    )
