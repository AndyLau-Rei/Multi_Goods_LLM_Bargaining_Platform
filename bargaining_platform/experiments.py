"""Run repeated controlled negotiations without calculating aggregate metrics."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model_adapter import MockLLMAdapter, OpenAIAdapter
from .models import validate_scenario
from .runner import default_mock_adapters, run_llm_negotiation
from .scenario import load_scenario
from .storage import DEFAULT_DB_PATH


@dataclass(frozen=True)
class ExperimentResult:
    """Identity and terminal status for one persisted repetition."""

    condition_id: str
    run_id: str
    outcome_status: str | None


def condition_id_for(variation: dict[str, Any], namespace: str | None = None) -> str:
    """Return a stable ID for one canonical variation definition."""
    payload = {"variation": variation}
    if namespace is not None:
        payload["namespace"] = namespace
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"condition-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]}"


class ExperimentRunner:
    """Expand configured variations into sequential, persisted negotiation runs."""

    def __init__(
        self,
        config: dict[str, Any] | str | Path,
        *,
        db_path: str | Path = DEFAULT_DB_PATH,
        condition_namespace: str | None = None,
        seller_prompt_path: str | Path | None = None,
        buyer_prompt_path: str | Path | None = None,
    ):
        self.config_path = Path(config) if isinstance(config, (str, Path)) else None
        if self.config_path is not None:
            self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        else:
            self.config = deepcopy(config)
        if not isinstance(self.config, dict):
            raise ValueError("Experiment config must be an object")
        repetitions = self.config.get("repetitions")
        if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions <= 0:
            raise ValueError("repetitions must be a positive integer")
        if not isinstance(self.config.get("scenario_path"), str):
            raise ValueError("scenario_path must be a string")
        variations = self.config.get("variations")
        if not isinstance(variations, list) or not variations:
            raise ValueError("variations must be a non-empty list")
        if any(not isinstance(variation, dict) for variation in variations):
            raise ValueError("each variation must be an object")
        self.db_path = Path(db_path)
        self.seller_prompt_path = seller_prompt_path
        self.buyer_prompt_path = buyer_prompt_path
        self.condition_namespace = condition_namespace or self.config.get("condition_namespace")
        if self.condition_namespace is None and self.config_path is not None:
            self.condition_namespace = self.config_path.stem

    @staticmethod
    def condition_id(variation: dict[str, Any]) -> str:
        return condition_id_for(variation)

    def run(self) -> list[ExperimentResult]:
        base_scenario = load_scenario(self._scenario_path())
        results: list[ExperimentResult] = []
        for variation in self.config["variations"]:
            condition_id = condition_id_for(variation, self.condition_namespace)
            scenario = deepcopy(base_scenario)
            agent_type = variation.get("agent_type", self.config.get("agent_type", "mock"))
            overrides = {key: value for key, value in variation.items() if key != "agent_type"}
            _deep_merge(scenario, overrides)
            validate_scenario(scenario)
            for repetition in range(1, self.config["repetitions"] + 1):
                run_id = f"{condition_id}-rep-{repetition}-{uuid.uuid4().hex[:8]}"
                adapters, mock_demo = self._adapters(agent_type, scenario)
                engine = run_llm_negotiation(
                    deepcopy(scenario),
                    adapters,
                    run_id=run_id,
                    db_path=self.db_path,
                    mock_demo=mock_demo,
                    condition_id=condition_id,
                    seller_prompt_path=self.seller_prompt_path,
                    buyer_prompt_path=self.buyer_prompt_path,
                )
                results.append(
                    ExperimentResult(
                        condition_id,
                        run_id,
                        engine.state.outcome.status if engine.state.outcome else None,
                    )
                )
        return results

    def _scenario_path(self) -> Path:
        path = Path(self.config["scenario_path"])
        if path.is_absolute() or path.exists() or self.config_path is None:
            return path
        return self.config_path.parent / path

    @staticmethod
    def _adapters(agent_type: str, scenario: dict) -> tuple[dict, bool]:
        if agent_type not in {"mock", "llm", "scripted"}:
            raise ValueError(f"Unsupported agent_type: {agent_type}")
        if agent_type == "llm" and os.getenv("LLM_API_KEY"):
            adapter = OpenAIAdapter()
            return {"seller": adapter, "buyer": adapter}, False
        if (
            agent_type == "mock"
            and scenario.get("bundle_rules", {}).get("max_discount_per_unit") == 0
        ):
            return _no_bundle_mock_adapters(), True
        if agent_type == "mock" and _buyer_budget(scenario) >= 160:
            return default_mock_adapters(), True
        return _budget_safe_mock_adapters(scenario), True


def _deep_merge(target: dict, overrides: dict) -> None:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)


def _buyer_budget(scenario: dict) -> float:
    return float(scenario["private_profiles"]["buyer"]["budget"])


def _budget_safe_mock_adapters(scenario: dict) -> dict[str, MockLLMAdapter]:
    budget = _buyer_budget(scenario)
    if budget < 80:
        return {
            "seller": MockLLMAdapter([
                json.dumps({"action_type": "WALK_AWAY", "proposal_id": None})
            ]),
            "buyer": MockLLMAdapter([]),
        }
    price = min(120, budget)
    proposal = {
        "proposal_id": "experiment-a",
        "acceptance_mode": "all_or_nothing",
        "terms": [
            {
                "type": "purchase", "item_id": "A", "quantity": 1,
                "unit_price": price, "unit": "piece",
            }
        ],
        "conditions": [],
        "decision_summary": "",
        "original_prices": {"A": price},
        "effective_prices": {"A": price},
    }
    return {
        "seller": MockLLMAdapter([
            json.dumps({
                "action_type": "PROPOSE", "proposal_id": "experiment-a",
                "proposal": proposal,
            })
        ]),
        "buyer": MockLLMAdapter([
            json.dumps({"action_type": "ACCEPT", "proposal_id": "experiment-a"})
        ]),
    }


def _no_bundle_mock_adapters() -> dict[str, MockLLMAdapter]:
    proposal = {
        "proposal_id": "baseline-a",
        "acceptance_mode": "all_or_nothing",
        "terms": [
            {"type": "purchase", "item_id": "A", "quantity": 1, "unit_price": 120, "unit": "piece"}
        ],
        "conditions": [],
        "decision_summary": "",
        "original_prices": {"A": 120},
        "effective_prices": {"A": 120},
    }
    return {
        "seller": MockLLMAdapter([
            json.dumps({
                "action_type": "PROPOSE", "proposal_id": "baseline-a",
                "proposal": proposal,
            })
        ]),
        "buyer": MockLLMAdapter([
            json.dumps({"action_type": "ACCEPT", "proposal_id": "baseline-a"})
        ]),
    }


def compare_experiment_configs(
    config_a: str | Path,
    config_b: str | Path,
    *,
    db_path: str | Path | None = DEFAULT_DB_PATH,
) -> str:
    """Run two configs and return a plain-text baseline/treatment comparison."""
    from .metrics import calculate_metrics

    database = db_path or DEFAULT_DB_PATH
    runner_a = ExperimentRunner(
        config_a,
        db_path=database,
        condition_namespace="baseline",
    )
    runner_b = ExperimentRunner(
        config_b,
        db_path=database,
        condition_namespace="treatment",
    )
    if len(runner_a.config["variations"]) != 1 or len(runner_b.config["variations"]) != 1:
        raise ValueError("Comparison only supports configs with a single variation for now.")
    results_a = runner_a.run()
    results_b = runner_b.run()
    condition_a = results_a[0].condition_id
    condition_b = results_b[0].condition_id
    metrics_a = calculate_metrics(database, condition_id=condition_a)[condition_a]
    metrics_b = calculate_metrics(database, condition_id=condition_b)[condition_b]
    return format_comparison(metrics_a, metrics_b)


def format_comparison(
    metrics_a: dict[str, Any],
    metrics_b: dict[str, Any],
) -> str:
    """Format the requested metrics as a dependency-free comparison table."""
    rows = (
        ("Agreement Rate", "agreement_rate"),
        ("Mean Joint Utility", "average_joint_utility"),
        ("GFT", "gft"),
        ("Bundle Frequency", "bundle_frequency"),
    )
    lines = [
        "Metric                  Condition A (Baseline: No Bundles)  "
        "Condition B (Treatment: Bundles Enabled)",
        "-" * 102,
    ]
    for label, key in rows:
        lines.append(f"{label:<24} {str(metrics_a.get(key)):<34} {str(metrics_b.get(key))}")
    return "\n".join(lines)
