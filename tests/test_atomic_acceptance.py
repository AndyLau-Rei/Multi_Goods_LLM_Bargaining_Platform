from copy import deepcopy

import pytest

from bargaining_platform.engine import NegotiationEngine
from bargaining_platform.models import Action, Condition, Proposal, Term
from bargaining_platform.scenario import load_scenario


def _engine() -> NegotiationEngine:
    scenario = load_scenario("scenarios/shop_bundle.json")
    scenario["private_profiles"]["seller"]["reservation_utility"] = 0
    scenario["private_profiles"]["buyer"]["reservation_utility"] = 0
    return NegotiationEngine(scenario)


def _bundle() -> Proposal:
    return Proposal(
        "bundle-1",
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


@pytest.mark.parametrize("change", ["remove", "inject"])
def test_accepting_with_modified_bundle_is_rejected(change):
    engine = _engine()
    proposal = _bundle()
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))
    modified = deepcopy(engine.state.active_proposal)
    if change == "remove":
        modified.terms = modified.terms[:1]
    else:
        modified.terms.append(Term("purchase", "C", 1, 10, unit="piece"))

    result = engine.step(Action("ACCEPT", proposal.proposal_id, modified))

    assert result.error_code == "MUTATION_ON_ACCEPT"
    assert engine.state.outcome is None
    assert [term.item_id for term in engine.state.active_proposal.terms] == ["A", "B"]


def test_valid_atomic_acceptance():
    engine = _engine()
    proposal = _bundle()
    engine.step(Action("PROPOSE", proposal.proposal_id, proposal))

    state = engine.step(Action("ACCEPT", proposal.proposal_id))

    assert state.outcome.status == "AGREEMENT"
    assert state.outcome.final_proposal_id == proposal.proposal_id
    assert [term.item_id for term in state.active_proposal.terms] == ["A", "B"]
    assert state.active_proposal.effective_prices == {"A": 100, "B": 60}
