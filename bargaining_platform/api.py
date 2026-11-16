"""Small synchronous FastAPI facade for the bargaining engine."""

from __future__ import annotations

import os
from copy import deepcopy
from decimal import Decimal
from hmac import compare_digest
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .metrics import calculate_metrics
from .model_adapter import OpenAIAdapter
from .models import json_safe
from .prompt_builder import make_private_view
from .runner import default_mock_adapters, run_llm_negotiation
from .scenario import load_scenario
from .storage import DEFAULT_DB_PATH, load_run_from_sqlite, replay_run
from .validator import _multiply_money, _sum_money


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
    line_totals = []
    for term in proposal.get("terms", []):
        item_id = term.get("item_id")
        if item_id not in effective:
            continue
        effective_unit_price = effective[item_id]
        price = (
            Decimal(effective_unit_price)
            if isinstance(effective_unit_price, str)
            else effective_unit_price
        )
        line_total = _multiply_money(term["quantity"], price)
        term["effective_unit_price"] = effective_unit_price
        term["effective_total_price"] = json_safe(line_total)
        line_totals.append(line_total)
    proposal["effective_total_price"] = json_safe(_sum_money(line_totals))
    return public


@app.get("/runs/{run_id}/view")
def run_view(
    run_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    """Reconstruct state for the role selected by its Bearer credential."""
    buyer_secret = os.getenv("BUYER_API_SECRET")
    seller_secret = os.getenv("SELLER_API_SECRET")
    scheme, _, token = (authorization or "").partition(" ")
    if (
        scheme.lower() != "bearer"
        or not token
        or not buyer_secret
        or not seller_secret
        or compare_digest(buyer_secret, seller_secret)
    ):
        raise HTTPException(status_code=403, detail="Role view not authorized")
    buyer_match = compare_digest(token, buyer_secret)
    seller_match = compare_digest(token, seller_secret)
    if not buyer_match and not seller_match:
        raise HTTPException(status_code=403, detail="Role view not authorized")
    role: Literal["buyer", "seller"] = "buyer" if buyer_match else "seller"
    try:
        state = replay_run(run_id, _db_path())
        return make_private_view(role, state)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail="Run could not be replayed") from exc
