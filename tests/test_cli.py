import json
from pathlib import Path
from types import SimpleNamespace

from bargaining_platform import cli
from bargaining_platform.cli import main
from bargaining_platform.model_adapter import MockLLMAdapter
from bargaining_platform.runner import run_llm_negotiation
from bargaining_platform.scenario import load_scenario


def test_replay_and_export_commands_round_trip_saved_run(tmp_path, capsys):
    scenario = load_scenario("scenarios/shop_bundle.json")
    scenario["private_profiles"]["seller"]["reservation_utility"] = 0
    scenario["private_profiles"]["buyer"]["reservation_utility"] = 0
    proposal = {
        "proposal_id": "cli-round-trip",
        "acceptance_mode": "all_or_nothing",
        "terms": [{"type": "purchase", "item_id": "A", "quantity": 1, "unit_price": 120, "unit": "piece"}],
        "conditions": [],
        "decision_summary": "",
        "original_prices": {"A": 120},
        "effective_prices": {"A": 120},
    }
    database = tmp_path / "runs.db"
    run_llm_negotiation(
        scenario,
        {
            "seller": MockLLMAdapter([json.dumps({
                "action_type": "PROPOSE", "proposal_id": "cli-round-trip", "proposal": proposal,
            })]),
            "buyer": MockLLMAdapter([json.dumps({
                "action_type": "ACCEPT", "proposal_id": "cli-round-trip",
            })]),
        },
        run_id="cli-round-trip",
        db_path=database,
    )

    assert main(["replay", "--run-id", "cli-round-trip", "--db-path", str(database)]) == 0
    assert "Replay successful: cli-round-trip (AGREEMENT)" in capsys.readouterr().out

    output = tmp_path / "events.jsonl"
    assert main([
        "export", "--run-id", "cli-round-trip", "--output", str(output),
        "--db-path", str(database),
    ]) == 0
    assert "Exported 4 events" in capsys.readouterr().out
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["event_type"] for row in rows] == [
        "ACTION_ATTEMPT", "STATE_TRANSITION", "ACTION_ATTEMPT", "STATE_TRANSITION",
    ]


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
