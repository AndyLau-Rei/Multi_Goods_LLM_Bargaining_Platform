"""Run a single scenario from the command line."""

import argparse
import os

from .model_adapter import OpenAIAdapter
from .runner import default_mock_adapters, run_llm_negotiation
from .scenario import load_scenario


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--agent", choices=("scripted", "llm", "mock"), default="scripted")
    args = parser.parse_args()

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
        )
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Completed negotiation: {engine.state.outcome.status} (run_id: {engine.run_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
