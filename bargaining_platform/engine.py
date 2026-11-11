"""Manage the negotiation turn state machine."""

from .models import Action, GameState, Outcome
from .validator import validate_action


class NegotiationEngine:
    def __init__(self, scenario: dict):
        if scenario["starting_role"] != "seller":
            raise ValueError("Seller must start the negotiation")
        self.max_rounds = scenario["max_rounds"]
        self.state = GameState(current_actor=scenario["starting_role"], round_number=1)
        self.action_count = 0

    def step(self, action: Action) -> GameState:
        state = self.state
        if state.outcome is not None:
            raise RuntimeError("Negotiation has ended")

        result = validate_action(state.current_actor, action, state)
        if not result.ok:
            raise ValueError(result.message or result.error_code)

        public_action = action.to_dict()
        if public_action["proposal"] is not None:
            public_action["proposal"].pop("decision_summary", None)
        public_action["actor"] = state.current_actor
        state.public_transcript.append(public_action)

        if action.action_type in ("PROPOSE", "COUNTER"):
            state.active_proposal = action.proposal
        elif action.action_type == "ACCEPT":
            state.outcome = Outcome("AGREEMENT", state.active_proposal.proposal_id)
        elif action.action_type == "WALK_AWAY":
            state.outcome = Outcome("NO_DEAL")

        self.action_count += 1
        if state.outcome is None:
            # A round contains a proposal and the other party's response.
            if self.action_count >= 2 * self.max_rounds:
                state.outcome = Outcome("MAX_ROUNDS")
            else:
                state.current_actor = "buyer" if state.current_actor == "seller" else "seller"
                state.round_number = 1 + self.action_count // 2
        return state
