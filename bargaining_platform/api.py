"""Small synchronous FastAPI facade for the bargaining engine."""

from __future__ import annotations

import os
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .metrics import calculate_metrics
from .model_adapter import OpenAIAdapter
from .prompt_builder import make_private_view
from .runner import default_mock_adapters, run_llm_negotiation
from .scenario import load_scenario
from .storage import DEFAULT_DB_PATH, load_run_from_sqlite, replay_run


app = FastAPI(title="Bargaining Platform API", version="0.1.0")
_STATIC_DIR = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def viewer() -> str:
    return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")


class RunRequest(BaseModel):
    scenario_id: str
    agent_type: Literal["mock", "llm"] = "mock"


def _db_path() -> Path:
    """Allow deployments and tests to select a database without changing the API shape."""
    return Path(os.getenv("BARGAINING_DB_PATH", str(DEFAULT_DB_PATH)))


@app.post("/runs")
def start_run(request: RunRequest) -> dict[str, str]:
    """Run one negotiation synchronously and return its persisted run ID."""
    try:
        scenario = load_scenario(request.scenario_id)
        use_mock = request.agent_type == "mock" or not os.getenv("LLM_API_KEY")
        if use_mock:
            adapters = default_mock_adapters()
        else:
            adapter = OpenAIAdapter()
            adapters = {"seller": adapter, "buyer": adapter}
        engine = run_llm_negotiation(
            scenario,
            adapters,
            db_path=_db_path(),
            mock_demo=use_mock,
        )
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail="Run could not be started") from exc
    return {"run_id": engine.run_id}


@app.get("/runs/{run_id}/status")
def run_status(run_id: str) -> dict[str, Any]:
    try:
        run = load_run_from_sqlite(run_id, _db_path())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "run_id": run_id,
        "outcome": run["outcome"],
        "metrics": calculate_metrics(_db_path(), run_id=run_id),
    }


@app.get("/runs/{run_id}/events")
def run_events(run_id: str) -> dict[str, Any]:
    """Return replayed public actions and transition summaries only."""
    try:
        run = load_run_from_sqlite(run_id, _db_path())
        state = replay_run(run_id, _db_path())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail="Run could not be replayed") from exc

    transcript = iter(state.public_transcript)
    public_events = []
    for event in run["events"]:
        record = {
            "event_id": event["event_id"],
            "event_type": event["event_type"],
            "actor": event["actor"],
        }
        if event["event_type"] == "ACTION_ATTEMPT":
            # Validation is used internally to identify committed actions only.
            if not (event.get("validation_result") or {}).get("ok"):
                continue
            record["public_transcript"] = _public_action(next(transcript))
        elif event["event_type"] == "STATE_TRANSITION":
            record["state_transition_summary"] = event["state_transition_summary"]
        else:
            continue
        public_events.append(record)
    return {"run_id": run_id, "round_number": state.round_number, "events": public_events}


def _public_action(action: dict) -> dict:
    """Expose effective prices without changing the proposal's base terms."""
    public = deepcopy(action)
    proposal = public.get("proposal")
    if not isinstance(proposal, dict):
        return public
    effective = proposal.get("effective_prices") or {}
    effective_total = Decimal(0)
    for term in proposal.get("terms", []):
        item_id = term.get("item_id")
        if item_id in effective:
            effective_unit_price = effective[item_id]
            line_total = Decimal(str(effective_unit_price)) * term["quantity"]
            term["effective_unit_price"] = effective_unit_price
            term["effective_total_price"] = _serialize_money(line_total, effective_unit_price)
            effective_total += line_total
    proposal["effective_total_price"] = _serialize_money(effective_total, *effective.values())
    return public


def _serialize_money(value: Decimal, *source_values: object) -> int | str:
    """Keep integral integer amounts numeric and preserve fractional precision as text."""
    if all(type(source) is int for source in source_values) and value == value.to_integral_value():
        return int(value)
    return format(value, "f")


@app.get("/runs/{run_id}/view")
def run_view(
    run_id: str,
    role: Literal["buyer", "seller"] = Query(..., description="Role-scoped view to return."),
    agent_role: str | None = Header(default=None, alias="X-Agent-Role"),
) -> dict[str, Any]:
    """Reconstruct authoritative state and expose only the requested role's private view."""
    # TODO: Replace this local role header with authenticated session binding before remote use.
    if agent_role != role:
        raise HTTPException(status_code=403, detail="Role view not authorized")
    try:
        state = replay_run(run_id, _db_path())
        return make_private_view(role, state)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail="Run could not be replayed") from exc
