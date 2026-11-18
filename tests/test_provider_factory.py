import json

import pytest

from bargaining_platform import provider_factory
from bargaining_platform.model_adapter import GenerationResult, GeminiAdapter, OpenAIAdapter
from bargaining_platform.runner import run_llm_negotiation
from bargaining_platform.scenario import load_scenario
from bargaining_platform.storage import load_run_from_sqlite


def _registry(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({
        "version": "test-v3",
        "default_models": {"seller": "gpt-test", "buyer": "gemini-test"},
        "models": [
            {"alias": "gpt-test", "provider": "openai", "model_id": "gpt-real", "base_url": "https://gpt.test/v1", "enabled": True, "json_schema": True},
            {"alias": "gemini-test", "provider": "gemini", "model_id": "gemini-real", "base_url": "https://gemini.test/v1beta", "enabled": True},
            {"alias": "off", "provider": "deepseek", "model_id": "off-real", "base_url": "https://deepseek.test/v1", "enabled": False},
        ],
    }), encoding="utf-8")
    return path


def test_role_keys_and_models_are_resolved_independently(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_factory, "_WHITELIST_PATH", _registry(tmp_path))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    models, version = provider_factory.resolve_models("gpt-test", "gemini-test")

    adapters, metadata = provider_factory.create_role_adapters(
        models,
        version,
        role_keys={"seller": "seller-secret", "buyer": "buyer-secret"},
    )

    assert isinstance(adapters["seller"], OpenAIAdapter)
    assert isinstance(adapters["buyer"], GeminiAdapter)
    assert adapters["seller"] is not adapters["buyer"]
    assert adapters["seller"].api_key == "seller-secret"
    assert adapters["buyer"].api_key == "buyer-secret"
    assert adapters["seller"].model == "gpt-real"
    assert adapters["buyer"].model == "gemini-real"
    assert metadata["roles"]["seller"]["public_alias"] == "gpt-test"
    assert metadata["roles"]["buyer"]["provider"] == "gemini"
    assert "secret" not in json.dumps(metadata)


@pytest.mark.parametrize(
    ("alias", "error"),
    [("private-value", "UNKNOWN_MODEL"), ("off", "MODEL_DISABLED")],
)
def test_whitelist_rejects_unknown_and_disabled_aliases(tmp_path, monkeypatch, alias, error):
    monkeypatch.setattr(provider_factory, "_WHITELIST_PATH", _registry(tmp_path))
    with pytest.raises(ValueError, match=f"^{error}$"):
        provider_factory.resolve_models(alias, "gemini-test")


def test_missing_role_key_uses_secret_free_mock_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_factory, "_WHITELIST_PATH", _registry(tmp_path))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    models, version = provider_factory.resolve_models("gpt-test", "gemini-test")

    adapters, metadata = provider_factory.create_role_adapters(
        models,
        version,
        role_keys={"seller": "seller-secret", "buyer": None},
    )

    assert adapters is None
    assert metadata["execution"] == "mock_fallback"
    assert metadata["roles"]["seller"]["provider"] == "mock"
    assert metadata["roles"]["seller"]["requested_model"]["public_alias"] == "gpt-test"
    assert "seller-secret" not in json.dumps(metadata)


def test_shared_key_fills_both_roles(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_factory, "_WHITELIST_PATH", _registry(tmp_path))
    models, version = provider_factory.resolve_models("gpt-test", "gemini-test")

    adapters, _ = provider_factory.create_role_adapters(
        models,
        version,
        role_keys={"seller": None, "buyer": None},
        shared_key="shared-secret",
    )

    assert adapters["seller"].api_key == "shared-secret"
    assert adapters["buyer"].api_key == "shared-secret"
    assert adapters["seller"] is not adapters["buyer"]


def test_model_config_is_persisted_without_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_factory, "_WHITELIST_PATH", _registry(tmp_path))
    models, version = provider_factory.resolve_models("gpt-test", "gemini-test")
    adapters, metadata = provider_factory.create_role_adapters(
        models,
        version,
        role_keys={"seller": "seller-secret", "buyer": "buyer-secret"},
    )
    proposal = {
        "proposal_id": "model-run",
        "acceptance_mode": "all_or_nothing",
        "terms": [{"type": "purchase", "item_id": "A", "quantity": 1, "unit_price": 120, "unit": "piece"}],
        "conditions": [],
        "decision_summary": "",
        "original_prices": {"A": 120},
        "effective_prices": {"A": 120},
    }

    def generate(self, prompt, action_schema):
        action = (
            {"action_type": "PROPOSE", "proposal_id": "model-run", "proposal": proposal}
            if self.model == "gpt-real"
            else {"action_type": "ACCEPT", "proposal_id": "model-run"}
        )
        return GenerationResult(json.dumps(action), {
            "model_version": self.model,
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "latency_ms": 1,
        })

    monkeypatch.setattr(OpenAIAdapter, "generate", generate)
    monkeypatch.setattr(GeminiAdapter, "generate", generate)
    scenario = load_scenario("scenarios/shop_bundle.json")
    scenario["private_profiles"]["seller"]["reservation_utility"] = 0
    scenario["private_profiles"]["buyer"]["reservation_utility"] = 0
    database = tmp_path / "runs.db"

    run_llm_negotiation(
        scenario,
        adapters,
        run_id="model-run",
        db_path=database,
        model_config=metadata,
    )

    saved = load_run_from_sqlite("model-run", database)
    assert saved["model_config"]["roles"]["seller"]["model_id"] == "gpt-real"
    assert saved["model_config"]["roles"]["buyer"]["model_id"] == "gemini-real"
    assert saved["model_config"]["roles"]["seller"]["prompt_version"]
    assert saved["events"][0]["metadata"]["provider"] == "openai"
    serialized = json.dumps(saved)
    assert "seller-secret" not in serialized
    assert "buyer-secret" not in serialized
