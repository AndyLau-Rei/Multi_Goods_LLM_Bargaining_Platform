import json
import hashlib
import sqlite3

from bargaining_platform import runner
from bargaining_platform.model_adapter import GenerationResult, MockLLMAdapter
from bargaining_platform.runner import run_llm_negotiation
from bargaining_platform.scenario import load_scenario
from bargaining_platform.storage import load_run_from_sqlite, replay_run


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
        "seller": MockLLMAdapter([
            json.dumps({
                "action_type": "PROPOSE", "proposal_id": "e2e-1",
                "proposal": proposal,
            })
        ]),
        "buyer": MockLLMAdapter([json.dumps({"action_type": "ACCEPT", "proposal_id": "e2e-1"})]),
    }

    engine = run_llm_negotiation(
        _scenario(), adapters, run_id="e2e-1", db_path=tmp_path / "runs.db"
    )

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

    engine = run_llm_negotiation(
        _scenario(), adapters, run_id="e2e-2", db_path=tmp_path / "runs.db"
    )

    assert engine.state.outcome.status == "AGREEMENT"
    assert engine.state.invalid_attempts == 0
    assert engine.event_logger.events[0].validation_result["error_code"] == "SCHEMA_VIOLATION"


def test_invalid_outputs_are_private_until_a_valid_retry(tmp_path):
    invalid_proposal = _proposal()
    invalid_proposal["decision_summary"] = "retry-private-summary"
    invalid_proposal["terms"][0]["type"] = "service"
    valid_proposal = _proposal()
    valid_proposal["decision_summary"] = "valid-private-summary"

    class CapturingAdapter(MockLLMAdapter):
        def __init__(self, responses):
            super().__init__(responses)
            self.prompts = []

        def generate(self, prompt, action_schema):
            self.prompts.append([dict(message) for message in prompt])
            return super().generate(prompt, action_schema)

    invalid_raw = '{"action_type":"PROPOSE",'
    seller = CapturingAdapter([
        invalid_raw,
        json.dumps({"action_type": "PROPOSE", "proposal": invalid_proposal}),
        json.dumps({"action_type": "PROPOSE", "proposal": valid_proposal}),
    ])
    buyer = CapturingAdapter([json.dumps({"action_type": "ACCEPT"})])

    engine = run_llm_negotiation(
        _scenario(),
        {"seller": seller, "buyer": buyer},
        run_id="private-invalid-retries",
        db_path=tmp_path / "runs.db",
    )

    assert engine.state.outcome.status == "AGREEMENT"
    assert [event.validation_result["error_code"] for event in engine.event_logger.events[:2]] == [
        "MALFORMED_JSON", "INVALID_TERM_TYPE",
    ]
    assert engine.event_logger.events[0].raw_output == invalid_raw
    assert engine.event_logger.events[0].validation_result == {
        "ok": False,
        "error_code": "MALFORMED_JSON",
        "message": "Model output is not valid JSON",
    }
    assert engine.event_logger.events[1].private_decision_summary == "retry-private-summary"
    assert engine.event_logger.events[2].private_decision_summary == "valid-private-summary"
    correction = seller.prompts[1][-1]["content"]
    assert correction == (
        "Your previous action was rejected.\n"
        "Error code: MALFORMED_JSON\n"
        "Error message: Model output is not valid JSON\n"
        "Correct the invalid fields and output exactly one valid JSON action."
    )
    assert "Error code: INVALID_TERM_TYPE" in seller.prompts[2][-1]["content"]
    buyer_history = json.dumps(buyer.prompts)
    assert invalid_raw not in buyer_history
    assert "MALFORMED_JSON" not in buyer_history
    assert "INVALID_TERM_TYPE" not in buyer_history
    assert "retry-private-summary" not in buyer_history
    assert "valid-private-summary" not in buyer_history
    public = json.dumps(engine.state.public_transcript)
    assert "service" not in public
    assert "retry-private-summary" not in public
    assert "valid-private-summary" not in public
    assert len(engine.state.public_transcript) == 2
    assert engine.state.public_transcript[0]["action_type"] == "PROPOSE"


