import json

import pytest

from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.prompt_builder import make_private_view, render_prompt
from bargaining_platform.scenario import load_scenario


def _views():
    engine = NegotiationEngine(load_scenario("scenarios/test_scenario.json"))
    return (
        make_private_view("seller", engine.state),
        make_private_view("buyer", engine.state),
    )


@pytest.mark.parametrize(
    ("role", "visible_field", "visible_value", "hidden_values"),
    [
        (
            "seller",
            "unit_cost",
            "70",
            ("130", "unit_value"),
        ),
        (
            "buyer",
            "unit_value",
            "130",
            ("70", "unit_cost"),
        ),
    ],
)
def test_role_prompt_generation(
    role, visible_field, visible_value, hidden_values
):
    seller_view, buyer_view = _views()
    prompt = render_prompt(role, seller_view if role == "seller" else buyer_view)

    assert f"You are the {role}" in prompt
    assert f"maximize {role} utility:" in prompt
    assert visible_field in prompt
    assert visible_value in prompt
    for hidden in hidden_values:
        assert hidden not in prompt


def test_prompt_includes_json_instructions():
    _, buyer_view = _views()

    prompt = render_prompt("buyer", buyer_view)

    assert "Return exactly one JSON object" in prompt
    for action_type in ("PROPOSE", "COUNTER", "ACCEPT", "WALK_AWAY"):
        assert action_type in prompt
    assert '"action_type"' in prompt
    assert "proposal_id" not in prompt
    assert "proposal to null" in prompt


def test_model_facing_schema_and_turn_updates_omit_proposal_ids():
    from bargaining_platform.prompt_builder import render_public_update
    from bargaining_platform.runner import _action_schema

    engine = NegotiationEngine(load_scenario("scenarios/test_scenario.json"))
    proposal = {
        "proposal_id": "internal-1",
        "acceptance_mode": "all_or_nothing",
        "terms": [{
            "type": "purchase", "item_id": "A", "quantity": 1,
            "unit_price": 100, "unit": "piece",
        }],
        "conditions": [],
        "decision_summary": "private",
        "original_prices": {"A": 100},
        "effective_prices": {"A": 100},
    }
    engine.step(
        json.dumps({"action_type": "PROPOSE", "proposal": proposal}),
        role="seller",
    )
    view = make_private_view("buyer", engine.state)
    update = render_public_update(view, 0)
    schema = _action_schema()

    assert "proposal_id" not in json.dumps(schema)
    assert schema["properties"]["proposal"]["additionalProperties"] is False
    assert "proposal_id" not in json.dumps(view)
    assert "proposal_id" not in update
    assert "internal-1" not in update


@pytest.mark.parametrize(
    "template",
    [
        "prompts/seller_single.txt",
        "prompts/buyer_single.txt",
        "prompts/seller_multi.txt",
        "prompts/buyer_multi.txt",
    ],
)
def test_active_prompt_templates_never_mention_proposal_ids(template):
    seller_view, buyer_view = _views()
    role = "seller" if "seller" in template else "buyer"
    view = seller_view if role == "seller" else buyer_view

    assert "proposal_id" not in render_prompt(role, view, template_path=template)


def test_custom_prompt_template():
    seller_view, _ = _views()

    prompt = render_prompt("seller", seller_view, template_path="prompts/test_prompt.txt")

    assert prompt.startswith("TEST PROMPT\n")
    assert f"Remaining rounds: {seller_view['remaining_rounds']}" in prompt
    assert '"unit_cost"' in prompt
    assert '"unit_value"' not in prompt


def test_default_prompt_template_remains_available():
    seller_view, _ = _views()

    prompt = render_prompt("seller", seller_view)

    assert "package of multiple catalog products" in prompt
    assert '"discount_if_item_in_bundle"' in prompt
    assert "minimum_required_quantity" in prompt
