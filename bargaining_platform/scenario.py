"""Load scenario and role profile data."""

import json
from pathlib import Path


def load_scenario(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("Cannot read scenario file") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid scenario JSON") from exc

    required = {"scenario_id", "max_rounds", "starting_role", "catalog", "private_profiles"}
    if not isinstance(data, dict) or not required <= data.keys():
        raise ValueError("Missing required scenario keys")
    return data
