from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.prompt_builder import make_private_view, render_prompt
from bargaining_platform.scenario import load_scenario


def _views():
    engine = NegotiationEngine(load_scenario("scenarios/shop_bundle.json"))
    return (
        make_private_view("seller", engine.state),
        make_private_view("buyer", engine.state),
    )


def test_seller_prompt_generation():
    seller_view, _ = _views()

    prompt = render_prompt("seller", seller_view)

    assert "You are a tough seller trying to maximize profit and clear inventory." in prompt
    assert "seller" in prompt
    assert "maximize seller utility" in prompt
    assert "unit_cost" in prompt
    assert "70" in prompt
    assert "130" not in prompt
    assert "unit_value" not in prompt


def test_buyer_prompt_generation():
    _, buyer_view = _views()

    prompt = render_prompt("buyer", buyer_view)

    assert "You are a strategic buyer trying to get the lowest price and best bundle deal." in prompt
    assert "buyer" in prompt
    assert "maximize buyer utility" in prompt
    assert "unit_value" in prompt
    assert "130" in prompt
    assert "70" not in prompt
    assert "unit_cost" not in prompt


def test_prompt_includes_json_instructions():
    _, buyer_view = _views()

    prompt = render_prompt("buyer", buyer_view)

    assert "Output ONLY one valid JSON object" in prompt
    for action_type in ("PROPOSE", "COUNTER", "ACCEPT", "WALK_AWAY"):
        assert action_type in prompt
    assert '"action_type"' in prompt
    assert '"proposal_id"' in prompt
    assert '"proposal": null' in prompt
