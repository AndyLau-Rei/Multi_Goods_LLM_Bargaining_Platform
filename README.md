# Multi-Goods LLM Bargaining Platform

This repository runs finite-turn negotiations between a seller agent and a buyer agent. A proposal can contain multiple products, and a typed condition can discount product A when product B is included in the same accepted bundle.

The current implementation is a Python command-line simulator. It provides scripted scenario validation, deterministic mock agents, an OpenAI-compatible LLM adapter, SQLite run storage, aggregate metrics, failure reports, and deterministic replay. There is no web UI or HTTP server yet.

## 1. Start in the repository directory

Download the repository to your local directory:

```powershell
git clone https://github.com/AndyLau-Rei/Multi_Goods_LLM_Bargaining_Platform
```

## 2. Install the project

Recommended PowerShell setup:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The editable install exposes the `bargaining_platform` package. The `dev` extra installs `pytest`.

## 3. Validate a scenario

```powershell
python -m bargaining_platform.cli --scenario scenarios/shop_bundle.json --agent scripted
```

Expected output:

```text
Loaded scenario: shop-v1 with 2 catalog items
```

`scripted` currently validates and loads the JSON scenario only; it does not run a negotiation.

The sample scenario contains products A and B, seller stock and price floors, buyer values and budget, legal quantity limits, an eight-round limit, and a maximum bundle discount.

## 4. Run an offline negotiation

```powershell
python -m bargaining_platform.cli --scenario scenarios/shop_bundle.json --agent mock
```

The mock adapters run a deterministic demonstration without network access. They submit a standalone offer, introduce an A+B bundle, apply the typed discount to A, and accept the bundle.

Expected output has this form:

```text
Completed negotiation: AGREEMENT (run_id: run-...)
```

The run ID is needed for inspection and failure reports. Runs are stored by default in `results/runs.db`.

## 5. Run two LLM agents

The CLI uses `OpenAIAdapter`, which sends JSON-schema output requests to an OpenAI-compatible Chat Completions endpoint. Set the key before using the LLM mode:

```powershell
$env:LLM_API_KEY = "your-api-key"
python -m bargaining_platform.cli --scenario scenarios/shop_bundle.json --agent llm
```

The adapter defaults to model `gpt-4o-mini` and endpoint `https://api.openai.com/v1/chat/completions`. The CLI does not currently expose model or endpoint flags; change `OpenAIAdapter(...)` in `bargaining_platform/cli.py` or call the Python API if a different compatible endpoint is required.

If `LLM_API_KEY` is missing, `--agent llm` deliberately falls back to the deterministic mock adapters. This is useful for local smoke runs, but it is not an LLM experiment.

Each turn builds a role-specific private view, renders a prompt, calls the adapter, parses the JSON action, validates it, updates the authoritative state, and appends only public information to the transcript. Invalid output is handled by the engine's retry policy and is logged.

## 6. Configure a scenario

Copy the sample before changing it:

```powershell
Copy-Item scenarios/shop_bundle.json scenarios/my_shop.json
notepad scenarios/my_shop.json
```

Then run it:

```powershell
python -m bargaining_platform.cli --scenario scenarios/my_shop.json --agent mock
```

Important fields are:

- `catalog`: public product IDs, units, and seller stock.
- `private_profiles.seller.unit_cost`: seller cost, kept private by the prompt builder.
- `private_profiles.seller.minimum_unit_price`: seller price floors.
- `private_profiles.buyer.unit_value`: buyer valuations, kept private.
- `private_profiles.buyer.budget`: buyer total budget.
- `reservation_utility`: minimum utility required for final acceptance.
- `legal_ranges.max_quantity`: per-product quantity limits.
- `bundle_rules.max_discount_per_unit`: maximum typed discount.
- `max_rounds`: maximum negotiation rounds.

The engine accepts `PROPOSE`, `COUNTER`, `ACCEPT`, and `WALK_AWAY`. Acceptance is atomic: it accepts the complete current proposal. A `discount_if_item_in_bundle` condition is valid only when the required item and minimum quantity are present in that proposal, and the discounted price remains above the seller floor.

## 7. Run repeated experiments

Use an experiment configuration containing `repetitions`, `scenario_path`, an optional default `agent_type`, and a `variations` list:

