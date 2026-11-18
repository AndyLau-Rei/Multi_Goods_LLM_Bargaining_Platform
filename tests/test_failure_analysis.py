from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.metrics import generate_failure_report
from bargaining_platform.models import Action, Condition, Proposal, Term
from bargaining_platform.scenario import load_scenario
from bargaining_platform.storage import save_run_to_sqlite


def _scenario(max_rounds=8):
    scenario = load_scenario("scenarios/shop_bundle.json")
    scenario["max_rounds"] = max_rounds
    scenario["private_profiles"]["buyer"]["reservation_utility"] = 0
    scenario["private_profiles"]["seller"]["reservation_utility"] = 0
    return scenario


def _proposal(proposal_id: str) -> Proposal:
    return Proposal(
        proposal_id,
        "all_or_nothing",
        [Term("purchase", "A", 1, 120, unit="piece")],
        [],
        "",
        {"A": 120},
        {"A": 120},
    )


def test_invalid_action_report_links_actor_and_error(tmp_path):
    scenario = _scenario()
    engine = NegotiationEngine(scenario)
    proposal = _proposal("failure-1")
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))
    engine.step(Action("ACCEPT", "stale-id"))
    engine.step(Action("ACCEPT", "stale-id"))
    save_run_to_sqlite(
        "invalid", scenario, engine.state.outcome,
        engine.event_logger.events, tmp_path / "runs.db",
    )

    report = generate_failure_report("invalid", tmp_path / "runs.db")

    assert report["outcome_status"] == "INVALID_ACTION"
    assert report["failures"][0]["actor"] == "buyer"
    assert report["failures"][0]["error_code"] == "STALE_PROPOSAL_ID"
    assert report["failures"][0]["event_id"] == 3


def test_max_rounds_report_identifies_timeout(tmp_path):
    scenario = _scenario(max_rounds=1)
    engine = NegotiationEngine(scenario)
    first = _proposal("first")
    counter = _proposal("counter")
    engine.step(Action("PROPOSE", first.proposal_id, first))
    engine.step(Action("COUNTER", first.proposal_id, counter))
    save_run_to_sqlite(
        "timeout", scenario, engine.state.outcome,
        engine.event_logger.events, tmp_path / "runs.db",
    )

    report = generate_failure_report("timeout", tmp_path / "runs.db")

    timeout = next(
        failure for failure in report["failures"]
        if failure["error_code"] == "MAX_ROUNDS"
    )
    assert report["outcome_status"] == "MAX_ROUNDS"
    assert timeout["actor"] == "buyer"
    assert timeout["event_type"] == "STATE_TRANSITION"


def test_walk_away_report_identifies_turn(tmp_path):
    scenario = _scenario()
    engine = NegotiationEngine(scenario)
    proposal = _proposal("walk-away")
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))
    engine.step(Action("WALK_AWAY"))
    save_run_to_sqlite(
        "walk-away", scenario, engine.state.outcome,
        engine.event_logger.events, tmp_path / "runs.db",
    )

    report = generate_failure_report("walk-away", tmp_path / "runs.db")

    walk_away = next(
        failure for failure in report["failures"]
        if failure["error_code"] == "WALK_AWAY"
    )
    assert report["outcome_status"] == "NO_DEAL"
    assert walk_away["actor"] == "buyer"
    assert walk_away["round"] == 1


def test_parser_errors_are_categorized(tmp_path):
    scenario = _scenario()
    engine = NegotiationEngine(scenario)
    engine.step("not valid json")
    save_run_to_sqlite(
        "parser", scenario, engine.state.outcome,
        engine.event_logger.events, tmp_path / "runs.db",
    )

    report = generate_failure_report("parser", tmp_path / "runs.db")

    failure = report["failures"][0]
    assert failure["error_code"] == "MALFORMED_JSON"
    assert failure["category"] == "parser_error"


def test_hard_constraint_errors_are_categorized(tmp_path):
    scenario = _scenario()
    scenario["private_profiles"]["buyer"]["budget"] = 50
    engine = NegotiationEngine(scenario)
    proposal = _proposal("budget")
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))
    engine.step(Action("ACCEPT", proposal.proposal_id))
    save_run_to_sqlite(
        "budget", scenario, engine.state.outcome,
        engine.event_logger.events, tmp_path / "runs.db",
    )

    report = generate_failure_report("budget", tmp_path / "runs.db")

    failure = report["failures"][0]
    assert failure["error_code"] == "BUDGET_EXCEEDED"
    assert failure["category"] == "hard_constraint"


def test_bundle_errors_are_categorized(tmp_path):
    scenario = _scenario()
    engine = NegotiationEngine(scenario)
    proposal = Proposal(
        "condition",
        "all_or_nothing",
        [
            Term("purchase", "A", 1, 120, unit="piece"),
            Term("purchase", "B", 1, 60, unit="piece"),
        ],
        [Condition("discount_if_item_in_bundle", "A", 20, "B", 2)],
        "",
        {"A": 120, "B": 60},
        {"A": 120, "B": 60},
    )
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))
    save_run_to_sqlite(
        "condition", scenario, engine.state.outcome,
        engine.event_logger.events, tmp_path / "runs.db",
    )

    report = generate_failure_report("condition", tmp_path / "runs.db")

    failure = report["failures"][0]
    assert failure["error_code"] == "CONDITION_NOT_SATISFIED"
    assert failure["category"] == "bundle_semantic"
