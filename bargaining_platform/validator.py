"""Parse actions and validate schemas, inventory, conditions, and hard bounds."""

import json
import re
from decimal import Decimal, localcontext
from math import isfinite

from .models import ACTION_TYPES, Action, Condition, GameState, Proposal, Term, ValidationResult


_ACTION_KEYS = {"action_type", "proposal_id", "proposal"}
_PROPOSAL_KEYS = {
    "proposal_id",
    "acceptance_mode",
    "terms",
    "conditions",
    "decision_summary",
    "original_prices",
    "effective_prices",
}
_TERM_KEYS = {"type", "item_id", "quantity", "unit_price", "unit"}
_CONDITION_KEYS = {
    "type",
    "discounted_item_id",
    "discount_per_unit",
    "required_item_id",
    "minimum_required_quantity",
}


def parse_action(raw_text: str, role: str) -> Action | ValidationResult:
    """Parse raw model output into an action or a structured error."""
    if role not in {"seller", "buyer"}:
        return ValidationResult(False, "SCHEMA_VIOLATION", "Unknown acting role")
    if not isinstance(raw_text, str):
        return ValidationResult(False, "MALFORMED_JSON", "Model output must be text")
    text = raw_text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ValidationResult(False, "MALFORMED_JSON", "Model output is not valid JSON")
    if not isinstance(data, dict):
        return ValidationResult(False, "SCHEMA_VIOLATION", "Action must be a JSON object")

    violation = _validate_action_shape(data)
    if violation is not None:
        return violation
    action_data = dict(data)
    if action_data.get("proposal") is not None:
        proposal_violation = _validate_proposal_shape(action_data["proposal"])
        if proposal_violation is not None:
            return proposal_violation
        proposal = action_data["proposal"]
        prices = {term["item_id"]: term["unit_price"] for term in proposal["terms"]}
        proposal.setdefault("proposal_id", None)
        proposal.setdefault("acceptance_mode", "all_or_nothing")
        proposal.setdefault("conditions", [])
        proposal.setdefault("original_prices", prices)
        proposal.setdefault("effective_prices", prices.copy())
    action_data["actor"] = role
    try:
        return Action.from_dict(action_data)
    except (KeyError, TypeError, ValueError):
        return ValidationResult(False, "SCHEMA_VIOLATION", "Action fields have invalid types")


def _validate_action_shape(data: dict) -> ValidationResult | None:
    if set(data) - _ACTION_KEYS:
        return ValidationResult(False, "SCHEMA_VIOLATION", "Action contains unexpected fields")
    if not isinstance(data.get("action_type"), str) or data["action_type"] not in ACTION_TYPES:
        return ValidationResult(
            False, "SCHEMA_VIOLATION", "Action type is required and unsupported"
        )
    action_type = data["action_type"]
    proposal_id = data.get("proposal_id")
    if proposal_id is not None and not isinstance(proposal_id, str):
        return ValidationResult(False, "SCHEMA_VIOLATION", "proposal_id must be a string or null")

    proposal = data.get("proposal")
    if action_type in {"PROPOSE", "COUNTER"}:
        if not isinstance(proposal, dict):
            return ValidationResult(False, "SCHEMA_VIOLATION", "A proposal is required")
    elif proposal is not None:
        return ValidationResult(False, "SCHEMA_VIOLATION", "This action cannot contain a proposal")
    return None