```powershell
python -m bargaining_platform.cli experiment --config configs/experiment_baseline.json --db-path results/baseline.db
```

This expands each variation and repeats it, assigning a stable condition ID to each variation. The provided baseline varies the buyer budget and runs two repetitions per condition.

Run the bundle ablation pair:

```powershell
python -m bargaining_platform.cli compare --config-a configs/ablation_no_bundle.json --config-b configs/ablation_with_bundle.json --db-path results/ablation.db
```

`compare` requires one variation in each config and reports agreement rate, mean joint utility, GFT, and bundle frequency. The no-bundle config sets the maximum discount to zero; the treatment config uses the sample bundle rule.

To run an experiment with the LLM adapter, set the key and put `"agent_type": "llm"` in the experiment config:

```powershell
$env:LLM_API_KEY = "your-api-key"
python -m bargaining_platform.cli experiment --config configs/my_llm_experiment.json --db-path results/my_llm_experiment.db
```

## 8. Calculate aggregate metrics

Calculate metrics for every condition in a database:

```powershell
python -m bargaining_platform.cli metrics --all --db-path results/baseline.db
```

Calculate one condition only:

```powershell
python -m bargaining_platform.cli metrics --condition-id CONDITION_ID --db-path results/baseline.db
```

The output includes run count, agreement/no-deal rate, buyer/seller/joint utility, average rounds, bundle frequency, discount size, retries, GFT, normalized price bias, and individual rationality. Metrics are recomputed from persisted structured events, not from free-form model explanations.

## 9. Inspect, replay, and diagnose a run

Inspect a complete persisted run with Python:

```powershell
python -c "from bargaining_platform.storage import load_run_from_sqlite; import pprint; pprint.pp(load_run_from_sqlite('RUN_ID', 'results/runs.db'))"
```

Replay the run without calling an LLM:

```powershell
python -c "from bargaining_platform.storage import replay_run; print(replay_run('RUN_ID', 'results/runs.db').outcome)"
```

Replay reconstructs the state from the stored scenario and action log. It raises `ReplayDivergenceError` if a stored action, effective price, transition, transcript, or outcome no longer matches deterministic engine behavior.

Generate a failure report:

```powershell
python -m bargaining_platform.cli failures --run-id RUN_ID --db-path results/runs.db
```

The report classifies parser errors, hard-constraint errors, bundle semantic errors, walk-away termination, and maximum-round timeouts.

## 10. Run the test suite

```powershell
pytest -q
```

The tests cover scenario validation, state transitions, action parsing, private-view isolation, inventory and price constraints, conditional discounts, atomic acceptance, utility calculation, storage, replay, experiment matrices, metrics, failure analysis, and mock LLM runs.

On Windows, pytest may need a writable system temporary directory because several tests use the `tmp_path` fixture. If pytest reports `PermissionError` while scanning the default temp directory, set a writable temporary directory for the shell and rerun:

```powershell
$env:TEMP = "$PWD\.tmp"
$env:TMP = "$PWD\.tmp"
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
pytest -q
```

## 11. Main source files

- `bargaining_platform/cli.py`: command-line subcommands.
- `bargaining_platform/runner.py`: agent loop and run persistence.
- `bargaining_platform/engine.py`: authoritative turn state machine.
- `bargaining_platform/models.py`: scenario and action schemas.
- `bargaining_platform/validator.py`: action, inventory, price, budget, and bundle validation.
- `bargaining_platform/prompt_builder.py`: role-scoped prompts and private-information isolation.
- `bargaining_platform/model_adapter.py`: mock and OpenAI-compatible generation adapters.
- `bargaining_platform/utility.py`: buyer, seller, and joint utility plus reservation checks.
- `bargaining_platform/storage.py`: SQLite persistence, JSONL export helpers, and replay.
- `bargaining_platform/experiments.py`: repeated runs and configuration comparison.
- `bargaining_platform/metrics.py`: aggregate metrics and failure reports.

## 12. Current limits

The repository has no web UI, HTTP API, provider-specific Gemini adapter, or government–firm scenario file yet. `experiments.py` supports repeated runs, but it does not automatically sweep arbitrary model parameters. The source documents do not define all government–firm utility weights or policy thresholds, so those values must be supplied in a versioned scenario before that scenario is run.
