from bargaining_platform.scenario import load_scenario


def test_load_sample_scenario():
    scenario = load_scenario("scenarios/shop_bundle.json")
    assert isinstance(scenario, dict)
    assert scenario["scenario_id"] == "shop-v1"
    assert len(scenario["catalog"]) == 2