def _validate_proposal_shape(proposal: dict) -> ValidationResult | None:
    if set(proposal) - _PROPOSAL_KEYS or not {"terms", "decision_summary"} <= set(proposal):
        return ValidationResult(
            False, "SCHEMA_VIOLATION", "Proposal fields are incomplete or unexpected"
        )
    if "proposal_id" in proposal and proposal["proposal_id"] is not None and not isinstance(proposal["proposal_id"], str):
        return ValidationResult(False, "SCHEMA_VIOLATION", "Proposal ID must be a non-empty string")
    if "acceptance_mode" in proposal and not isinstance(proposal["acceptance_mode"], str):
        return ValidationResult(False, "SCHEMA_VIOLATION", "Acceptance mode must be text")
    if "decision_summary" in proposal and not isinstance(proposal["decision_summary"], str):
        return ValidationResult(False, "SCHEMA_VIOLATION", "Decision summary must be text")
    if not isinstance(proposal["terms"], list) or not proposal["terms"]:
        return ValidationResult(
            False, "SCHEMA_VIOLATION", "Proposal terms must be a non-empty list"
        )
    if "conditions" in proposal and not isinstance(proposal["conditions"], list):
        return ValidationResult(False, "SCHEMA_VIOLATION", "Proposal conditions must be a list")
    for term in proposal["terms"]:
        if not isinstance(term, dict) or set(term) != _TERM_KEYS:
            return ValidationResult(
                False, "SCHEMA_VIOLATION", "Term fields are incomplete or unexpected"
            )
        if not isinstance(term["type"], str) or not isinstance(term["item_id"], str):
            return ValidationResult(False, "SCHEMA_VIOLATION", "Term identifiers must be text")
        if not _json_number(term["quantity"]) or not _json_number(term["unit_price"]):
            return ValidationResult(False, "SCHEMA_VIOLATION", "Term numeric fields are invalid")
        if not isinstance(term["unit"], str):
            return ValidationResult(False, "SCHEMA_VIOLATION", "Term unit must be text")
    for condition in proposal.get("conditions", []):
        if not isinstance(condition, dict) or set(condition) != _CONDITION_KEYS:
            return ValidationResult(
                False, "SCHEMA_VIOLATION",
                "Condition fields are incomplete or unexpected",
            )
        if not isinstance(condition["type"], str):
            return ValidationResult(False, "SCHEMA_VIOLATION", "Condition type must be text")
        if (
            not isinstance(condition["discounted_item_id"], str)
            or not isinstance(condition["required_item_id"], str)
        ):
            return ValidationResult(False, "SCHEMA_VIOLATION", "Condition item IDs must be text")
        if (
            not _json_number(condition["discount_per_unit"])
            or not _json_number(condition["minimum_required_quantity"])
        ):
            return ValidationResult(
                False, "SCHEMA_VIOLATION", "Condition numeric fields are invalid"
            )
    for prices in (proposal.get("original_prices", {}), proposal.get("effective_prices", {})):
        if not isinstance(prices, dict) or any(
            not isinstance(item_id, str) or not _json_number(price)
            for item_id, price in prices.items()
        ):
            return ValidationResult(False, "SCHEMA_VIOLATION", "Proposal prices are invalid")
    return None


def _json_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and (
        not isinstance(value, float) or isfinite(value)
    )


