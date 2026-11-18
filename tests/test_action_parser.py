import json

import pytest

from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.models import Action, ValidationResult
from bargaining_platform.scenario import load_scenario
from bargaining_platform.validator import parse_action


def _proposal_payload():
    return {
        "proposal_id": "offer-1",
        "acceptance_mode": "all_or_nothing",
        "terms": [
            {
                "type": "purchase",
                "item_id": "A",
                "quantity": 1,
                "unit_price": 120,
                "unit": "piece",
            }
        ],
        "conditions": [],
        "decision_summary": "",
        "original_prices": {"A": 120},
        "effective_prices": {"A": 120},
    }


@pytest.mark.parametrize(
    ("raw_text", "role", "action_type"),
    [
        (
            json.dumps({
                "action_type": "PROPOSE",
                "proposal_id": "offer-1",
                "proposal": _proposal_payload(),
            }),
            "seller",
            "PROPOSE",
        ),
        (
            "```json\n{\"action_type\":\"ACCEPT\",\"proposal_id\":\"offer-1\"}\n```",
            "buyer",
            "ACCEPT",
        ),
    ],
)
def test_parse_valid_json(raw_text, role, action_type):
    result = parse_action(raw_text, role)

    assert isinstance(result, Action)
    assert result.action_type == action_type
    assert result.actor == role
    if action_type == "PROPOSE":
        assert result.proposal.proposal_id == "offer-1"


def test_reject_malformed_json():
    result = parse_action('{"action_type":"ACCEPT",', "buyer")

    assert isinstance(result, ValidationResult)
    assert result.error_code == "MALFORMED_JSON"
    assert result.ok is False


def test_reject_extra_fields():
    result = parse_action(
        '{"action_type":"ACCEPT","proposal_id":"offer-1","note":"I like this"}',
        "buyer",
    )

    assert isinstance(result, ValidationResult)
    assert result.error_code == "SCHEMA_VIOLATION"
    assert result.ok is False


def test_parse_error_is_logged_and_uses_existing_retry_policy():
    engine = NegotiationEngine(load_scenario("scenarios/shop_bundle.json"))
    raw_text = '{"action_type":"PROPOSE",'

    result = engine.step(raw_text, role="seller")

    assert result.error_code == "MALFORMED_JSON"
    assert engine.state.invalid_attempts == 1
    assert engine.state.active_proposal is None
    event = engine.event_logger.events[0]
    assert event.raw_output == raw_text
    assert event.parsed_action is None
    assert event.validation_result["error_code"] == "MALFORMED_JSON"


def test_model_can_omit_or_misstate_proposal_ids():
    engine = NegotiationEngine(load_scenario("scenarios/shop_bundle.json"))
    proposal = _proposal_payload()
    proposal.pop("proposal_id")
    proposal.pop("original_prices")
    proposal.pop("effective_prices")

    first = engine.step(
        json.dumps({"action_type": "PROPOSE", "proposal": proposal}),
        role="seller",
    )
    assert first.active_proposal.proposal_id == "proposal-1"

    proposal["terms"][0]["unit_price"] = 110
    second = engine.step(
        json.dumps({
            "action_type": "COUNTER",
            "proposal_id": "stale-model-id",
            "proposal": proposal,
        }),
        role="buyer",
    )
    assert second.active_proposal.proposal_id == "proposal-2"
    assert second.invalid_attempts == 0
