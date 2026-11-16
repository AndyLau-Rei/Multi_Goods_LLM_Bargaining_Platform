# Multi-Goods LLM Bargaining Platform

This repository provides a deterministic command-line bargaining simulator for a seller and buyer negotiating over one or more catalog items. It supports typed proposals, conditional bundle discounts, role-private prompts, event logging, SQLite persistence, JSONL export, replay, experiments, metrics, and failure reports.

## Install

From the repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The editable install exposes the `bargaining_platform` package and the development extra installs pytest.

## CLI overview

Every operation is an explicit subcommand. Run `python -m bargaining_platform.cli --help` or append `--help` to any command for its options.

### Run one negotiation

Validate and load a scenario without model calls:

```powershell
python -m bargaining_platform.cli run --scenario scenarios/shop_bundle.json --agent scripted
```

Run the deterministic offline demo and save it to `results/runs.db`:

```powershell
python -m bargaining_platform.cli run --scenario scenarios/shop_bundle.json --agent mock
```

Use an LLM adapter when `LLM_API_KEY` is set; otherwise the runner falls back to the mock adapter:

```powershell
$env:LLM_API_KEY = "your-api-key"
python -m bargaining_platform.cli run --scenario scenarios/shop_bundle.json --agent llm
```

Role-specific prompt templates can be selected at runtime:

```powershell
python -m bargaining_platform.cli run --scenario scenarios/shop_bundle.json --agent mock `
  --seller-prompt prompts/seller_prompt_multi.txt `
  --buyer-prompt prompts/buyer_prompt_multi.txt
```

### Run an experiment matrix

```powershell
python -m bargaining_platform.cli experiment `
  --config configs/experiment_baseline.json `
  --db-path results/baseline.db
```

An experiment config contains `repetitions`, `scenario_path`, an optional `agent_type`, and a `variations` list. Each variation receives a stable condition ID and each repetition receives its own run ID.

### Compare baseline and treatment

```powershell
python -m bargaining_platform.cli compare `
  --config-a configs/ablation_no_bundle.json `
  --config-b configs/ablation_with_bundle.json `
  --db-path results/ablation.db
```

The comparison prints agreement rate, mean joint utility, GFT, and bundle frequency. Each config must contain exactly one variation.

### Calculate metrics

For every condition:

```powershell
python -m bargaining_platform.cli metrics --all --db-path results/baseline.db
```

For one condition:

```powershell
python -m bargaining_platform.cli metrics --condition-id CONDITION_ID --db-path results/baseline.db
```

For one run:

```powershell
python -m bargaining_platform.cli metrics --run-id RUN_ID --db-path results/runs.db
```

Metrics are calculated from persisted structured runs and events. They include agreement and no-deal rates, role and joint utility, rounds, bundle frequency, per-unit discounts, retries, GFT, normalized price bias, and individual rationality.

### Generate a failure report

```powershell
python -m bargaining_platform.cli failures --run-id RUN_ID --db-path results/runs.db
```

The report links parser, hard-constraint, bundle, walk-away, and timeout failures to their event, actor, round, and attempted action.

### Replay a run

Replay uses the saved scenario and action log without calling an LLM:

```powershell
python -m bargaining_platform.cli replay --run-id RUN_ID --db-path results/runs.db
```

The command reports success or returns an error when the persisted run diverges from deterministic engine behavior.

### Export events as JSONL

```powershell
python -m bargaining_platform.cli export `
  --run-id RUN_ID `
  --output results/RUN_ID.jsonl `
  --db-path results/runs.db
```

Each event is written on one line. Decimal monetary values are serialized as strings so exact precision and typed conditions survive export.

## Scenario essentials

`scenarios/shop_bundle.json` demonstrates products A and B, seller stock and price floors, buyer values and budget, legal quantity limits, reservation utilities, and a capped `discount_if_item_in_bundle` condition. The engine accepts `PROPOSE`, `COUNTER`, `ACCEPT`, and `WALK_AWAY`; acceptance commits the complete current proposal atomically.

Private profiles are isolated by role. The seller's costs and floors never enter the buyer view, and the buyer's values and budget never enter the seller view. Invalid model output receives one retry and then ends with an explicit `INVALID_ACTION` outcome.

## Tests

```powershell
pytest -q
```

The suite covers schema validation, state transitions, bundle discounts, hard constraints, privacy, utility and reservation checks, event storage, replay, LLM adapter behavior, experiments, metrics, failure analysis, and CLI routing.

On Windows, if pytest cannot clean its default temporary directory, use a workspace-local directory:

```powershell
$env:TEMP = "$PWD\.tmp"
$env:TMP = "$PWD\.tmp"
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
pytest -q
```

## Package layout

- `bargaining_platform/cli.py` — explicit local CLI commands.
- `bargaining_platform/runner.py` — role-scoped model loop and persistence.
- `bargaining_platform/engine.py` — authoritative state machine.
- `bargaining_platform/models.py` — typed scenario, action, proposal, and outcome models.
- `bargaining_platform/validator.py` — structural, hard-constraint, and bundle validation.
- `bargaining_platform/prompt_builder.py` — private views and external prompt templates.
- `bargaining_platform/storage.py` — event logging, SQLite, JSONL, and replay.
- `bargaining_platform/experiments.py` — repeated runs and config comparison.
- `bargaining_platform/metrics.py` — aggregate metrics and failure reports.

The project does not include a browser UI; the backend API is available for later clients.

## Backend API

Install the development dependencies, then start the synchronous API with:

```powershell
uvicorn bargaining_platform.api:app --reload
```

Start a mock run and inspect it:

```powershell
curl -X POST http://127.0.0.1:8000/runs `
  -H "Content-Type: application/json" `
  -d '{"scenario_id":"scenarios/shop_bundle.json","agent_type":"mock"}'
curl http://127.0.0.1:8000/runs/RUN_ID/status
curl "http://127.0.0.1:8000/runs/RUN_ID/view?role=buyer"
curl http://127.0.0.1:8000/runs/RUN_ID/events
```

Set `BARGAINING_DB_PATH` to use a different SQLite file. Role-scoped views are built with
`make_private_view`, so they contain only the requested role's private profile and public state.
