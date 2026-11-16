"""Run a single scenario from the command line."""

import argparse
import json
import os
import sys

from .experiments import ExperimentRunner, compare_experiment_configs
from .metrics import calculate_metrics, format_failure_report, generate_failure_report
from .model_adapter import OpenAIAdapter
from .runner import default_mock_adapters, run_llm_negotiation
from .scenario import load_scenario


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "experiment":
        experiment_parser = argparse.ArgumentParser(prog="python -m bargaining_platform.cli experiment")
        experiment_parser.add_argument("--config", required=True)
        experiment_parser.add_argument("--db-path", default=None)
        experiment_parser.add_argument("--seller-prompt", default=None)
        experiment_parser.add_argument("--buyer-prompt", default=None)
        args = experiment_parser.parse_args(argv[1:])
        try:
            runner = ExperimentRunner(
                args.config,
                **{
                    key: value
                    for key, value in {
                        "db_path": args.db_path,
                        "seller_prompt_path": args.seller_prompt,
                        "buyer_prompt_path": args.buyer_prompt,
                    }.items()
                    if value
                },
            )
            results = runner.run()
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}")
            return 1
        print(f"Completed {len(results)} experiment runs")
        return 0

    if argv and argv[0] == "metrics":
        metrics_parser = argparse.ArgumentParser(prog="python -m bargaining_platform.cli metrics")
        selection = metrics_parser.add_mutually_exclusive_group(required=True)
        selection.add_argument("--condition-id")
        selection.add_argument("--all", action="store_true")
        metrics_parser.add_argument("--db-path", default=None)
        args = metrics_parser.parse_args(argv[1:])
        try:
            output = calculate_metrics(
                args.db_path if args.db_path else None,
                condition_id=args.condition_id,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}")
            return 1
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0

    if argv and argv[0] == "compare":
        compare_parser = argparse.ArgumentParser(prog="python -m bargaining_platform.cli compare")
        compare_parser.add_argument("--config-a", required=True)
        compare_parser.add_argument("--config-b", required=True)
        compare_parser.add_argument("--db-path", default=None)
        args = compare_parser.parse_args(argv[1:])
        try:
            report = compare_experiment_configs(
                args.config_a,
                args.config_b,
                db_path=args.db_path if args.db_path else None,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}")
            return 1
        print(report)
        return 0

    if argv and argv[0] == "failures":
        failures_parser = argparse.ArgumentParser(prog="python -m bargaining_platform.cli failures")
        failures_parser.add_argument("--run-id", required=True)
        failures_parser.add_argument("--db-path", default=None)
        args = failures_parser.parse_args(argv[1:])
        try:
            report = generate_failure_report(
                args.run_id,
                args.db_path if args.db_path else None,
            )
        except (OSError, KeyError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}")
            return 1
        print(format_failure_report(report))
        return 0

    if argv and argv[0] == "run":
        argv = argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--agent", choices=("scripted", "llm", "mock"), default="scripted")
    parser.add_argument("--seller-prompt", default=None)
    parser.add_argument("--buyer-prompt", default=None)
    args = parser.parse_args(argv)

    try:
        scenario = load_scenario(args.scenario)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    if args.agent == "scripted":
        print(f"Loaded scenario: {scenario['scenario_id']} with {len(scenario['catalog'])} catalog items")
        return 0

    if args.agent == "mock" or not os.getenv("LLM_API_KEY"):
        adapters = default_mock_adapters()
    else:
        adapter = OpenAIAdapter()
        adapters = {"seller": adapter, "buyer": adapter}
    try:
        engine = run_llm_negotiation(
            scenario,
            adapters,
            mock_demo=args.agent == "mock" or not os.getenv("LLM_API_KEY"),
            seller_prompt_path=args.seller_prompt,
            buyer_prompt_path=args.buyer_prompt,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Completed negotiation: {engine.state.outcome.status} (run_id: {engine.run_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
