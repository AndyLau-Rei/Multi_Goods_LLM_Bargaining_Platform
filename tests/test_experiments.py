import json
import sqlite3
from types import SimpleNamespace

import pytest

from bargaining_platform import experiments as experiment_module
from bargaining_platform.experiments import ExperimentRunner, condition_id_for


def test_experiment_matrix_runs(tmp_path):
    config = {
        "repetitions": 2,
        "scenario_path": "scenarios/shop_bundle.json",
        "variations": [
            {"agent_type": "mock", "private_profiles": {"buyer": {"budget": 150}}},
            {"agent_type": "mock", "private_profiles": {"buyer": {"budget": 200}}},
        ],
    }
    database = tmp_path / "runs.db"
    results = ExperimentRunner(config, db_path=database).run()

    assert len(results) == 4
    assert len({result.run_id for result in results}) == 4
    assert {result.condition_id for result in results} == {
        condition_id_for(config["variations"][0]),
        condition_id_for(config["variations"][1]),
    }
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT run_id, condition_id FROM runs").fetchall()
    assert len(rows) == 4
    assert {(run_id, condition_id) for run_id, condition_id in rows} == {
        (result.run_id, result.condition_id) for result in results
    }


def test_condition_ids_are_stable():
    variation = {"agent_type": "mock", "private_profiles": {"buyer": {"budget": 150}}}
    assert condition_id_for(variation) == condition_id_for(json.loads(json.dumps(variation)))


def test_experiment_config_selects_models_per_role(tmp_path, monkeypatch):
    monkeypatch.setenv("SELLER_LLM_API_KEY", "seller-key")
    monkeypatch.setenv("BUYER_LLM_API_KEY", "buyer-key")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    captured = {}

    def fake_run(scenario, adapters, **kwargs):
        captured["adapters"] = adapters
        captured["kwargs"] = kwargs
        return SimpleNamespace(state=SimpleNamespace(outcome=SimpleNamespace(status="AGREEMENT")))

    monkeypatch.setattr(experiment_module, "run_llm_negotiation", fake_run)
    runner = ExperimentRunner({
        "repetitions": 1,
        "scenario_path": "scenarios/shop_bundle.json",
        "agent_type": "llm",
        "seller_model": "gpt-4o-mini",
        "buyer_model": "gemini-2.0-flash",
        "variations": [{}],
    }, db_path=tmp_path / "unused.db")

    results = runner.run()

    assert results[0].outcome_status == "AGREEMENT"
    assert captured["adapters"]["seller"].model == "gpt-4o-mini"
    assert captured["adapters"]["buyer"].model == "gemini-2.0-flash"
    assert captured["adapters"]["seller"] is not captured["adapters"]["buyer"]
    assert captured["kwargs"]["model_config"]["roles"]["buyer"]["public_alias"] == "gemini-2.0-flash"


def test_blank_cli_model_alias_is_rejected_instead_of_using_config_default(tmp_path):
    runner = ExperimentRunner({
        "repetitions": 1,
        "scenario_path": "scenarios/shop_bundle.json",
        "agent_type": "llm",
        "seller_model": "gpt-4o-mini",
        "variations": [{}],
    }, seller_model="", db_path=tmp_path / "unused.db")

    with pytest.raises(ValueError, match="^UNKNOWN_MODEL$"):
        runner.run()
