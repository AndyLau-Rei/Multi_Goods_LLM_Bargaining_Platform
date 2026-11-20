from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bargaining_platform import cli
from bargaining_platform.cli import main


def test_replay_command_routes_to_storage(tmp_path, capsys):
    state = SimpleNamespace(outcome=SimpleNamespace(status="AGREEMENT"))
    with patch("bargaining_platform.cli.replay_run", return_value=state) as replay:
        assert main(["replay", "--run-id", "run-1", "--db-path", str(tmp_path / "runs.db")]) == 0

    replay.assert_called_once_with("run-1", str(tmp_path / "runs.db"))
    assert "Replay successful" in capsys.readouterr().out


def test_export_command_routes_loaded_events(tmp_path, capsys):
    output = tmp_path / "events.jsonl"
    with patch(
        "bargaining_platform.cli.load_run_from_sqlite",
        return_value={"events": []},
    ) as load_run:
        with patch("bargaining_platform.cli.export_run_to_jsonl") as export:
            assert main([
                "export",
                "--run-id",
                "run-1",
                "--output",
                str(output),
                "--db-path",
                str(tmp_path / "runs.db"),
            ]) == 0

    load_run.assert_called_once_with("run-1", str(tmp_path / "runs.db"))
    export.assert_called_once_with("run-1", tuple(), output)
    assert "Exported 0 events" in capsys.readouterr().out


def test_run_cli_routes_role_model_aliases(tmp_path, monkeypatch, capsys):
    engine = SimpleNamespace(state=SimpleNamespace(outcome=SimpleNamespace(status="AGREEMENT")), run_id="run-1")
    captured = {}
    monkeypatch.setattr(cli, "load_scenario", lambda _: {"scenario_id": "shop", "catalog": []})
    monkeypatch.setattr(cli, "resolve_models", lambda seller, buyer: (captured.setdefault("aliases", (seller, buyer)) or {}, "registry-v1"))
    monkeypatch.setattr(cli, "create_role_adapters", lambda models, version: ({"seller": object(), "buyer": object()}, {"roles": {}}))

    def run(*args, **kwargs):
        captured["run_kwargs"] = kwargs
        return engine

    monkeypatch.setattr(cli, "run_llm_negotiation", run)
    assert main([
        "run", "--scenario", "unused.json",
        "--seller-model", "gpt-test", "--buyer-model", "gemini-test",
        "--db-path", str(tmp_path / "runs.db"),
    ]) == 0

    assert captured["aliases"] == ("gpt-test", "gemini-test")
    assert captured["run_kwargs"]["model_config"] == {"roles": {}}
    assert "Completed negotiation" in capsys.readouterr().out


def test_check_scenario_command_accepts_valid_and_rejects_missing_fields(tmp_path, capsys):
    import json

    valid_path = tmp_path / "scenario.json"
    valid_path.write_text(Path("scenarios/shop_bundle.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["check-scenario", "--scenario", str(valid_path)]) == 0
    assert "Scenario is valid" in capsys.readouterr().out

    invalid_data = json.loads(valid_path.read_text(encoding="utf-8"))
    del invalid_data["private_profiles"]["buyer"]["budget"]
    valid_path.write_text(json.dumps(invalid_data), encoding="utf-8")
    assert main(["check-scenario", "--scenario", str(valid_path)]) == 1
    assert "Missing required key: private_profiles.buyer.budget" in capsys.readouterr().out

    valid_path.write_text("{invalid json", encoding="utf-8")
    assert main(["check-scenario", "--scenario", str(valid_path)]) == 1
    assert "Invalid scenario JSON" in capsys.readouterr().out
