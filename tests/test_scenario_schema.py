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
