"""Calculate aggregate experiment metrics from persisted SQLite runs."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from .models import GameState, Proposal, ValidationResult, json_safe
from .storage import DEFAULT_DB_PATH, _create_schema
from .storage import load_run_from_sqlite
from .validator import calculate_effective_totals


_PARSER_ERRORS = {"MALFORMED_JSON", "SCHEMA_VIOLATION"}
_HARD_CONSTRAINT_ERRORS = {
    "PRICE_BELOW_FLOOR",
    "BUDGET_EXCEEDED",
    "INSUFFICIENT_STOCK",
}
_BUNDLE_ERRORS = {"CONDITION_NOT_SATISFIED", "DISCOUNT_EXCEEDS_CAP"}


def calculate_metrics(
    db_path: str | Path | None = DEFAULT_DB_PATH,
    condition_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, dict[str, float | int | None]]:
    """Return aggregate metrics grouped by condition, using SQLite records only."""
    database = Path(db_path or DEFAULT_DB_PATH)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        _create_schema(connection)
        query = """
            SELECT
                r.run_id, r.condition_id, r.outcome_status, r.outcome_json, r.scenario_json,
                e.event_type, e.parsed_action_json, e.validation_result_json
            FROM runs AS r
            LEFT JOIN events AS e ON e.run_id = r.run_id
        """
        parameters: tuple[Any, ...] = ()
        filters = []
        if condition_id is not None:
            filters.append("r.condition_id = ?")
            parameters += (condition_id,)
        if run_id is not None:
            filters.append("r.run_id = ?")
            parameters += (run_id,)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY r.run_id, e.event_id"
        grouped_rows: dict[str, dict[str, Any]] = {}
        for row in connection.execute(query, parameters):
            run = grouped_rows.setdefault(
                row["run_id"],
                {"row": row, "events": []},
            )
            if row["event_type"] is not None:
                run["events"].append(row)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for run in grouped_rows.values():
        row = run["row"]
        grouped.setdefault(row["condition_id"] or "unassigned", []).append(
            _run_data(row, run["events"])
        )

    return {key: _aggregate(runs) for key, runs in sorted(grouped.items())}


def compute_metrics(
    db_path: str | Path | None = DEFAULT_DB_PATH,
    condition_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, dict[str, float | int | None]]:
    """Compatibility alias for callers that use ``compute_metrics``."""
    return calculate_metrics(db_path, condition_id=condition_id, run_id=run_id)


def generate_failure_report(
    run_id: str,
    db_path: str | Path | None = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Extract structured validation and termination failures for one run."""
    run = load_run_from_sqlite(run_id, db_path or DEFAULT_DB_PATH)
    failures: list[dict[str, Any]] = []
    valid_actions = 0
    timeout_seen = False

    for event in run["events"]:
        if event["event_type"] == "ACTION_ATTEMPT":
            validation = event.get("validation_result") or {}
            action = event.get("parsed_action") or {}
            round_number = valid_actions // 2 + 1
            if validation.get("ok") is not True:
                error_code = validation.get("error_code") or "INVALID_ACTION"
                failures.append(
                    _failure(
                        event,
                        round_number,
                        _failure_category(error_code),
                        error_code,
                        action or None,
                    )
                )
            elif action.get("action_type") == "WALK_AWAY":
                failures.append(
                    _failure(
                        event,
                        round_number,
                        "premature_termination",
                        "WALK_AWAY",
                        action,
                    )
                )
            if validation.get("ok") is True:
                valid_actions += 1
        elif (
            event["event_type"] == "STATE_TRANSITION"
            and "Maximum rounds" in (event.get("state_transition_summary") or "")
        ):
            timeout_seen = True
            failures.append(
                _failure(
                    event,
                    (valid_actions + 1) // 2,
                    "timeout",
                    "MAX_ROUNDS",
                    None,
                )
            )

    if run["outcome_status"] == "MAX_ROUNDS" and not timeout_seen:
        failures.append(
            {
                "event_id": None,
                "event_type": "OUTCOME",
                "round": (valid_actions + 1) // 2,
                "actor": None,
                "category": "timeout",
                "error_code": "MAX_ROUNDS",
                "action_attempted": None,
            }
        )

    counts: dict[str, int] = {}
    for failure in failures:
        counts[failure["category"]] = counts.get(failure["category"], 0) + 1
    return {
        "run_id": run_id,
        "outcome_status": run["outcome_status"],
        "failure_counts": counts,
        "failures": failures,
    }


def format_failure_report(report: dict[str, Any]) -> str:
    """Render a failure report without interpreting free-form model text."""
    lines = [
        f"Failure report for run {report['run_id']}",
        f"Outcome: {report['outcome_status']}",
    ]
    if not report["failures"]:
        lines.append("No failures recorded.")
        return "\n".join(lines)
    lines.append("Failures:")
    for failure in report["failures"]:
        lines.append(
            f"- Event {failure['event_id']}, Round {failure['round']}, "
            f"Actor: {failure['actor']}, Category: {failure['category']}, "
            f"Error: {failure['error_code']}, "
            f"Action attempted: {failure['action_attempted']}"
        )
    return "\n".join(lines)


def _failure(
    event: dict[str, Any],
    round_number: int,
    category: str,
    error_code: str,
    action: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "round": round_number,
        "actor": event["actor"],
        "category": category,
        "error_code": error_code,
        "action_attempted": action if action is not None else event.get("raw_output"),
    }


def _failure_category(error_code: str) -> str:
    if error_code in _PARSER_ERRORS:
        return "parser_error"
    if error_code in _HARD_CONSTRAINT_ERRORS:
        return "hard_constraint"
    if error_code in _BUNDLE_ERRORS:
        return "bundle_semantic"
    return "validation_error"


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
            proposal = _authoritative_proposal(action["proposal"], scenario)
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
    """Return the mean per-unit discount across discounted item lines."""
    if not proposal:
        return None
    original = proposal.get("original_prices") or {}
    effective = proposal.get("effective_prices") or {}
    discounts: list[Fraction] = []
    for term in proposal.get("terms", []):
        item_id = term.get("item_id")
        original_price = _fraction(original.get(item_id, term.get("unit_price")))
        effective_price = _fraction(effective.get(item_id, original.get(item_id, term.get("unit_price"))))
        quantity = _fraction(term.get("quantity"))
        if original_price is None or effective_price is None or quantity is None:
            return None
        difference = original_price - effective_price
        if difference != 0:
            discounts.append(difference)
    return sum(discounts, Fraction(0)) / len(discounts) if discounts else Fraction(0)


def _authoritative_proposal(proposal_data: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    """Recompute prices from typed terms and conditions, ignoring logged price outputs."""
    data = json.loads(json.dumps(proposal_data))
    data.setdefault("decision_summary", "")
    data["effective_prices"] = {}
    try:
        proposal = Proposal.from_dict(data)
        state = GameState(
            current_actor="seller",
            round_number=1,
            catalog=scenario["catalog"],
            private_profiles=scenario["private_profiles"],
            max_rounds=scenario["max_rounds"],
            legal_ranges=scenario["legal_ranges"],
            bundle_rules=scenario["bundle_rules"],
        )
        totals = calculate_effective_totals(proposal, state)
    except (KeyError, TypeError, ValueError):
        return data
    if isinstance(totals, ValidationResult):
        return data
    data["original_prices"] = json_safe(totals["original_prices"])
    data["effective_prices"] = json_safe(totals["effective_prices"])
    return data


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
