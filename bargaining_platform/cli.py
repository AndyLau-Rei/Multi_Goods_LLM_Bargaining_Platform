"""Command-line interface for running and inspecting negotiations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .experiments import ExperimentRunner, compare_experiment_configs
from .metrics import calculate_metrics, format_failure_report, generate_failure_report
from .provider_factory import create_role_adapters, resolve_models
from .runner import default_mock_adapters, run_llm_negotiation
from .scenario import load_scenario
from .storage import Event, export_run_to_jsonl, load_run_from_sqlite, replay_run


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bargaining_platform.cli",
        description="Run deterministic bargaining negotiations and inspect their results.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Run one negotiation from a scenario.")
    run.add_argument("--scenario", required=True, help="Path to the scenario JSON file.")
    run.add_argument(
        "--agent",
        choices=("scripted", "llm", "mock"),
        default="scripted",
        help="Agent mode (default: scripted).",
    )
    run.add_argument("--seller-prompt", help="Optional seller prompt template path.")
    run.add_argument("--buyer-prompt", help="Optional buyer prompt template path.")
    run.add_argument("--seller-model", help="Seller model whitelist alias (LLM mode).")
    run.add_argument("--buyer-model", help="Buyer model whitelist alias (LLM mode).")
    run.add_argument(
        "--db-path", default=None,
        help="SQLite output path (default: results/runs.db).",
    )

    experiment = commands.add_parser("experiment", help="Run a batch experiment configuration.")
    experiment.add_argument("--config", required=True, help="Experiment configuration JSON path.")
    experiment.add_argument("--db-path", default=None, help="SQLite output path.")
    experiment.add_argument("--seller-prompt", help="Optional seller prompt template path.")
    experiment.add_argument("--buyer-prompt", help="Optional buyer prompt template path.")
    experiment.add_argument("--seller-model", help="Seller model whitelist alias for LLM runs.")
    experiment.add_argument("--buyer-model", help="Buyer model whitelist alias for LLM runs.")

    compare = commands.add_parser(
        "compare", help="Run and compare two single-condition experiments."
    )
    compare.add_argument(
        "--config-a", required=True,
        help="Baseline experiment configuration JSON path.",
    )
    compare.add_argument(
        "--config-b", required=True,
        help="Treatment experiment configuration JSON path.",
    )
    compare.add_argument("--db-path", default=None, help="SQLite output path.")

    metrics = commands.add_parser("metrics", help="Calculate metrics from persisted runs.")
    selection = metrics.add_mutually_exclusive_group(required=True)
    selection.add_argument("--run-id", help="Calculate metrics for one persisted run.")
    selection.add_argument("--condition-id", help="Calculate metrics for one experiment condition.")
    selection.add_argument(
        "--all", action="store_true",
        help="Calculate metrics for every condition.",
    )
    metrics.add_argument(
        "--db-path", default=None,
        help="SQLite input path (default: results/runs.db).",
    )

    failures = commands.add_parser(
        "failures", help="Generate a structured failure report for one run."
    )
    failures.add_argument("--run-id", required=True, help="Persisted run ID.")
    failures.add_argument("--db-path", default=None, help="SQLite input path.")

    replay = commands.add_parser("replay", help="Replay one persisted run without an LLM.")
    replay.add_argument("--run-id", required=True, help="Persisted run ID.")
    replay.add_argument("--db-path", default=None, help="SQLite input path.")

    export = commands.add_parser("export", help="Export one run's event log as JSONL.")
    export.add_argument("--run-id", required=True, help="Persisted run ID.")
    export.add_argument("--output", required=True, help="Destination JSONL file.")
    export.add_argument("--db-path", default=None, help="SQLite input path.")

    return parser


def _run(args: argparse.Namespace) -> int:
    scenario = load_scenario(args.scenario)
    model_config = None
    if args.agent == "scripted":
        if args.seller_model is not None or args.buyer_model is not None:
            raise ValueError("MODEL_SELECTION_REQUIRES_LLM")
        print(
            f"Loaded scenario: {scenario['scenario_id']} "
            f"with {len(scenario['catalog'])} catalog items"
        )
        return 0
    if args.agent == "llm":
        models, registry_version = resolve_models(args.seller_model, args.buyer_model)
        adapters, model_config = create_role_adapters(models, registry_version)
    else:
        if args.seller_model is not None or args.buyer_model is not None:
            raise ValueError("MODEL_SELECTION_REQUIRES_LLM")
        adapters = None
    use_mock = args.agent == "mock" or adapters is None
    if use_mock:
        adapters = default_mock_adapters()
    engine = run_llm_negotiation(
        scenario,
        adapters,
        db_path=args.db_path or "results/runs.db",
        mock_demo=use_mock,
        seller_prompt_path=args.seller_prompt,
        buyer_prompt_path=args.buyer_prompt,
        model_config=model_config,
    )
    print(f"Completed negotiation: {engine.state.outcome.status} (run_id: {engine.run_id})")
    return 0


def _events_from_run(run: dict) -> tuple[Event, ...]:
    return tuple(
        Event(**{key: value for key, value in event.items() if key != "run_id"})
        for event in run["events"]
    )


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    # Keep the pre-T-701 ``--scenario ...`` form working for existing scripts.
    if raw_args and raw_args[0].startswith("-") and "--scenario" in raw_args:
        raw_args.insert(0, "run")
    args = _parser().parse_args(raw_args)
    database = args.db_path or "results/runs.db"
    try:
        if args.command == "run":
            return _run(args)
        if args.command == "experiment":
            runner = ExperimentRunner(
                args.config,
                db_path=database,
                seller_prompt_path=args.seller_prompt,
                buyer_prompt_path=args.buyer_prompt,
                seller_model=args.seller_model,
                buyer_model=args.buyer_model,
            )
            results = runner.run()
            print(f"Completed {len(results)} experiment runs")
            return 0
        if args.command == "compare":
            print(compare_experiment_configs(args.config_a, args.config_b, db_path=database))
            return 0
        if args.command == "metrics":
            output = calculate_metrics(
                database,
                condition_id=args.condition_id,
                run_id=args.run_id,
            )
            print(json.dumps(output, indent=2, sort_keys=True))
            return 0
        if args.command == "failures":
            print(format_failure_report(generate_failure_report(args.run_id, database)))
            return 0
        if args.command == "replay":
            state = replay_run(args.run_id, database)
            status = state.outcome.status if state.outcome else "IN_PROGRESS"
            print(f"Replay successful: {args.run_id} ({status})")
            return 0
        if args.command == "export":
            run = load_run_from_sqlite(args.run_id, database)
            export_run_to_jsonl(args.run_id, _events_from_run(run), Path(args.output))
            print(f"Exported {len(run['events'])} events to {args.output}")
            return 0
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
