"""Run a single scenario from the command line."""

import argparse

from .scenario import load_scenario


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args()

    try:
        scenario = load_scenario(args.scenario)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"Loaded scenario: {scenario['scenario_id']} with {len(scenario['catalog'])} catalog items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