def test_invalid_output_retry_exhaustion_ends_invalid_action(tmp_path):
    seller = MockLLMAdapter(["bad one", "bad two", "bad three"])
    buyer = MockLLMAdapter([])

    engine = run_llm_negotiation(
        _scenario(),
        {"seller": seller, "buyer": buyer},
        run_id="invalid-retry-exhaustion",
        db_path=tmp_path / "runs.db",
    )

    assert engine.state.outcome.status == "INVALID_ACTION"
    assert engine.state.public_transcript == []
    assert [event.raw_output for event in engine.event_logger.events if event.event_type == "ACTION_ATTEMPT"] == [
        "bad one", "bad two", "bad three",
    ]
    assert engine.event_logger.events[-1].state_transition_summary == (
        "Game ended: invalid action retry exhausted"
    )
    assert replay_run("invalid-retry-exhaustion", tmp_path / "runs.db").outcome.status == "INVALID_ACTION"


def test_retry_counter_resets_after_valid_action_and_replays_later_exhaustion(tmp_path):
    proposal = _proposal()
    seller = MockLLMAdapter([
        json.dumps({"action_type": "PROPOSE", "proposal": proposal}),
        "first invalid",
        "second invalid",
        "third invalid",
    ])
    buyer = MockLLMAdapter([
        '{"action_type":"COUNTER",',
        json.dumps({"action_type": "COUNTER", "proposal": proposal}),
    ])

    engine = run_llm_negotiation(
        _scenario(),
        {"seller": seller, "buyer": buyer},
        run_id="reset-retry-exhaustion-round-2",
        db_path=tmp_path / "runs.db",
    )

    assert engine.state.outcome.status == "INVALID_ACTION"
    assert engine.state.invalid_attempts == 3
    assert [
        event.raw_output for event in engine.event_logger.events
        if event.event_type == "ACTION_ATTEMPT"
        and event.actor == "seller"
        and not event.validation_result["ok"]
    ] == ["first invalid", "second invalid", "third invalid"]
    transition = engine.event_logger.events[-1]
    assert transition.state_transition_summary == "Game ended: invalid action retry exhausted"
    assert transition.round_number == 2
    replayed = replay_run("reset-retry-exhaustion-round-2", tmp_path / "runs.db")
    assert replayed.outcome.status == "INVALID_ACTION"


def test_id_free_offer_below_private_floor_fails_on_acceptance(tmp_path):
    proposal = _proposal()
    proposal["terms"][0]["unit_price"] = 79
    proposal["terms"][1]["unit_price"] = 60
    proposal.pop("proposal_id")
    proposal.pop("original_prices")
    proposal.pop("effective_prices")
    revised = json.loads(json.dumps(proposal))
    revised["terms"][0]["unit_price"] = 100

    adapters = {
        "seller": MockLLMAdapter([
            json.dumps({"action_type": "PROPOSE", "proposal": proposal}),
            json.dumps({"action_type": "COUNTER", "proposal": revised}),
        ]),
        "buyer": MockLLMAdapter([
            json.dumps({"action_type": "ACCEPT"}),
            json.dumps({"action_type": "ACCEPT"}),
        ]),
    }

    engine = run_llm_negotiation(
        _scenario(), adapters, run_id="id-free-revision", db_path=tmp_path / "runs.db"
    )

    assert engine.state.outcome.status == "NO_DEAL"
    assert engine.state.outcome.final_proposal_id is None
    assert any(
        event.validation_result.get("error_code") == "PRICE_BELOW_FLOOR"
        for event in engine.event_logger.events
        if event.event_type == "ACTION_ATTEMPT"
    )
    assert replay_run("id-free-revision", tmp_path / "runs.db").outcome.status == "NO_DEAL"