def validate_action(role: str, action: Action, state: GameState) -> ValidationResult:
    if not isinstance(action, Action):
        return ValidationResult(False, "INVALID_ACTION_SCHEMA", "Action has an invalid schema")
    if action.actor is not None and action.actor != role:
        return ValidationResult(False, "WRONG_ACTOR", "Action actor does not match the acting role")
    if role != state.current_actor:
        return ValidationResult(False, "WRONG_TURN", "It is not this actor's turn")
    if (
        not isinstance(action.action_type, str)
        or action.action_type not in {"PROPOSE", "COUNTER", "ACCEPT", "WALK_AWAY"}
    ):
        return ValidationResult(False, "INVALID_ACTION_TYPE", "Unsupported action type")
    if action.action_type not in state.private_profiles[role]["allowed_actions"]:
        return ValidationResult(
            False, "ACTION_NOT_ALLOWED", "Action is not permitted for this role"
        )

    active = state.active_proposal
    if active is None and action.action_type != "PROPOSE":
        return ValidationResult(False, "NO_ACTIVE_PROPOSAL", "A proposal is required first")
    if active is not None and action.action_type == "PROPOSE":
        return ValidationResult(False, "INVALID_ACTION_TYPE", "Use COUNTER to replace a proposal")
    if action.action_type in {"ACCEPT", "COUNTER"} and action.proposal_id != active.proposal_id:
        return ValidationResult(False, "STALE_PROPOSAL_ID", "Proposal ID is not current")
    if action.action_type == "WALK_AWAY":
        if action.proposal is not None:
            return ValidationResult(
                False, "INVALID_ACTION_SCHEMA", "WALK_AWAY cannot contain a proposal"
            )
        return ValidationResult(ok=True)
    if action.action_type == "ACCEPT":
        if action.proposal is not None:
            return ValidationResult(
                False, "MUTATION_ON_ACCEPT",
                "ACCEPT must reference only the current proposal ID",
            )
        result = _validate_hard_constraints(active, state, check_budget=True, check_floor=True)
        if role == "seller" and result.error_code == "BUDGET_EXCEEDED":
            return ValidationResult(
                False, "ACCEPTANCE_REQUIREMENTS_NOT_MET",
                "The proposal does not meet acceptance requirements",
            )
        return result

    proposal = action.proposal
    if (
        not isinstance(proposal, Proposal)
        or not isinstance(proposal.proposal_id, str)
        or not proposal.proposal_id
    ):
        return ValidationResult(False, "INVALID_ACTION_SCHEMA", "A proposal with an ID is required")
    if action.action_type == "PROPOSE" and action.proposal_id != proposal.proposal_id:
        return ValidationResult(False, "INVALID_PROPOSAL_ID", "Action and proposal IDs must match")
    if (
        proposal.acceptance_mode != "all_or_nothing"
        or not isinstance(proposal.terms, list)
        or not proposal.terms
    ):
        return ValidationResult(
            False, "INVALID_PROPOSAL",
            "A complete all-or-nothing proposal is required",
        )
    if not isinstance(proposal.conditions, list):
        return ValidationResult(False, "INVALID_CONDITION", "Conditions must be a list")

    quantities = _validate_purchase_terms(proposal, state)
    if isinstance(quantities, ValidationResult):
        return quantities

    try:
        totals = calculate_effective_totals(proposal, state)
    except _ConditionError as exc:
        if exc.code == "DISCOUNT_BELOW_FLOOR" and not proposal.conditions:
            return ValidationResult(False, "PRICE_BELOW_FLOOR", "The proposed price is invalid")
        return ValidationResult(False, exc.code, exc.message)
    if isinstance(totals, ValidationResult):
        return totals

    return _validate_hard_constraints(proposal, state, totals=totals, quantities=quantities)


def _validate_purchase_terms(
    proposal: Proposal, state: GameState
) -> dict[str, int] | ValidationResult:
    catalog = {entry["id"]: entry for entry in state.catalog}
    quantities: dict[str, int] = {}
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
            return ValidationResult(
                False, "INVALID_QUANTITY", "Quantity must be a positive integer"
            )
        if not _nonnegative_number(term.unit_price):
            return ValidationResult(
                False, "INVALID_PRICE", "Unit price must be non-negative and finite"
            )
        quantities[term.item_id] = quantities.get(term.item_id, 0) + term.quantity
    return quantities


