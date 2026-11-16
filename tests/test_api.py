import json

from fastapi.testclient import TestClient

from bargaining_platform.api import app
from bargaining_platform.model_adapter import MockLLMAdapter
from bargaining_platform.runner import run_llm_negotiation
from bargaining_platform.scenario import load_scenario


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


def test_viewer_page_and_static_mount():
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert "Negotiation Viewer" in page.text
    static = client.get("/static/style.css")
    assert static.status_code == 200
    assert "transcript" in static.text


def test_events_show_authoritative_effective_bundle_price(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAINING_DB_PATH", str(tmp_path / "runs.db"))
    scenario = load_scenario("scenarios/shop_bundle.json")
    proposal = {
        "proposal_id": "discounted",
        "acceptance_mode": "all_or_nothing",
        "terms": [
            {"type": "purchase", "item_id": "A", "quantity": 1, "unit_price": 120, "unit": "piece"},
            {"type": "purchase", "item_id": "B", "quantity": 1, "unit_price": 60, "unit": "piece"},
        ],
        "conditions": [{
            "type": "discount_if_item_in_bundle",
            "discounted_item_id": "A",
            "discount_per_unit": 20,
            "required_item_id": "B",
            "minimum_required_quantity": 1,
        }],
        "decision_summary": "",
        "original_prices": {"A": 120, "B": 60},
        "effective_prices": {"A": 120, "B": 60},
    }
    actions = {
        "seller": MockLLMAdapter([json.dumps({"action_type": "PROPOSE", "proposal_id": "discounted", "proposal": proposal})]),
        "buyer": MockLLMAdapter([json.dumps({"action_type": "ACCEPT", "proposal_id": "discounted"})]),
    }
    run_llm_negotiation(scenario, actions, run_id="discounted", db_path=tmp_path / "runs.db", mock_demo=True)

    response = TestClient(app).get("/runs/discounted/events")
    assert response.status_code == 200
    serialized = json.dumps(response.json(), sort_keys=True)
    assert "120" not in serialized
    assert "100" in serialized
    assert "validation_result" not in serialized
    assert "raw_output" not in serialized
    assert "metadata" not in serialized
    assert "private_decision_summary" not in serialized
