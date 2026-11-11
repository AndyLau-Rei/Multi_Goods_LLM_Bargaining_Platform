"""Load scenario and role profile data."""

import json
from pathlib import Path

from .models import validate_scenario

def load_scenario(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("Cannot read scenario file") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid scenario JSON") from exc
    return validate_scenario(data)