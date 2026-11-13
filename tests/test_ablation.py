from bargaining_platform.cli import main
from bargaining_platform.experiments import ExperimentRunner


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
    treatment = ExperimentRunner("configs/ablation_with_bundle.json", db_path=tmp_path / "b.db").run()
    assert baseline[0].condition_id != treatment[0].condition_id
