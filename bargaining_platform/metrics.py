"""Calculate aggregate experiment metrics from persisted SQLite runs."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from .storage import DEFAULT_DB_PATH, _create_schema


def calculate_metrics(
    db_path: str | Path | None = DEFAULT_DB_PATH,
    condition_id: str | None = None,
) -> dict[str, dict[str, float | int | None]]:
    """Return aggregate metrics grouped by condition, using SQLite records only."""
    database = Path(db_path or DEFAULT_DB_PATH)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        _create_schema(connection)
        query = "SELECT run_id, condition_id FROM runs"
        parameters: tuple[Any, ...] = ()
        if condition_id is not None:
            query += " WHERE condition_id = ?"
            parameters = (condition_id,)
        run_rows = connection.execute(query, parameters).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in run_rows:
            events = connection.execute(
                """
                SELECT event_type, parsed_action_json, validation_result_json
                FROM events WHERE run_id = ? ORDER BY event_id
                """,
                (row["run_id"],),
            ).fetchall()
            run = connection.execute(
                "SELECT outcome_status, outcome_json, scenario_json FROM runs WHERE run_id = ?",
                (row["run_id"],),
            ).fetchone()
            grouped.setdefault(row["condition_id"] or "unassigned", []).append(
                _run_data(run, events)
            )

    return {key: _aggregate(runs) for key, runs in sorted(grouped.items())}


def compute_metrics(
    db_path: str | Path | None = DEFAULT_DB_PATH,
    condition_id: str | None = None,
) -> dict[str, dict[str, float | int | None]]:
    """Compatibility alias for callers that use ``compute_metrics``."""
    return calculate_metrics(db_path, condition_id=condition_id)


def _run_data(run: sqlite3.Row, events: list[sqlite3.Row]) -> dict[str, Any]:
    outcome = _decode(run["outcome_json"]) or {}
    scenario = _decode(run["scenario_json"]) or {}
    attempts = []
    for event in events:
        if event["event_type"] != "ACTION_ATTEMPT":
            continue
        attempts.append(
            {
                "action": _decode(event["parsed_action_json"]),
                "validation": _decode(event["validation_result_json"]) or {},
            }
        )
    valid = [attempt for attempt in attempts if attempt["validation"].get("ok") is True]
    proposal = None
    for attempt in valid:
        action = attempt["action"] or {}
        if action.get("action_type") in {"PROPOSE", "COUNTER"} and action.get("proposal"):
            proposal = action["proposal"]
        elif action.get("action_type") == "ACCEPT":
            break
    return {
        "status": run["outcome_status"],
        "outcome": outcome,
        "scenario": scenario,
        "attempts": attempts,
        "valid_count": len(valid),
        "proposal": proposal,
    }


def _aggregate(runs: list[dict[str, Any]]) -> dict[str, float | int | None]:
    total = len(runs)
    agreements = [run for run in runs if run["status"] == "AGREEMENT"]
    no_deals = [run for run in runs if run["status"] == "NO_DEAL"]
    utility_values = {
        name: [_number(run["outcome"].get("utility_metrics", {}).get(name)) for run in agreements]
        for name in ("buyer_utility", "seller_utility", "joint_utility")
    }
    utility_values = {name: [value for value in values if value is not None] for name, values in utility_values.items()}
    bundle_runs = [run for run in agreements if _is_bundle(run["proposal"])]
    discounts = [_discount_size(run["proposal"]) for run in bundle_runs]
    paper = [_paper_metrics(run) for run in agreements]
    gft = [value["gft"] for value in paper if value["gft"] is not None]
    npb = [value["npb"] for value in paper if value["npb"] is not None]
    ir_values = [value["ir"] for value in paper if value["ir"] is not None]

    def mean(values: list[Fraction | None]) -> float | None:
        values = [value for value in values if value is not None]
        return _float(sum(values, Fraction(0)) / len(values)) if values else None

    result: dict[str, float | int | None] = {
        "run_count": total,
        "agreement_rate": len(agreements) / total if total else 0.0,
        "no_deal_rate": len(no_deals) / total if total else 0.0,
        "average_buyer_utility": mean(utility_values["buyer_utility"]),
        "average_seller_utility": mean(utility_values["seller_utility"]),
        "average_joint_utility": mean(utility_values["joint_utility"]),
        "average_rounds": (
            sum((run["valid_count"] + 1) // 2 for run in runs) / total if total else 0.0
        ),
        "bundle_frequency": len(bundle_runs) / len(agreements) if agreements else 0.0,
        "average_discount_size": mean(discounts),
        "average_retries": sum(
            sum(attempt["validation"].get("ok") is not True for attempt in run["attempts"])
            for run in runs
        ) / total
        if total
        else 0.0,
        "gft": mean(gft),
        "npb": mean(npb),
        "ir": sum(ir_values) / len(ir_values) if ir_values else None,
    }
    return result


def _paper_metrics(run: dict[str, Any]) -> dict[str, Fraction | None | bool]:
    proposal = run["proposal"]
    if not proposal:
        return {"gft": None, "npb": None, "ir": None}
    scenario = run["scenario"]
    buyer_values = scenario.get("private_profiles", {}).get("buyer", {}).get("unit_value", {})
    seller_costs = scenario.get("private_profiles", {}).get("seller", {}).get("unit_cost", {})
    terms = proposal.get("terms") or []
    if not terms:
        return {"gft": None, "npb": None, "ir": None}
    value = Fraction(0)
    cost = Fraction(0)
    payment = Fraction(0)
    for term in terms:
        item_id = term.get("item_id")
        quantity = _fraction(term.get("quantity"))
        unit_price = _price(proposal, item_id, term.get("unit_price"))
        if item_id not in buyer_values or item_id not in seller_costs or unit_price is None or quantity is None:
            return {"gft": None, "npb": None, "ir": None}
        value += quantity * _fraction(buyer_values[item_id])
        cost += quantity * _fraction(seller_costs[item_id])
        payment += quantity * unit_price
    surplus = value - cost
    if surplus == 0:
        return {"gft": Fraction(0), "npb": None, "ir": cost <= payment <= value}
    npb = (payment - (value + cost) / 2) / surplus
    return {"gft": surplus, "npb": npb, "ir": cost <= payment <= value}


def _is_bundle(proposal: dict[str, Any] | None) -> bool:
    return bool(proposal and len({term.get("item_id") for term in proposal.get("terms", [])}) > 1)


def _discount_size(proposal: dict[str, Any] | None) -> Fraction | None:
    if not proposal:
        return None
    original = proposal.get("original_prices") or {}
    effective = proposal.get("effective_prices") or {}
    total = Fraction(0)
    found = False
    for term in proposal.get("terms", []):
        item_id = term.get("item_id")
        original_price = _fraction(original.get(item_id, term.get("unit_price")))
        effective_price = _fraction(effective.get(item_id, original.get(item_id, term.get("unit_price"))))
        quantity = _fraction(term.get("quantity"))
        if original_price is None or effective_price is None or quantity is None:
            return None
        total += quantity * (original_price - effective_price)
        found = found or original_price != effective_price
    return total if found else Fraction(0)


def _price(proposal: dict, item_id: str, fallback: Any) -> Fraction | None:
    prices = proposal.get("effective_prices") or {}
    return _fraction(prices.get(item_id, fallback))


def _fraction(value: Any) -> Fraction | None:
    if value is None:
        return None
    try:
        return Fraction(Decimal(str(value)))
    except (ArithmeticError, ValueError):
        return None


def _number(value: Any) -> Fraction | None:
    return _fraction(value)


def _float(value: Fraction) -> float:
    return float(value)


def _decode(value: str | None) -> Any:
    return json.loads(value) if value is not None else None
