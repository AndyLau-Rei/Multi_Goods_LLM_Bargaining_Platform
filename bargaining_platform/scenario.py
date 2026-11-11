"""Load scenario and role profile data."""

import json
from pathlib import Path

<<<<<<< HEAD
from .models import validate_scenario

=======
>>>>>>> 477fb69f6db7b0d7c7aada3e78e36efc42d23150

def load_scenario(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("Cannot read scenario file") from exc
<<<<<<< HEAD
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid scenario JSON") from exc
    return validate_scenario(data)
=======
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid scenario JSON") from exc

    required = {"scenario_id", "max_rounds", "starting_role", "catalog", "private_profiles"}
    if not isinstance(data, dict) or not required <= data.keys():
        raise ValueError("Missing required scenario keys")
    return data
>>>>>>> 477fb69f6db7b0d7c7aada3e78e36efc42d23150
