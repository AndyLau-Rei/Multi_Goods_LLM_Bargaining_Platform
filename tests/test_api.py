import json
from unittest.mock import patch

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from bargaining_platform import api as api_module
from bargaining_platform.api import _public_action, app
from bargaining_platform.model_adapter import MockLLMAdapter
from bargaining_platform.runner import run_llm_negotiation
from bargaining_platform.scenario import load_scenario


def test_start_run_and_get_status(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAINING_DB_PATH", str(tmp_path / "runs.db"))
    scheduled = []

    def capture_task(_background_tasks, function, *args, **kwargs):
        scheduled.append((function, args, kwargs))

    client = TestClient(app)
    with patch.object(BackgroundTasks, "add_task", capture_task):
        response = client.post(
            "/runs",
            json={"scenario_id": "scenarios/shop_bundle.json", "agent_type": "mock"},
        )
    assert response.status_code == 202
    run_id = response.json()["run_id"]

    status = client.get(f"/runs/{run_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "IN_PROGRESS"

    function, args, kwargs = scheduled.pop()
    function(*args, **kwargs)
    status = client.get(f"/runs/{run_id}/status")
    assert status.status_code == 200
    assert status.json()["outcome"]["status"] == "AGREEMENT"
    serialized = json.dumps(status.json(), sort_keys=True)
    assert "utility_metrics" not in serialized
    assert "buyer_utility" not in serialized
    assert "seller_utility" not in serialized
    assert "joint_utility" not in serialized
    assert "gft" not in serialized
    assert "npb" not in serialized


def test_background_failure_marks_run_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAINING_DB_PATH", str(tmp_path / "runs.db"))

    def fail_run(*args, **kwargs):
        raise RuntimeError("generation failed")

    monkeypatch.setattr(api_module, "run_llm_negotiation", fail_run)
    scheduled = []

    def capture_task(_background_tasks, function, *args, **kwargs):
        scheduled.append((function, args, kwargs))

    client = TestClient(app)
    with patch.object(BackgroundTasks, "add_task", capture_task):
        response = client.post(
            "/runs",
            json={"scenario_id": "scenarios/shop_bundle.json", "agent_type": "mock"},
        )
    run_id = response.json()["run_id"]
    function, args, kwargs = scheduled.pop()
    function(*args, **kwargs)

    status = client.get(f"/runs/{run_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "ERROR"


def test_in_progress_status_and_events(monkeypatch):
    public_event = {
        "event_id": 2,
        "event_type": "STATE_TRANSITION",
        "actor": "seller",
        "state_transition_summary": "Proposal updated",
    }
    monkeypatch.setattr(api_module, "_active_runs", {
        "live": {"status": "IN_PROGRESS", "round_number": 2, "events": [public_event]}
    })
    client = TestClient(app)

    status = client.get("/runs/live/status")
    events = client.get("/runs/live/events")
    assert status.status_code == 200
    assert status.json()["status"] == "IN_PROGRESS"
    assert status.json()["round"] == 2
    assert events.json() == {
        "run_id": "live", "round_number": 2, "events": [public_event],
    }
    assert "private_decision_summary" not in json.dumps(events.json())


def test_view_privacy_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAINING_DB_PATH", str(tmp_path / "runs.db"))
    monkeypatch.setenv("BUYER_API_SECRET", "buyer-secret")
    monkeypatch.setenv("SELLER_API_SECRET", "seller-secret")
    client = TestClient(app)
    run_id = client.post(
        "/runs",
        json={"scenario_id": "scenarios/shop_bundle.json", "agent_type": "mock"},
    ).json()["run_id"]

    response = client.get(
        f"/runs/{run_id}/view",
        headers={"Authorization": "Bearer buyer-secret"},
    )
    assert response.status_code == 200
    serialized = json.dumps(response.json(), sort_keys=True)
    assert "unit_cost" not in serialized
    assert "minimum_unit_price" not in serialized
    assert '"70"' not in serialized
    assert '"80"' not in serialized

    assert client.get(f"/runs/{run_id}/view").status_code == 403
    assert client.get(
        f"/runs/{run_id}/view",
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 403
    assert client.get(
        f"/runs/{run_id}/view",
        params={"role": "seller"},
        headers={"Authorization": "Bearer buyer-secret"},
    ).json()["role"] == "buyer"
    assert client.get(
        f"/runs/{run_id}/view",
        headers={"Authorization": "Bearer seller-secret"},
    ).json()["role"] == "seller"

    monkeypatch.setenv("SELLER_API_SECRET", "buyer-secret")
    assert client.get(
        f"/runs/{run_id}/view",
        headers={"Authorization": "Bearer buyer-secret"},
    ).status_code == 403


def test_viewer_page_and_static_mount():
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert "Negotiation Viewer" in page.text
    static = client.get("/static/app.js")
    assert static.status_code == 200
    assert "renderEvents" in static.text


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
        "seller": MockLLMAdapter([
            json.dumps({
                "action_type": "PROPOSE", "proposal_id": "discounted",
                "proposal": proposal,
            })
        ]),
        "buyer": MockLLMAdapter([
            json.dumps({"action_type": "ACCEPT", "proposal_id": "discounted"})
        ]),
    }
    run_llm_negotiation(
        scenario, actions, run_id="discounted",
        db_path=tmp_path / "runs.db", mock_demo=True,
    )

    response = TestClient(app).get("/runs/discounted/events")
    assert response.status_code == 200
    proposal = response.json()["events"][0]["public_transcript"]["proposal"]
    assert proposal["terms"][0]["unit_price"] == 120
    assert proposal["terms"][0]["effective_unit_price"] == 100
    assert proposal["terms"][0]["effective_total_price"] == 100
    assert proposal["effective_total_price"] == 160
    assert proposal["conditions"][0]["discount_per_unit"] == 20
    assert response.json()["round_number"] == 1
    serialized = json.dumps(response.json(), sort_keys=True)
    assert "validation_result" not in serialized
    assert "raw_output" not in serialized
    assert "metadata" not in serialized
    assert "private_decision_summary" not in serialized


def test_api_effective_totals_preserve_large_integers():
    price = 10**40 + 1
    action = {
        "proposal": {
            "terms": [{"item_id": "A", "quantity": 2, "unit_price": price}],
            "effective_prices": {"A": price},
        }
    }
    proposal = _public_action(action)["proposal"]
    assert proposal["terms"][0]["unit_price"] == price
    assert proposal["terms"][0]["effective_total_price"] == 2 * price
    assert proposal["effective_total_price"] == 2 * price


def test_api_round_display_with_retries(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAINING_DB_PATH", str(tmp_path / "runs.db"))
    scenario = load_scenario("scenarios/shop_bundle.json")
    proposal = {
        "proposal_id": "retry-round",
        "acceptance_mode": "all_or_nothing",
        "terms": [
            {
                "type": "purchase", "item_id": "A", "quantity": 1,
                "unit_price": 120, "unit": "piece",
            }
        ],
        "conditions": [], "decision_summary": "",
        "original_prices": {"A": 120}, "effective_prices": {"A": 120},
    }
    adapters = {
        "seller": MockLLMAdapter([
            json.dumps({
                "action_type": "PROPOSE", "proposal_id": "retry-round",
                "proposal": proposal,
            }),
            "bad", "still bad",
        ]),
        "buyer": MockLLMAdapter([
            json.dumps({
                "action_type": "COUNTER", "proposal_id": "retry-round",
                "proposal": proposal,
            })
        ]),
    }
    run_llm_negotiation(
        scenario, adapters, run_id="retry-round",
        db_path=tmp_path / "runs.db", mock_demo=True,
    )
    response = TestClient(app).get("/runs/retry-round/events")
    assert response.status_code == 200
    assert response.json()["round_number"] == 2


def test_api_empty_action_shows_transitions(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAINING_DB_PATH", str(tmp_path / "runs.db"))
    scenario = load_scenario("scenarios/shop_bundle.json")
    adapters = {"seller": MockLLMAdapter(["bad", "still bad"]), "buyer": MockLLMAdapter([])}
    run_llm_negotiation(
        scenario, adapters, run_id="empty-actions",
        db_path=tmp_path / "runs.db", mock_demo=True,
    )
    response = TestClient(app).get("/runs/empty-actions/events")
    assert response.status_code == 200
    assert not any(event["event_type"] == "ACTION_ATTEMPT" for event in response.json()["events"])
    assert any(
        event["event_type"] == "STATE_TRANSITION"
        and event["state_transition_summary"] == "Game ended: invalid action retry exhausted"
        for event in response.json()["events"]
    )
