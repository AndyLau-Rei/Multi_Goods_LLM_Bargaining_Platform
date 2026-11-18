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
    assert '"proposal_id"' in prompt
    assert "proposal to null" in prompt


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