def test_llm_metadata_logging(tmp_path):
    proposal = _proposal()
    proposal["proposal_id"] = "e2e-3"
    scenario = _scenario()
    scenario["private_profiles"]["seller"]["private_notes"] = "seller-secret-note"
    scenario["private_profiles"]["buyer"]["private_notes"] = "buyer-secret-note"

    class CapturingAdapter(MockLLMAdapter):
        def __init__(self, responses):
            super().__init__(responses)
            self.prompts = []

        def generate(self, prompt, action_schema):
            self.prompts.append([dict(message) for message in prompt])
            result = super().generate(prompt, action_schema)
            return GenerationResult(result.text, {
                **result.metadata, "feedback": "provider-private-feedback",
                "api_key": "provider-secret-key",
            })

    adapters = {
        "seller": CapturingAdapter([
            '{"bad_json": true}',
            json.dumps({"action_type": "PROPOSE", "proposal_id": "e2e-3", "proposal": proposal}),
        ]),
        "buyer": CapturingAdapter([json.dumps({"action_type": "ACCEPT", "proposal_id": "e2e-3"})]),
    }

    engine = run_llm_negotiation(
        scenario, adapters, run_id="e2e-3", db_path=tmp_path / "runs.db"
    )

    action_events = [
        event for event in engine.event_logger.events
        if event.event_type == "ACTION_ATTEMPT"
    ]
    assert len(action_events) == 3
    role_prompts = {role: iter(adapter.prompts) for role, adapter in adapters.items()}
    for event in action_events:
        prompt = "\n\n".join(
            f"{message['role']}: {message['content']}"
            for message in next(role_prompts[event.actor])
        )
        assert isinstance(event.raw_output, str) and event.raw_output
        assert event.metadata["model_version"] == "mock"
        assert event.metadata["provider"] == "mock"
        assert event.metadata["prompt_template_id"] == f"{event.actor}_multi.txt"
        assert event.metadata["prompt_version"]
        assert event.metadata["prompt_hash"] == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        assert event.metadata["prompt_length"] == len(prompt)
        assert event.metadata["prompt_tokens"] >= 1
        assert event.metadata["completion_tokens"] >= 1
        assert event.metadata["latency_ms"] >= 0
        assert len(json.dumps(event.metadata)) < 1000
        assert not {"prompt", "feedback", "api_key"} & event.metadata.keys()
    saved = load_run_from_sqlite("e2e-3", tmp_path / "runs.db")
    assert saved["events"][0]["metadata"]["model_version"] == "mock"
    prompt_rows = saved["prompts"]
    assert len(prompt_rows) == len(action_events)
    assert [row["event_id"] for row in prompt_rows] == [event.event_id for event in action_events]
    assert [row["role"] for row in prompt_rows] == [event.actor for event in action_events]
    assert prompt_rows[0]["prompt"] == adapters["seller"].prompts[0]
    assert prompt_rows[0]["action_schema"]["required"] == ["action_type"]
    assert prompt_rows[0]["output"] == action_events[0].raw_output
    assert "seller-secret-note" in json.dumps(prompt_rows[0]["prompt"])
    assert "buyer-secret-note" not in json.dumps(prompt_rows[0]["prompt"])
    seller_prompt_rows = [row for row in prompt_rows if row["role"] == "seller"]
    assert len(seller_prompt_rows) == 2
    assert seller_prompt_rows[1]["prompt"]
    assert all(message["role"] != "system" for message in seller_prompt_rows[1]["prompt"])
    assert "seller-secret-note" not in json.dumps(seller_prompt_rows[1]["prompt"])
    with sqlite3.connect(tmp_path / "runs.db") as connection:
        prompt_columns = {row[1] for row in connection.execute("PRAGMA table_info(prompts)")}
    assert prompt_columns == {"run_id", "event_id", "role", "prompt", "action_schema", "output"}
    with sqlite3.connect(tmp_path / "runs.db") as connection:
        metadata_rows = connection.execute(
            "SELECT metadata_json FROM events WHERE metadata_json IS NOT NULL"
        ).fetchall()
    assert all(len(row[0]) < 1000 for row in metadata_rows)
    stored_metadata = json.dumps(metadata_rows)
    for secret in ("seller-secret-note", "buyer-secret-note", "provider-secret-key", "provider-private-feedback"):
        assert secret not in stored_metadata
    assert replay_run("e2e-3", tmp_path / "runs.db").outcome.status == "AGREEMENT"


