import json
import pytest

from bargaining_platform.cli import main
from bargaining_platform import experiments as experiment_module
from bargaining_platform.experiments import ExperimentRunner, compare_experiment_configs
from bargaining_platform.model_adapter import MockLLMAdapter


def _use_test_adapters(monkeypatch):
    proposal = {
        "proposal_id": "ablation-proposal",
        "acceptance_mode": "all_or_nothing",
        "terms": [{"type": "purchase", "item_id": "A", "quantity": 1, "unit_price": 120, "unit": "piece"}],
        "conditions": [], "decision_summary": "",
        "original_prices": {"A": 120}, "effective_prices": {"A": 120},
    }
    monkeypatch.setattr(experiment_module, "create_role_adapters", lambda *_: ({
        "seller": MockLLMAdapter([json.dumps({
            "action_type": "PROPOSE", "proposal_id": "ablation-proposal", "proposal": proposal,
        })]),
        "buyer": MockLLMAdapter([json.dumps({"action_type": "WALK_AWAY"})]),
    }, {"execution": "test"}))


def test_compare_command_runs_both_configs_and_reports(tmp_path, capsys, monkeypatch):
    _use_test_adapters(monkeypatch)
    database = tmp_path / "ablation.db"
    exit_code = main([
        "compare",
        "--config-a",
        "configs/ablation_no_bundle.json",
        "--config-b",
        "configs/ablation_with_bundle.json",
        "--db-path",
        str(database),
    ])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Condition A (Baseline: No Bundles)" in output
    assert "Condition B (Treatment: Bundles Enabled)" in output
    assert "Agreement Rate" in output
    assert output.strip()

    baseline = ExperimentRunner("configs/ablation_no_bundle.json", db_path=tmp_path / "a.db").run()
    treatment = ExperimentRunner(
        "configs/ablation_with_bundle.json", db_path=tmp_path / "b.db"
    ).run()
    assert baseline[0].condition_id != treatment[0].condition_id


def test_compare_namespaces_identical_variations(tmp_path, monkeypatch):
    _use_test_adapters(monkeypatch)
    config = {
        "repetitions": 1,
        "scenario_path": "scenarios/shop_bundle.json",
        "variations": [{}],
    }
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(config), encoding="utf-8")
    second.write_text(json.dumps(config), encoding="utf-8")

    runner_a = ExperimentRunner(first, db_path=tmp_path / "a.db")
    runner_b = ExperimentRunner(second, db_path=tmp_path / "b.db")

    assert runner_a.run()[0].condition_id != runner_b.run()[0].condition_id


def test_compare_rejects_multiple_variations(tmp_path, monkeypatch):
    _use_test_adapters(monkeypatch)
    config = {
        "repetitions": 1,
        "scenario_path": "scenarios/shop_bundle.json",
        "variations": [{}, {}],
    }
    config_path = tmp_path / "multi.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match="single variation"):
        compare_experiment_configs(config_path, config_path, db_path=tmp_path / "runs.db")