def _validate_hard_constraints(
    proposal: Proposal,
    state: GameState,
    totals: dict | None = None,
    quantities: dict[str, int] | None = None,
    check_budget: bool = False,
    check_floor: bool = False,
) -> ValidationResult:
    catalog = {entry["id"]: entry for entry in state.catalog}
    if quantities is None:
        quantities = _validate_purchase_terms(proposal, state)
        if isinstance(quantities, ValidationResult):
            return quantities
    for item_id, quantity in quantities.items():
        if quantity > catalog[item_id]["seller_stock"]:
            return ValidationResult(
                False, "INSUFFICIENT_STOCK", "The requested quantity is unavailable"
            )

    if (
        not isinstance(proposal.original_prices, dict)
        or proposal.original_prices.keys() != quantities.keys()
    ):
        return ValidationResult(False, "INVALID_PRICE", "The proposed price is invalid")
    for term in proposal.terms:
        if proposal.original_prices.get(term.item_id) != term.unit_price:
            return ValidationResult(False, "INVALID_PRICE", "The proposed price is invalid")
    if totals is None:
        try:
            totals = calculate_effective_totals(proposal, state)
        except _ConditionError as exc:
            if exc.code == "DISCOUNT_BELOW_FLOOR" and not proposal.conditions:
                return ValidationResult(False, "PRICE_BELOW_FLOOR", "The proposed price is invalid")
            return ValidationResult(False, exc.code, exc.message)
        if isinstance(totals, ValidationResult):
            return totals
    if not proposal.conditions and (
        not isinstance(proposal.effective_prices, dict)
        or proposal.effective_prices != totals["effective_prices"]
    ):
        return ValidationResult(False, "INVALID_PRICE", "The proposed price is invalid")
    for item_id, effective_price in totals["effective_prices"].items():
        if effective_price < 0:
            return ValidationResult(False, "INVALID_PRICE", "The proposed price is invalid")
        if check_floor and effective_price < state.private_profiles["seller"]["minimum_unit_price"][item_id]:
            return ValidationResult(False, "PRICE_BELOW_FLOOR", "The proposed price is invalid")
    try:
        calculate_original_totals(proposal, state.legal_ranges["max_quantity"])
    except ValueError:
        return ValidationResult(
            False, "QUANTITY_LIMIT_EXCEEDED", "The requested quantity is not permitted"
        )
    if check_budget and totals["effective_total"] > Decimal(str(state.private_profiles["buyer"]["budget"])):
        return ValidationResult(
            False, "BUDGET_EXCEEDED", "The proposed total exceeds the allowed budget"
        )
    return ValidationResult(ok=True)


class _ConditionError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


