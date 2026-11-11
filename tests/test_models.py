from bargaining_platform.models import (
    Action,
    Condition,
    GameState,
    Outcome,
    Proposal,
    Term,
    ValidationResult,
)


def test_proposal_round_trip_with_conditional_discount():
    proposal = Proposal(
        proposal_id="offer-03",
        acceptance_mode="all_or_nothing",
        terms=[
            Term(type="purchase", item_id="A", quantity=1, unit_price=120),
            Term(type="purchase", item_id="B", quantity=1, unit_price=60),
        ],
        conditions=[Condition("discount_if_item_in_bundle", "A", 20, "B", 1)],
        decision_summary="Discount A if B is purchased.",
        original_prices={"A": 120, "B": 60},
        effective_prices={"A": 100, "B": 60},
    )

    data = proposal.to_dict()
    assert Proposal.from_dict(data) == proposal
    assert Proposal.from_dict(data).to_dict() == data


def test_original_and_effective_prices_are_distinct():
    proposal = Proposal(
        "offer-03", "all_or_nothing", [Term("purchase", "A", 1, 120)], [], "",
        {"A": 120}, {"A": 100},
    )

    assert proposal.to_dict()["original_prices"]["A"] == 120
    assert proposal.to_dict()["effective_prices"]["A"] == 100


def test_nested_models_round_trip():
    proposal = Proposal("offer-03", "all_or_nothing", [], [], "", {}, {})
    action = Action("PROPOSE", proposal.proposal_id, proposal)
    state = GameState("buyer", 1, proposal, [action.to_dict()], Outcome("AGREEMENT", proposal.proposal_id))
    result = ValidationResult(False, "INVALID", "Rejected")

    assert Action.from_dict(action.to_dict()) == action
    assert GameState.from_dict(state.to_dict()) == state
    assert ValidationResult.from_dict(result.to_dict()) == result
