from bargaining_platform.cli import main
import json

import pytest

from bargaining_platform.experiments import ExperimentRunner, compare_experiment_configs


def test_compare_command_runs_both_configs_and_reports(tmp_path, capsys):
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


def test_compare_namespaces_identical_variations(tmp_path):
    config = {
        "repetitions": 1,
        "scenario_path": "scenarios/shop_bundle.json",
        "agent_type": "mock",
        "variations": [{}],
    }
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(config), encoding="utf-8")
    second.write_text(json.dumps(config), encoding="utf-8")

    runner_a = ExperimentRunner(first, db_path=tmp_path / "a.db")
    runner_b = ExperimentRunner(second, db_path=tmp_path / "b.db")

    assert runner_a.run()[0].condition_id != runner_b.run()[0].condition_id


def test_compare_rejects_multiple_variations(tmp_path):
    config = {
        "repetitions": 1,
        "scenario_path": "scenarios/shop_bundle.json",
        "agent_type": "mock",
        "variations": [{}, {}],
    }
    config_path = tmp_path / "multi.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match="single variation"):
        compare_experiment_configs(config_path, config_path, db_path=tmp_path / "runs.db")
