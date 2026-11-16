import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bargaining_platform.scenario import load_scenario


def _sample_data():
    with open("scenarios/shop_bundle.json", encoding="utf-8") as file:
        return json.load(file)


def test_shop_bundle_loads():
    scenario = load_scenario("scenarios/shop_bundle.json")
    assert scenario["scenario_id"] == "shop-v1"
    assert [entry["id"] for entry in scenario["catalog"]] == ["A", "B"]
    assert scenario["private_profiles"]["seller"]["reservation_utility"] == 100
    assert scenario["private_profiles"]["buyer"]["reservation_utility"] == 150
    assert scenario["private_profiles"]["seller"]["allowed_actions"] == [
        "PROPOSE", "COUNTER", "ACCEPT", "WALK_AWAY"
    ]
    assert scenario["private_profiles"]["buyer"]["allowed_actions"] == [
        "ACCEPT", "COUNTER", "WALK_AWAY"
    ]
    assert scenario["legal_ranges"]["max_quantity"] == {"A": 5, "B": 5}
    assert scenario["bundle_rules"]["max_discount_per_unit"] == 30


def test_missing_allowed_actions_is_rejected():
    data = _sample_data()
    del data["private_profiles"]["seller"]["allowed_actions"]

    with patch.object(Path, "read_text", return_value=json.dumps(data)):
        with pytest.raises(
            ValueError,
            match="Missing required key: private_profiles.seller.allowed_actions",
        ):
            load_scenario("missing_allowed_actions.json")


def test_missing_catalog_id_is_rejected():
    data = _sample_data()
    del data["catalog"][0]["id"]

    with patch.object(Path, "read_text", return_value=json.dumps(data)):
        with pytest.raises(ValueError, match=r"Missing required key: catalog\[0\]\.id"):
            load_scenario("missing_id.json")


def test_public_field_under_private_profiles_is_rejected():
    data = _sample_data()
    data["private_profiles"]["catalog"] = data.pop("catalog")

    with patch.object(Path, "read_text", return_value=json.dumps(data)):
        with pytest.raises(ValueError, match="Ambiguous visibility"):
            load_scenario("ambiguous_visibility.json")
