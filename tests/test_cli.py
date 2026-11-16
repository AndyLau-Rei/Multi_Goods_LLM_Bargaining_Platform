from types import SimpleNamespace
from unittest.mock import patch

from bargaining_platform.cli import main


def test_replay_command_routes_to_storage(tmp_path, capsys):
    state = SimpleNamespace(outcome=SimpleNamespace(status="AGREEMENT"))
    with patch("bargaining_platform.cli.replay_run", return_value=state) as replay:
        assert main(["replay", "--run-id", "run-1", "--db-path", str(tmp_path / "runs.db")]) == 0

    replay.assert_called_once_with("run-1", str(tmp_path / "runs.db"))
    assert "Replay successful" in capsys.readouterr().out


def test_export_command_routes_loaded_events(tmp_path, capsys):
    output = tmp_path / "events.jsonl"
    with patch("bargaining_platform.cli.load_run_from_sqlite", return_value={"events": []}) as load_run:
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
