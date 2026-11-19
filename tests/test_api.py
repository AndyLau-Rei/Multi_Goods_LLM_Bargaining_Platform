import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from bargaining_platform import api as api_module
from bargaining_platform.api import _public_action, app
from bargaining_platform.model_adapter import MockLLMAdapter
from bargaining_platform.runner import run_llm_negotiation
from bargaining_platform.scenario import load_scenario
from bargaining_platform.storage import load_run_from_sqlite


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
        "round_number": 2,
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
    assert 'id="seller-prompt"' in page.text
    assert 'id="buyer-prompt"' in page.text
    static = client.get("/static/app.js")
    assert static.status_code == 200
    assert "renderEvents" in static.text
    assert 'fetch("/prompts")' in static.text


def test_scenarios_endpoint_lists_available_scenarios():
    response = TestClient(app).get("/scenarios")
    assert response.status_code == 200
    scenarios = {scenario["id"]: scenario["name"] for scenario in response.json()}
    assert scenarios["scenarios/single_product.json"] == "single_product"
    assert scenarios["scenarios/shop_bundle.json"] == "shop-v1"


def test_prompt_selection_is_role_scoped_and_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAINING_DB_PATH", str(tmp_path / "runs.db"))
    client = TestClient(app)
    choices = client.get("/prompts")
    assert choices.status_code == 200
    assert choices.json()["defaults"] == {
        "seller": "seller_multi.txt", "buyer": "buyer_multi.txt",
    }
    assert {"seller_multi.txt", "seller_single.txt"} <= set(choices.json()["seller"])
    assert {"buyer_multi.txt", "buyer_single.txt"} <= set(choices.json()["buyer"])
    assert "private_constraints" not in choices.text

    for invalid in ("buyer_single.txt", "../buyer_single.txt", "missing.txt"):
        response = client.post("/runs", json={
            "scenario_id": "scenarios/shop_bundle.json",
            "seller_prompt": invalid,
        })
        assert response.status_code == 400
        assert response.json()["detail"] == "UNKNOWN_PROMPT"
        assert invalid not in response.text

    response = client.post("/runs", json={
        "scenario_id": "scenarios/shop_bundle.json",
        "seller_prompt": "seller_single.txt",
        "buyer_prompt": "buyer_multi.txt",
    })
    assert response.status_code == 202
    saved = load_run_from_sqlite(response.json()["run_id"], tmp_path / "runs.db")
    for role, name in (("seller", "seller_single.txt"), ("buyer", "buyer_multi.txt")):
        events = [event for event in saved["events"] if event["actor"] == role
                  and event["event_type"] == "ACTION_ATTEMPT"]
        assert events
        assert all(event["metadata"]["prompt_template_id"] == name for event in events)
        assert saved["model_config"]["roles"][role]["prompt_version"] == hashlib.sha256(
            (Path("prompts") / name).read_bytes()
        ).hexdigest()


def test_invalid_prompt_is_rejected_before_scheduling(tmp_path, monkeypatch):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    for role in ("seller", "buyer"):
        (prompt_dir / f"{role}_multi.txt").write_text("{objective}", encoding="utf-8")
    bad_prompt = prompt_dir / "seller_bad.txt"
    monkeypatch.setattr(
        api_module, "default_prompt_path", lambda role: prompt_dir / f"{role}_multi.txt"
    )
    client = TestClient(app)
    assert bad_prompt.name not in client.get("/prompts").json()["seller"]

    for template in ("{missing}", '{"action_type": "ACCEPT"}', "{}"):
        bad_prompt.write_text(template, encoding="utf-8")
        assert bad_prompt.name in client.get("/prompts").json()["seller"]
        with patch.object(BackgroundTasks, "add_task") as schedule:
            response = client.post("/runs", json={
                "scenario_id": "scenarios/shop_bundle.json",
                "seller_prompt": bad_prompt.name,
            })
        assert response.status_code == 400
        assert response.json()["detail"] == "INVALID_PROMPT"
        assert template not in response.text
        schedule.assert_not_called()


