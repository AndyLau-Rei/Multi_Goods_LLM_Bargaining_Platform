import json

from bargaining_platform.model_adapter import MockLLMAdapter
from bargaining_platform.runner import run_llm_negotiation
from bargaining_platform.scenario import load_scenario
from bargaining_platform.storage import load_run_from_sqlite


def _scenario():
    scenario = load_scenario("scenarios/shop_bundle.json")
    scenario["private_profiles"]["seller"]["reservation_utility"] = 0
    scenario["private_profiles"]["buyer"]["reservation_utility"] = 0
    return scenario


def _proposal():
    return {
        "proposal_id": "e2e-1",
        "acceptance_mode": "all_or_nothing",
        "terms": [
            {"type": "purchase", "item_id": "A", "quantity": 1, "unit_price": 120, "unit": "piece"},
            {"type": "purchase", "item_id": "B", "quantity": 1, "unit_price": 60, "unit": "piece"},
        ],
        "conditions": [],
        "decision_summary": "",
        "original_prices": {"A": 120, "B": 60},
        "effective_prices": {"A": 120, "B": 60},
    }


def test_e2e_mock_llm_run(tmp_path):
    proposal = _proposal()
    adapters = {
        "seller": MockLLMAdapter([json.dumps({"action_type": "PROPOSE", "proposal_id": "e2e-1", "proposal": proposal})]),
        "buyer": MockLLMAdapter([json.dumps({"action_type": "ACCEPT", "proposal_id": "e2e-1"})]),
    }

    engine = run_llm_negotiation(_scenario(), adapters, run_id="e2e-1", db_path=tmp_path / "runs.db")

    assert engine.state.outcome.status == "AGREEMENT"
    saved = load_run_from_sqlite("e2e-1", tmp_path / "runs.db")
    assert saved["outcome_status"] == "AGREEMENT"


def test_e2e_llm_retry_on_bad_json(tmp_path):
    proposal = _proposal()
    proposal["proposal_id"] = "e2e-2"
    adapters = {
        "seller": MockLLMAdapter([
            '{"bad_json": true}',
            json.dumps({"action_type": "PROPOSE", "proposal_id": "e2e-2", "proposal": proposal}),
        ]),
        "buyer": MockLLMAdapter([json.dumps({"action_type": "ACCEPT", "proposal_id": "e2e-2"})]),
    }

    engine = run_llm_negotiation(_scenario(), adapters, run_id="e2e-2", db_path=tmp_path / "runs.db")

    assert engine.state.outcome.status == "AGREEMENT"
    assert engine.state.invalid_attempts == 0
    assert engine.event_logger.events[0].validation_result["error_code"] == "SCHEMA_VIOLATION"


def test_llm_metadata_logging(tmp_path):
    proposal = _proposal()
    proposal["proposal_id"] = "e2e-3"
    adapters = {
        "seller": MockLLMAdapter([
            '{"bad_json": true}',
            json.dumps({"action_type": "PROPOSE", "proposal_id": "e2e-3", "proposal": proposal}),
        ]),
        "buyer": MockLLMAdapter([json.dumps({"action_type": "ACCEPT", "proposal_id": "e2e-3"})]),
    }

    engine = run_llm_negotiation(_scenario(), adapters, run_id="e2e-3", db_path=tmp_path / "runs.db")

    action_events = [event for event in engine.event_logger.events if event.event_type == "ACTION_ATTEMPT"]
    assert len(action_events) == 3
    for event in action_events:
        assert isinstance(event.raw_output, str) and event.raw_output
        assert event.metadata["model_version"] == "mock"
        assert event.metadata["prompt_tokens"] >= 1
        assert event.metadata["completion_tokens"] >= 1
        assert event.metadata["latency_ms"] >= 0
        assert "PRIVATE CONSTRAINTS" in event.metadata["prompt"]
    saved = load_run_from_sqlite("e2e-3", tmp_path / "runs.db")
    assert saved["events"][0]["metadata"]["model_version"] == "mock"
