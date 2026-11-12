import json
import sqlite3

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
