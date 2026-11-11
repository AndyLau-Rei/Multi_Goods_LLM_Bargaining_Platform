<<<<<<< HEAD
"""Define the scenario configuration schema."""

from math import isfinite


PUBLIC_KEYS = {"scenario_id", "max_rounds", "starting_role", "catalog", "private_profiles"}
CATALOG_KEYS = {"id", "type", "unit", "seller_stock"}
SELLER_KEYS = {"unit_cost", "minimum_unit_price"}
BUYER_KEYS = {"unit_value", "budget"}


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
    return scenario
=======
"""Define action, offer, term, and game-state models."""
>>>>>>> 477fb69f6db7b0d7c7aada3e78e36efc42d23150
