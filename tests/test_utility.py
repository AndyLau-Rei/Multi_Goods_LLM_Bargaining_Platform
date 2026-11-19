from decimal import Decimal

from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.models import Action, Condition, Proposal, Term
from bargaining_platform.scenario import load_scenario
from bargaining_platform.utility import calculate_utilities


def _proposal(bundle: bool = False) -> Proposal:
    terms = [Term("purchase", "A", 1, 120, unit="piece")]
    conditions = []
    if bundle:
        terms.append(Term("purchase", "B", 1, 60, unit="piece"))
        conditions.append(Condition("discount_if_item_in_bundle", "A", 20, "B", 1))
    prices = {term.item_id: term.unit_price for term in terms}
    return Proposal("utility-1", "all_or_nothing", terms, conditions, "", prices, prices.copy())


def _engine(buyer_reservation=0, seller_reservation=0) -> NegotiationEngine:
    scenario = load_scenario("scenarios/shop_bundle.json")
    scenario["private_profiles"]["buyer"]["reservation_utility"] = buyer_reservation
    scenario["private_profiles"]["seller"]["reservation_utility"] = seller_reservation
    return NegotiationEngine(scenario)


def test_merchandise_utility_calculation():
    engine = _engine()
    proposal = _proposal()
    proposal.terms[0].quantity = 2

    utilities = calculate_utilities(proposal, engine.state)

    assert utilities == {
        "buyer_utility": Decimal("20"),
        "seller_utility": Decimal("100"),
        "joint_utility": Decimal("120"),
    }


def test_bundle_utility_calculation():
    engine = _engine()

    utilities = calculate_utilities(_proposal(bundle=True), engine.state)

    assert utilities == {
        "buyer_utility": Decimal("45"),
        "seller_utility": Decimal("55"),
        "joint_utility": Decimal("100"),
    }


def test_reservation_utility_rejection():
    engine = _engine(buyer_reservation=50)
    proposal = _proposal()
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    result = engine.step(Action("ACCEPT", proposal.proposal_id))

    assert result.error_code == "RESERVATION_UTILITY_NOT_MET"
    assert engine.state.outcome.status == "NO_DEAL"
    assert len(engine.state.public_transcript) == 1


def test_valid_utility_acceptance():
    engine = _engine(buyer_reservation=40, seller_reservation=50)
    proposal = _proposal(bundle=True)
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    state = engine.step(Action("ACCEPT", proposal.proposal_id))

    assert state.outcome.status == "AGREEMENT"
    assert state.outcome.utility_metrics == {
        "buyer_utility": Decimal("45"),
        "seller_utility": Decimal("55"),
        "joint_utility": Decimal("100"),
    }


def test_large_integer_utility_remains_exact():
    engine = _engine()
    amount = 10**30
    engine.state.private_profiles["buyer"]["unit_value"]["A"] = amount + 10
    engine.state.private_profiles["seller"]["unit_cost"]["A"] = amount - 10
    proposal = _proposal()
    proposal.terms[0].unit_price = amount
    proposal.original_prices["A"] = amount
    proposal.effective_prices["A"] = amount

    assert calculate_utilities(proposal, engine.state) == {
        "buyer_utility": Decimal("10"),
        "seller_utility": Decimal("10"),
        "joint_utility": Decimal("20"),
    }
