"""Define the scenario schema and negotiation data models."""

from dataclasses import asdict, dataclass, field
from math import isfinite


PUBLIC_KEYS = {
    "scenario_id",
    "max_rounds",
    "starting_role",
    "catalog",
    "private_profiles",
    "legal_ranges",
    "bundle_rules",
}
CATALOG_KEYS = {"id", "type", "unit", "seller_stock"}
SELLER_KEYS = {"unit_cost", "minimum_unit_price", "reservation_utility", "allowed_actions"}
BUYER_KEYS = {"unit_value", "budget", "reservation_utility", "allowed_actions"}
ACTION_TYPES = {"PROPOSE", "ACCEPT", "COUNTER", "WALK_AWAY"}


def _fields(value: object, required: set[str], location: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    missing = required - value.keys()
    if missing:
        raise ValueError(f"Missing required key: {location}.{sorted(missing)[0]}")
    extra = value.keys() - required
    if extra:
        key = sorted(extra)[0]
        if location.startswith("private_profiles") and key in PUBLIC_KEYS:
            raise ValueError(f"Ambiguous visibility: {location}.{key}")
        raise ValueError(f"Unexpected key: {location}.{key}")
    return value


def _text(value: object, location: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must be a non-empty string")


def _number(value: object, location: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (
        isinstance(value, float) and not isfinite(value)
    ):
        raise ValueError(f"{location} must be a finite number")


def validate_scenario(data: object) -> dict:
    """Validate the shop scenario's required structure and field visibility."""
    if isinstance(data, dict) and isinstance(data.get("private_profiles"), dict):
        profiles = data["private_profiles"]
        for location, fields in (
            ("private_profiles", profiles),
            ("private_profiles.seller", profiles.get("seller")),
            ("private_profiles.buyer", profiles.get("buyer")),
        ):
            if isinstance(fields, dict):
                misplaced = PUBLIC_KEYS & fields.keys()
                if misplaced:
                    raise ValueError(f"Ambiguous visibility: {location}.{sorted(misplaced)[0]}")
    scenario = _fields(data, PUBLIC_KEYS, "scenario")
    _text(scenario["scenario_id"], "scenario.scenario_id")
    rounds = scenario["max_rounds"]
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds <= 0:
        raise ValueError("scenario.max_rounds must be a positive integer")
    if scenario["starting_role"] not in ("seller", "buyer"):
        raise ValueError("scenario.starting_role must be seller or buyer")

    catalog = scenario["catalog"]
    if not isinstance(catalog, list) or not catalog:
        raise ValueError("scenario.catalog must be a non-empty list")
    item_ids = set()
    for index, entry in enumerate(catalog):
        location = f"catalog[{index}]"
        entry = _fields(entry, CATALOG_KEYS, location)
        for key in ("id", "type", "unit"):
            _text(entry[key], f"{location}.{key}")
        if entry["id"] in item_ids:
            raise ValueError(f"Duplicate catalog ID: {entry['id']}")
        item_ids.add(entry["id"])
        stock = entry["seller_stock"]
        if isinstance(stock, bool) or not isinstance(stock, int) or stock < 0:
            raise ValueError(f"{location}.seller_stock must be a non-negative integer")

    legal_ranges = _fields(scenario["legal_ranges"], {"max_quantity"}, "legal_ranges")
    max_quantity = _fields(legal_ranges["max_quantity"], item_ids, "legal_ranges.max_quantity")
    for item_id, value in max_quantity.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"legal_ranges.max_quantity.{item_id} must be a positive integer")

    bundle_rules = _fields(
        scenario["bundle_rules"], {"max_discount_per_unit", "allowed_condition_types"}, "bundle_rules"
    )
    _number(bundle_rules["max_discount_per_unit"], "bundle_rules.max_discount_per_unit")
    if bundle_rules["max_discount_per_unit"] < 0:
        raise ValueError("bundle_rules.max_discount_per_unit must be non-negative")
    condition_types = bundle_rules["allowed_condition_types"]
    if (
        not isinstance(condition_types, list)
        or not condition_types
        or any(condition_type != "discount_if_item_in_bundle" for condition_type in condition_types)
    ):
        raise ValueError("bundle_rules.allowed_condition_types contains an unsupported type")

    profiles = _fields(scenario["private_profiles"], {"seller", "buyer"}, "private_profiles")
    seller = _fields(profiles["seller"], SELLER_KEYS, "private_profiles.seller")
    buyer = _fields(profiles["buyer"], BUYER_KEYS, "private_profiles.buyer")
    for name, values in (
        ("seller.unit_cost", seller["unit_cost"]),
        ("seller.minimum_unit_price", seller["minimum_unit_price"]),
        ("buyer.unit_value", buyer["unit_value"]),
    ):
        location = f"private_profiles.{name}"
        values = _fields(values, item_ids, location)
        for item_id, value in values.items():
            _number(value, f"{location}.{item_id}")
    _number(buyer["budget"], "private_profiles.buyer.budget")
    for role, profile in (("seller", seller), ("buyer", buyer)):
        _number(profile["reservation_utility"], f"private_profiles.{role}.reservation_utility")
        actions = profile["allowed_actions"]
        if (
            not isinstance(actions, list)
            or not actions
            or any(not isinstance(action, str) or action not in ACTION_TYPES for action in actions)
            or len(actions) != len(set(actions))
        ):
            raise ValueError(f"private_profiles.{role}.allowed_actions contains an invalid action")
    return scenario


class _Serializable:
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**data)


@dataclass
class Term(_Serializable):
    type: str
    item_id: str | None = None
    quantity: int | float | None = None
    unit_price: int | float | None = None
    unit: str | None = None


@dataclass
class Condition(_Serializable):
    type: str
    discounted_item_id: str
    discount_per_unit: int | float
    required_item_id: str
    minimum_required_quantity: int | float


@dataclass
class Proposal(_Serializable):
    proposal_id: str
    acceptance_mode: str
    terms: list[Term]
    conditions: list[Condition]
    decision_summary: str
    original_prices: dict[str, int | float]
    effective_prices: dict[str, int | float]

    @classmethod
    def from_dict(cls, data: dict) -> "Proposal":
        return cls(**{
            **data,
            "terms": [Term.from_dict(term) for term in data["terms"]],
            "conditions": [Condition.from_dict(condition) for condition in data["conditions"]],
        })


@dataclass
class Action(_Serializable):
    action_type: str
    proposal_id: str | None = None
    proposal: Proposal | None = None
    actor: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Action":
        proposal = data.get("proposal")
        return cls(**{**data, "proposal": Proposal.from_dict(proposal) if proposal is not None else None})


@dataclass
class ValidationResult(_Serializable):
    ok: bool
    error_code: str | None = None
    message: str | None = None


@dataclass
class Outcome(_Serializable):
    status: str
    final_proposal_id: str | None = None
    utility_metrics: dict[str, int | float] = field(default_factory=dict)


@dataclass
class GameState(_Serializable):
    current_actor: str
    round_number: int
    active_proposal: Proposal | None = None
    public_transcript: list[dict] = field(default_factory=list)
    outcome: Outcome | None = None
    catalog: list[dict] = field(default_factory=list)
    private_profiles: dict = field(default_factory=dict)
    invalid_attempts: int = 0
    private_attempt_log: list[dict] = field(default_factory=list)
    max_rounds: int = 0
    legal_ranges: dict = field(default_factory=dict)
    bundle_rules: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "GameState":
        proposal = data.get("active_proposal")
        outcome = data.get("outcome")
        return cls(**{
            **data,
            "active_proposal": Proposal.from_dict(proposal) if proposal is not None else None,
            "outcome": Outcome.from_dict(outcome) if outcome is not None else None,
        })
