"""Resolve public model aliases and build isolated per-role provider clients."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from .model_adapter import GeminiAdapter, OpenAIAdapter


_WHITELIST_PATH = Path(__file__).with_name("model_whitelist.v1.json")
_ROLE_KEY_ENV = {"seller": "SELLER_LLM_API_KEY", "buyer": "BUYER_LLM_API_KEY"}
_ADAPTER_VERSION = "1"


def load_model_whitelist() -> dict[str, Any]:
    return json.loads(_WHITELIST_PATH.read_text(encoding="utf-8"))


def available_models() -> dict[str, Any]:
    whitelist = load_model_whitelist()
    return {
        "version": whitelist["version"],
        "default_models": whitelist["default_models"],
        "models": [
            {"alias": model["alias"], "provider": model["provider"]}
            for model in whitelist["models"]
            if model.get("enabled") is True
        ],
    }


def resolve_models(
    seller_alias: str | None = None,
    buyer_alias: str | None = None,
) -> tuple[dict[str, dict], str]:
    whitelist = load_model_whitelist()
    model_by_alias = {model["alias"]: model for model in whitelist["models"]}
    resolved = {}
    for role, requested in (("seller", seller_alias), ("buyer", buyer_alias)):
        alias = whitelist["default_models"][role] if requested is None else requested
        if not isinstance(alias, str):
            raise ValueError("UNKNOWN_MODEL")
        model = model_by_alias.get(alias)
        if model is None:
            raise ValueError("UNKNOWN_MODEL")
        if model.get("enabled") is not True:
            raise ValueError("MODEL_DISABLED")
        resolved[role] = deepcopy(model)
    return resolved, whitelist["version"]


def create_role_adapters(
    models: dict[str, dict],
    registry_version: str = "1",
    *,
    shared_key: str | None = None,
    role_keys: dict[str, str | None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return independent provider clients and secret-free model metadata."""
    if role_keys is None:
        role_keys = {role: os.getenv(env_name) for role, env_name in _ROLE_KEY_ENV.items()}
    shared_key = os.getenv("LLM_API_KEY") if shared_key is None else shared_key
    keys = {role: role_keys.get(role) or shared_key for role in _ROLE_KEY_ENV}
    if not all(keys.values()):
        raise ValueError("MISSING_API_KEY")
    model_config: dict[str, Any] = {"version": registry_version, "roles": {}}
    for role, model in models.items():
        resolved = {
            "public_alias": model["alias"],
            "provider": model["provider"],
            "model_id": model["model_id"],
            "base_url": model.get("base_url"),
            "adapter_version": _ADAPTER_VERSION,
            "provider_version": "v1beta" if model["provider"] == "gemini" else "openai-compatible-v1",
        }
        model_config["roles"][role] = resolved
    model_config["execution"] = "provider"

    adapters = {}
    for role, model in models.items():
        base_url = model.get("base_url")
        if model["provider"] == "gemini":
            adapters[role] = GeminiAdapter(model["model_id"], keys[role], base_url)
        else:
            adapters[role] = OpenAIAdapter(
                model["model_id"],
                api_key=keys[role],
                endpoint=f"{base_url.rstrip('/')}/chat/completions",
                provider=model["provider"],
                json_schema=model.get("json_schema", False),
            )
    return adapters, model_config