def calculate_effective_totals(proposal: Proposal, state: GameState) -> dict | ValidationResult:
    """Evaluate typed bundle conditions and return original and effective totals."""
    if (
        not isinstance(proposal, Proposal)
        or not isinstance(proposal.terms, list)
        or not proposal.terms
    ):
        return ValidationResult(False, "MALFORMED_TERM", "Proposal terms are malformed")
    catalog = {entry["id"]: entry for entry in state.catalog}
    for term in proposal.terms:
        if (
            not isinstance(term, Term)
            or not isinstance(term.item_id, str)
            or not term.item_id
            or type(term.quantity) is not int
            or term.quantity <= 0
            or not _nonnegative_number(term.unit_price)
        ):
            return ValidationResult(False, "MALFORMED_TERM", "Proposal term fields are malformed")
        if term.item_id not in catalog:
            return ValidationResult(False, "INVALID_ITEM_ID", "Item is not in the catalog")
        if term.unit != catalog[term.item_id]["unit"]:
            return ValidationResult(False, "INVALID_UNIT", "Unit does not match the catalog")

    quantities: dict[str, int] = {}
    original_prices: dict[str, int | float] = {}
    for term in proposal.terms:
        quantities[term.item_id] = quantities.get(term.item_id, 0) + term.quantity
        if term.item_id in original_prices and original_prices[term.item_id] != term.unit_price:
            raise _ConditionError("INVALID_PRICE", "The proposed price is invalid")
        original_prices[term.item_id] = term.unit_price

    discounts: dict[str, int | float] = {}
    catalog_ids = set(catalog)
    max_discount = state.bundle_rules.get("max_discount_per_unit")
    for condition in proposal.conditions:
        if not isinstance(condition, Condition) or condition.type != "discount_if_item_in_bundle":
            raise _ConditionError("INVALID_CONDITION", "Condition type is not permitted")
        if (
            not isinstance(condition.discounted_item_id, str)
            or not isinstance(condition.required_item_id, str)
            or condition.discounted_item_id not in catalog_ids
            or condition.required_item_id not in catalog_ids
        ):
            raise _ConditionError("INVALID_ITEM_ID", "Condition item is not in the catalog")
        if (
            type(condition.minimum_required_quantity) is not int
            or condition.minimum_required_quantity <= 0
        ):
            raise _ConditionError(
                "INVALID_QUANTITY", "Required quantity must be a positive integer"
            )
        if not _nonnegative_number(condition.discount_per_unit):
            raise _ConditionError("INVALID_CONDITION", "Discount must be non-negative and finite")
        if (
            condition.discounted_item_id not in quantities
            or condition.required_item_id not in quantities
        ):
            raise _ConditionError(
                "CONDITION_REFERENCES_MISSING_ITEM",
                "The condition must reference items in the proposal",
            )
        if quantities.get(condition.required_item_id, 0) < condition.minimum_required_quantity:
            raise _ConditionError(
                "CONDITION_NOT_SATISFIED",
                "The conditional offer requirements are not met",
            )
        if max_discount is None or condition.discount_per_unit > max_discount:
            raise _ConditionError("DISCOUNT_EXCEEDS_CAP", "The proposed discount is not permitted")
        discounts[condition.discounted_item_id] = _add_money(
            discounts.get(condition.discounted_item_id, 0), condition.discount_per_unit
        )
        if discounts[condition.discounted_item_id] > max_discount:
            raise _ConditionError("DISCOUNT_EXCEEDS_CAP", "The proposed discount is not permitted")

    effective_prices = {
        item_id: _subtract_money(price, discounts.get(item_id, 0))
        for item_id, price in original_prices.items()
    }
    original_line_totals = [
        _multiply_money(term.quantity, term.unit_price) for term in proposal.terms
    ]
    effective_line_totals = [
        _multiply_money(term.quantity, effective_prices[term.item_id])
        for term in proposal.terms
    ]
    return {
        "original_prices": original_prices,
        "effective_prices": effective_prices,
        "original_line_totals": original_line_totals,
        "original_total": _sum_money(original_line_totals),
        "effective_line_totals": effective_line_totals,
        "effective_total": _sum_money(effective_line_totals),
    }


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
    whole_digits = max(
        (
            price.adjusted() + 1 + len(str(term.quantity))
            for term, price in zip(proposal.terms, prices)
        ),
        default=1,
    )
    fractional_digits = max((-price.as_tuple().exponent for price in prices), default=0)
    with localcontext() as context:
        context.prec = max(
            context.prec,
            whole_digits + fractional_digits + len(str(len(prices))) + 1,
        )
        line_totals = [
            term.quantity * (term.unit_price if type(term.unit_price) is int else price)
            for term, price in zip(proposal.terms, prices)
        ]
        total = sum(line_totals, 0)
    return {"line_totals": line_totals, "original_total": total}


def _nonnegative_number(value: object) -> bool:
    return (type(value) is int and value >= 0) or (
        type(value) is float and isfinite(value) and value >= 0
    )


def _add_money(left: int | float, right: int | float):
    if type(left) is int and type(right) is int:
        return left + right
    with localcontext() as context:
        context.prec = _money_precision((left, right))
        return Decimal(str(left)) + Decimal(str(right))


def _subtract_money(left: int | float, right: int | float):
    if type(left) is int and type(right) is int:
        return left - right
    with localcontext() as context:
        context.prec = _money_precision((left, right))
        return Decimal(str(left)) - Decimal(str(right))


def _multiply_money(quantity: int, price: int | float | Decimal):
    if type(price) is int:
        return quantity * price
    with localcontext() as context:
        context.prec = _money_precision((price,)) + len(str(quantity))
        return Decimal(str(price)) * quantity


def _sum_money(values: list):
    if all(type(value) is int for value in values):
        return sum(values, 0)
    with localcontext() as context:
        context.prec = _money_precision(values) + len(values) + 1
        return sum((Decimal(str(value)) for value in values), Decimal(0))


def _money_precision(values) -> int:
    return max(
        28,
        max((len(str(value).replace(".", "").lstrip("-")) for value in values), default=1) + 2,
    )
