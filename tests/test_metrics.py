from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.metrics import calculate_metrics
from bargaining_platform.models import Action, Condition, Proposal, Term
from bargaining_platform.scenario import load_scenario
from bargaining_platform.storage import save_run_to_sqlite


def _proposal() -> Proposal:
    return Proposal(
        "metric-offer",
        "all_or_nothing",
        [Term("purchase", "A", 1, 100, unit="piece")],
        [],
        "",
        {"A": 100},
        {"A": 100},
    )


def _bundle_proposal() -> Proposal:
    return Proposal(
        "bundle-offer",
        "all_or_nothing",
        [
            Term("purchase", "A", 1, 120, unit="piece"),
            Term("purchase", "B", 1, 60, unit="piece"),
        ],
        [Condition("discount_if_item_in_bundle", "A", 20, "B", 1)],
        "",
        {"A": 120, "B": 60},
        {"A": 120, "B": 60},
    )


def _scenario():
    scenario = load_scenario("scenarios/shop_bundle.json")
    scenario["private_profiles"]["buyer"]["reservation_utility"] = 0
    scenario["private_profiles"]["seller"]["reservation_utility"] = 0
    return scenario


def test_metrics_agreement_rate_gft_and_npb(tmp_path):
    scenario = _scenario()
    agreement = NegotiationEngine(scenario)
    proposal = _proposal()
    agreement.step(Action("PROPOSE", proposal.proposal_id, proposal))
    agreement.step(Action("ACCEPT", proposal.proposal_id))
    save_run_to_sqlite(
        "agreement", scenario, agreement.state.outcome,
        agreement.event_logger.events, tmp_path / "runs.db",
        condition_id="baseline",
    )

    no_deal = NegotiationEngine(scenario)
    no_deal.step(Action("PROPOSE", proposal.proposal_id, proposal))
    no_deal.step(Action("WALK_AWAY"))
    save_run_to_sqlite(
        "no-deal", scenario, no_deal.state.outcome,
        no_deal.event_logger.events, tmp_path / "runs.db",
        condition_id="baseline",
    )

    metrics = calculate_metrics(tmp_path / "runs.db")["baseline"]
    assert metrics["agreement_rate"] == 0.5
    assert metrics["no_deal_rate"] == 0.5
    assert metrics["gft"] == 60.0
    assert metrics["npb"] == 0.0
    assert metrics["ir"] == 1.0


def test_metrics_include_bundle_utilities_rounds_discount_and_retries(tmp_path):
    scenario = _scenario()
    engine = NegotiationEngine(scenario)
    proposal = _bundle_proposal()
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))
    invalid = engine.step(Action("ACCEPT", "stale-id"))
    assert invalid.error_code == "STALE_PROPOSAL_ID"
    engine.step(Action("ACCEPT", proposal.proposal_id))
    save_run_to_sqlite(
        "bundle-agreement",
        scenario,
        engine.state.outcome,
        engine.event_logger.events,
        tmp_path / "runs.db",
        condition_id="bundle",
    )

    metrics = calculate_metrics(tmp_path / "runs.db")["bundle"]
    assert metrics["agreement_rate"] == 1.0
    assert metrics["no_deal_rate"] == 0.0
    assert metrics["average_buyer_utility"] == 45.0
    assert metrics["average_seller_utility"] == 55.0
    assert metrics["average_joint_utility"] == 100.0
    assert metrics["average_rounds"] == 1.0
    assert metrics["bundle_frequency"] == 1.0
    assert metrics["average_discount_size"] == 20.0
    assert metrics["average_retries"] == 1.0
    assert metrics["gft"] == 100.0
    assert metrics["npb"] == 0.05
    assert metrics["ir"] == 1.0
