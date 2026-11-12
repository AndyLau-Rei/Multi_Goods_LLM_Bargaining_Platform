"""Manage the negotiation turn state machine."""

from copy import deepcopy

from .models import Action, GameState, Outcome, ValidationResult, json_safe
from .validator import calculate_effective_totals, validate_action


class NegotiationEngine:
    def __init__(self, scenario: dict):
        if scenario["starting_role"] != "seller":
            raise ValueError("Seller must start the negotiation")
        self.max_rounds = scenario["max_rounds"]
        self.state = GameState(
            current_actor=scenario["starting_role"],
            round_number=1,
            catalog=scenario["catalog"],
            private_profiles=scenario["private_profiles"],
            max_rounds=scenario["max_rounds"],
            legal_ranges=scenario["legal_ranges"],
            bundle_rules=scenario["bundle_rules"],
        )
        self.action_count = 0

    def step(self, action: Action, role: str | None = None) -> GameState | ValidationResult:
        state = self.state
        if state.outcome is not None:
            raise RuntimeError("Negotiation has ended")

        actor = role if role is not None else (
            action.actor if isinstance(action, Action) and action.actor is not None else state.current_actor
        )
        result = validate_action(actor, action, state)
        state.private_attempt_log.append({
            "actor": actor,
            "action": json_safe(action.to_dict()) if isinstance(action, Action) else repr(action),
            "validation": result.to_dict(),
        })
        if not result.ok:
            state.invalid_attempts += 1
            if state.invalid_attempts > 1:
                state.outcome = Outcome("INVALID_ACTION")
            return result
        state.invalid_attempts = 0

        stored_proposal = None
        if action.action_type in ("PROPOSE", "COUNTER"):
            stored_proposal = deepcopy(action.proposal)
            stored_proposal.effective_prices = calculate_effective_totals(
                stored_proposal, state
            )["effective_prices"]

        public_action = json_safe(action.to_dict())
        if stored_proposal is not None:
            public_action["proposal"] = json_safe(stored_proposal.to_dict())
        if public_action["proposal"] is not None:
            public_action["proposal"].pop("decision_summary", None)
        public_action["actor"] = actor
        state.public_transcript.append(public_action)

        if action.action_type in ("PROPOSE", "COUNTER"):
            state.active_proposal = stored_proposal
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
