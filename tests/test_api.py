import json

from fastapi.testclient import TestClient

from bargaining_platform.api import app


def test_start_run_and_get_status(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAINING_DB_PATH", str(tmp_path / "runs.db"))
    response = TestClient(app).post(
        "/runs",
        json={"scenario_id": "scenarios/shop_bundle.json", "agent_type": "mock"},
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]

    status = TestClient(app).get(f"/runs/{run_id}/status")
    assert status.status_code == 200
    assert status.json()["outcome"]["status"] == "AGREEMENT"


def test_view_privacy_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAINING_DB_PATH", str(tmp_path / "runs.db"))
    client = TestClient(app)
    run_id = client.post(
        "/runs",
        json={"scenario_id": "scenarios/shop_bundle.json", "agent_type": "mock"},
    ).json()["run_id"]

    response = client.get(f"/runs/{run_id}/view", params={"role": "buyer"})
    assert response.status_code == 200
    serialized = json.dumps(response.json(), sort_keys=True)
    assert "unit_cost" not in serialized
    assert "minimum_unit_price" not in serialized
    assert '"70"' not in serialized
    assert '"80"' not in serialized