def test_model_whitelist_endpoint_and_run_selection_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAINING_DB_PATH", str(tmp_path / "runs.db"))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("SELLER_LLM_API_KEY", raising=False)
    monkeypatch.delenv("BUYER_LLM_API_KEY", raising=False)
    client = TestClient(app)

    response = client.get("/models")
    assert response.status_code == 200
    assert {item["provider"] for item in response.json()["models"]} == {
        "openai", "gemini", "deepseek", "qwen", "moonshot", "zhipu",
    }
    assert "base_url" not in json.dumps(response.json())
    assert "api_key" not in json.dumps(response.json()).lower()

    unknown = client.post("/runs", json={
        "scenario_id": "scenarios/shop_bundle.json",
        "agent_type": "llm",
        "seller_model": "unlisted-model",
    })
    assert unknown.status_code == 400
    assert unknown.json()["detail"] == "UNKNOWN_MODEL"
    assert "unlisted-model" not in unknown.text

    with_key = client.post("/runs", json={
        "scenario_id": "scenarios/shop_bundle.json",
        "agent_type": "llm",
        "api_key": "must-not-be-accepted",
    })
    assert with_key.status_code == 422
    assert "must-not-be-accepted" not in with_key.text

    fallback = client.post("/runs", json={
        "scenario_id": "scenarios/shop_bundle.json",
        "agent_type": "llm",
        "seller_model": "gpt-4o-mini",
        "buyer_model": "gemini-2.0-flash",
    })
    assert fallback.status_code == 202
    saved = load_run_from_sqlite(fallback.json()["run_id"], tmp_path / "runs.db")
    assert saved["model_config"]["execution"] == "mock_fallback"
    assert saved["model_config"]["roles"]["seller"]["requested_model"]["public_alias"] == "gpt-4o-mini"
    assert saved["model_config"]["roles"]["buyer"]["requested_model"]["public_alias"] == "gemini-2.0-flash"


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
            "bad", "still bad", json.dumps({"action_type": "WALK_AWAY"}),
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


def test_api_schema_corrections_do_not_emit_false_terminal_transition(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAINING_DB_PATH", str(tmp_path / "runs.db"))
    scenario = load_scenario("scenarios/shop_bundle.json")
    proposal = {
        "acceptance_mode": "all_or_nothing",
        "terms": [{"type": "purchase", "item_id": "A", "quantity": 1, "unit_price": 120, "unit": "piece"}],
        "conditions": [], "decision_summary": "Opening offer",
    }
    adapters = {
        "seller": MockLLMAdapter([
            "bad", "still bad",
            json.dumps({"action_type": "PROPOSE", "proposal": proposal}),
        ]),
        "buyer": MockLLMAdapter([json.dumps({"action_type": "WALK_AWAY"})]),
    }
    run_llm_negotiation(
        scenario, adapters, run_id="empty-actions",
        db_path=tmp_path / "runs.db", mock_demo=True,
    )
    response = TestClient(app).get("/runs/empty-actions/events")
    assert response.status_code == 200
    public_actions = [
        event for event in response.json()["events"]
        if event["event_type"] == "ACTION_ATTEMPT"
    ]
    assert len(public_actions) == 2
    assert [event["public_transcript"]["action_type"] for event in public_actions] == [
        "PROPOSE", "WALK_AWAY",
    ]
    attempts = load_run_from_sqlite("empty-actions", tmp_path / "runs.db")["events"]
    invalid_attempts = [
        event for event in attempts
        if event["event_type"] == "ACTION_ATTEMPT"
        and not event["validation_result"]["ok"]
    ]
    assert len(invalid_attempts) == 2
    assert [event["validation_result"]["error_code"] for event in invalid_attempts] == [
        "MALFORMED_JSON", "MALFORMED_JSON",
    ]
    assert all(
        event.get("state_transition_summary") != "Game ended: invalid action retry exhausted"
        for event in response.json()["events"]
    )
