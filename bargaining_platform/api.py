"""FastAPI facade for the bargaining engine."""

from __future__ import annotations

import logging
import os
import uuid
from copy import deepcopy
from decimal import Decimal
from hmac import compare_digest
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from .metrics import calculate_metrics
from .models import json_safe
from .prompt_builder import make_private_view
from .provider_factory import available_models, create_role_adapters, resolve_models
from .runner import default_mock_adapters, run_llm_negotiation
from .scenario import load_scenario
from .storage import DEFAULT_DB_PATH, load_run_from_sqlite, replay_run
from .validator import _multiply_money, _sum_money


app = FastAPI(title="Bargaining Platform API", version="0.1.0")
_STATIC_DIR = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Avoid echoing request values, which may contain a mistakenly supplied secret."""
    errors = [
        {key: value for key, value in error.items() if key != "input"}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})

# shortcut: progress lives in one API worker, use shared storage for multi-worker serving.
_active_runs: dict[str, dict[str, Any]] = {}
_active_runs_lock = Lock()
_logger = logging.getLogger(__name__)

_PUBLIC_METRIC_FIELDS = {
    "run_count",
    "agreement_rate",
    "no_deal_rate",
    "average_rounds",
    "bundle_frequency",
    "average_discount_size",
    "average_retries",
}


@app.get("/", response_class=HTMLResponse)
def viewer() -> str:
    return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/models")
def models() -> dict[str, Any]:
    """List enabled public aliases without returning private provider settings."""
    return available_models()


@app.get("/scenarios")
def scenarios() -> list[dict[str, str]]:
    """List scenario files available to the local viewer."""
    directory = Path("scenarios")
    return [
        {"id": f"scenarios/{path.name}", "name": load_scenario(str(path))["scenario_id"]}
        for path in sorted(directory.glob("*.json"))
    ]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str
    agent_type: Literal["mock", "llm"] = "mock"
    seller_model: str | None = None
    buyer_model: str | None = None


def _db_path() -> Path:
    """Allow deployments and tests to select a database without changing the API shape."""
    return Path(os.getenv("BARGAINING_DB_PATH", str(DEFAULT_DB_PATH)))


def _public_metrics(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Keep private utility and valuation-derived fields out of public status."""
    return {
        condition: {
            key: value
            for key, value in values.items()
            if key in _PUBLIC_METRIC_FIELDS
        }
        for condition, values in metrics.items()
        if isinstance(values, dict)
    }


def _run_in_background(
    run_id: str,
    scenario: dict,
    agent_type: str,
    db_path: Path,
    seller_model: str | None = None,
    buyer_model: str | None = None,
) -> None:
    try:
        model_config = None
        adapters = None
        if agent_type == "llm":
            models, version = resolve_models(seller_model, buyer_model)
            adapters, model_config = create_role_adapters(models, version)
        use_mock = agent_type == "mock" or adapters is None
        if use_mock:
            adapters = default_mock_adapters()

        def publish(progress: dict) -> None:
            with _active_runs_lock:
                active = _active_runs[run_id]
                active["round_number"] = progress["round_number"]
                for event in progress["events"]:
                    if "public_transcript" in event:
                        event = {**event, "public_transcript": _public_action(event["public_transcript"])}
                    active["events"].append(event)

        run_llm_negotiation(
            scenario,
            adapters,
            run_id=run_id,
            db_path=db_path,
            mock_demo=use_mock,
            progress_callback=publish,
            model_config=model_config,
        )
    except Exception:
        _logger.exception("Background negotiation failed for run %s", run_id)
        with _active_runs_lock:
            _active_runs[run_id]["status"] = "ERROR"
    else:
        # The saved run is readable before the in-memory copy disappears.
        with _active_runs_lock:
            del _active_runs[run_id]


@app.post("/runs", status_code=202)
def start_run(request: RunRequest, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Schedule one negotiation and return its run ID immediately."""
    try:
        scenario = load_scenario(request.scenario_id)
        if request.agent_type == "llm":
            resolve_models(request.seller_model, request.buyer_model)
        elif request.seller_model is not None or request.buyer_model is not None:
            raise ValueError("MODEL_SELECTION_REQUIRES_LLM")
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        detail = str(exc) if isinstance(exc, ValueError) and str(exc) in {
            "UNKNOWN_MODEL", "MODEL_DISABLED", "MODEL_SELECTION_REQUIRES_LLM"
        } else "Run could not be started"
        raise HTTPException(status_code=400, detail=detail) from exc
    run_id = f"run-{uuid.uuid4().hex}"
    with _active_runs_lock:
        _active_runs[run_id] = {"status": "IN_PROGRESS", "round_number": 1, "events": []}
    background_tasks.add_task(
        _run_in_background,
        run_id,
        scenario,
        request.agent_type,
        _db_path(),
        request.seller_model,
        request.buyer_model,
    )
    return {"run_id": run_id}


@app.get("/runs/{run_id}/status")
def run_status(run_id: str) -> dict[str, Any]:
    with _active_runs_lock:
        active = _active_runs.get(run_id)
        if active is not None:
            status = active["status"]
            return {
                "run_id": run_id,
                "status": status,
                "round": active["round_number"],
                "outcome": {"status": status} if status != "IN_PROGRESS" else None,
            }
    try:
        run = load_run_from_sqlite(run_id, _db_path())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    outcome = run["outcome"] or {}
    public_outcome = {
        key: value for key, value in outcome.items() if key != "utility_metrics"
    }
    return {
        "run_id": run_id,
        "outcome": public_outcome,
        "metrics": _public_metrics(calculate_metrics(_db_path(), run_id=run_id)),
    }


@app.get("/runs/{run_id}/events")
def run_events(run_id: str) -> dict[str, Any]:
    """Return replayed public actions and transition summaries only."""
    with _active_runs_lock:
        active = _active_runs.get(run_id)
        if active is not None:
            return {
                "run_id": run_id,
                "round_number": active["round_number"],
                "events": deepcopy(active["events"]),
            }
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