def test_progress_callback_publishes_only_committed_public_events(tmp_path):
    proposal = _proposal()
    proposal["decision_summary"] = "seller-only note"
    adapters = {
        "seller": MockLLMAdapter([
            '{"bad_json": true}',
            json.dumps({"action_type": "PROPOSE", "proposal_id": "e2e-1", "proposal": proposal}),
        ]),
        "buyer": MockLLMAdapter([json.dumps({"action_type": "ACCEPT", "proposal_id": "e2e-1"})]),
    }
    progress = []

    run_llm_negotiation(
        _scenario(), adapters, run_id="progress", db_path=tmp_path / "runs.db",
        progress_callback=progress.append,
    )

    assert len(progress) == 3
    assert progress[0]["events"] == []
    assert [event["event_id"] for update in progress for event in update["events"]] == [2, 3, 4, 5]
    assert progress[-1]["events"][-1]["state_transition_summary"] == "Proposal accepted; game ended"
    assert progress[-1]["round_number"] == 1
    serialized = json.dumps(progress)
    for private_field in ("raw_output", "metadata", "validation_result", "private_decision_summary", "seller-only note"):
        assert private_field not in serialized


def test_runner_uses_separate_role_agents_and_prompt_contexts(tmp_path, monkeypatch):
    scenario = _scenario()
    scenario["private_profiles"]["seller"]["unit_cost"]["A"] = 71
    scenario["private_profiles"]["buyer"]["unit_value"]["A"] = 131
    proposal = _proposal()
    proposal["proposal_id"] = "isolated"
    seller_template = tmp_path / "seller.txt"
    buyer_template = tmp_path / "buyer.txt"
    seller_template.write_text("SELLER VIEW {private_constraints}", encoding="utf-8")
    buyer_template.write_text("BUYER VIEW {private_constraints}", encoding="utf-8")
    adapters = {
        "seller": MockLLMAdapter([json.dumps({
            "action_type": "PROPOSE", "proposal_id": "isolated", "proposal": proposal,
        })]),
        "buyer": MockLLMAdapter([json.dumps({"action_type": "ACCEPT", "proposal_id": "isolated"})]),
    }
    created_agents = []
    original_init = runner.LLMAgent.__init__

    def record_agent(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        created_agents.append(self)

    monkeypatch.setattr(runner.LLMAgent, "__init__", record_agent)
    engine = run_llm_negotiation(
        scenario,
        adapters,
        run_id="isolated-agents",
        db_path=tmp_path / "runs.db",
        seller_prompt_path=seller_template,
        buyer_prompt_path=buyer_template,
    )

    assert len(created_agents) == 2
    seller, buyer = created_agents
    assert seller is not buyer
    assert seller.role == "seller" and buyer.role == "buyer"
    assert seller.adapter is adapters["seller"]
    assert buyer.adapter is adapters["buyer"]
    assert seller.adapter is not buyer.adapter
    seller_prompt = seller.messages[0]["content"]
    buyer_prompt = buyer.messages[0]["content"]
    assert "SELLER VIEW" in seller_prompt and '"A": 71' in seller_prompt
    assert '"A": 131' not in seller_prompt
    assert "BUYER VIEW" in buyer_prompt and '"A": 131' in buyer_prompt
    assert '"A": 71' not in buyer_prompt
    assert "prompt" not in engine.event_logger.events[0].metadata
    assert engine.state.outcome.status == "AGREEMENT"


def test_role_history_is_incremental_and_decision_summaries_stay_private(tmp_path):
    seller_offer = _proposal()
    seller_offer["decision_summary"] = "seller private strategy"
    buyer_offer = _proposal()
    buyer_offer["decision_summary"] = "buyer private strategy"
    seller_final = _proposal()
    seller_final["decision_summary"] = "seller final private strategy"

    class CapturingAdapter(MockLLMAdapter):
        def __init__(self, responses):
            super().__init__(responses)
            self.prompts = []
            self.histories = []

        def generate(self, prompt, action_schema):
            self.prompts.append([dict(message) for message in prompt])
            self.histories.append(prompt)
            return super().generate(prompt, action_schema)

    seller = CapturingAdapter([
        json.dumps({"action_type": "PROPOSE", "proposal": seller_offer}),
        json.dumps({"action_type": "COUNTER", "proposal": seller_final}),
    ])
    buyer = CapturingAdapter([
        json.dumps({"action_type": "COUNTER", "proposal": buyer_offer}),
        json.dumps({"action_type": "ACCEPT"}),
    ])

    engine = run_llm_negotiation(
        _scenario(),
        {"seller": seller, "buyer": buyer},
        run_id="incremental-context",
        db_path=tmp_path / "runs.db",
    )

    seller_instruction = seller.prompts[0][0]["content"]
    assert seller.prompts[1][0]["content"] == seller_instruction
    assert sum(message["role"] == "system" for message in seller.prompts[0]) == 1
    assert seller.prompts[0][1] == {
        "role": "user",
        "content": "Please make the opening proposal now. Output exactly one valid JSON action.",
    }
    assert "seller private strategy" not in seller.prompts[0][0]["content"]
    assert sum(message["role"] == "system" for message in seller.prompts[1]) == 1
    assert sum(message["role"] == "user" for message in seller.prompts[1]) == 2
    seller_context = json.dumps(seller.prompts[1])
    assert "buyer private strategy" not in seller_context
    assert "seller private strategy" in seller_context
    assert "proposal_id" not in seller.prompts[1][-1]["content"]
    assert "decision_summary" not in seller.prompts[1][-1]["content"]
    buyer_context = json.dumps(buyer.prompts[0])
    assert "seller private strategy" not in buyer_context
    assert "buyer private strategy" not in buyer_context
    assert "proposal_id" not in buyer_context
    assert all(
        "decision_summary" not in message["content"]
        for message in buyer.prompts[0]
        if message["role"] == "user"
    )
    for role, history in (
        ("buyer", buyer.prompts[0]), ("buyer", buyer.prompts[1]),
        ("seller", seller.prompts[0]), ("seller", seller.prompts[1]),
    ):
        assert sum(message["role"] == "system" for message in history) == 1
        assert "proposal_id" not in json.dumps(history)
        opponent_summary = "seller private strategy" if role == "buyer" else "buyer private strategy"
        assert opponent_summary not in json.dumps(history)

    def forwarded_payload(history):
        instruction = next(
            message["content"] for message in reversed(history)
            if message["role"] == "user" and "proposal" in message["content"].lower()
        )
        return json.loads(instruction.split("\n\n", 1)[1])

    buyer_counter_payload = forwarded_payload(buyer.prompts[0])
    assert buyer_counter_payload["action_type"] == "PROPOSE"
    assert buyer_counter_payload["proposal"]["terms"] == seller_offer["terms"]
    assert buyer_counter_payload["proposal"]["conditions"] == seller_offer["conditions"]
    assert buyer_counter_payload["proposal"]["effective_prices"] == {"A": 120, "B": 60}
    assert "decision_summary" not in json.dumps(buyer_counter_payload)

    seller_counter_payload = forwarded_payload(seller.prompts[1])
    buyer_echo_payload = forwarded_payload(buyer.histories[1])
    assert seller_counter_payload == buyer_echo_payload
    assert seller_counter_payload["action_type"] == "COUNTER"
    assert seller_counter_payload["proposal"]["terms"] == buyer_offer["terms"]
    assert seller_counter_payload["proposal"]["conditions"] == buyer_offer["conditions"]
    assert "decision_summary" not in json.dumps(seller_counter_payload)
    assert "seller private strategy" not in json.dumps(engine.state.public_transcript)
    assert "buyer private strategy" not in json.dumps(engine.state.public_transcript)
    assert engine.state.outcome.status == "AGREEMENT"
