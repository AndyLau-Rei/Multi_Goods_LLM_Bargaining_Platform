"""Manage the negotiation turn state machine."""

from copy import deepcopy

from .models import Action, GameState, Outcome, ValidationResult, json_safe
from .storage import EventLogger
from .utility import calculate_utilities, validate_reservation_utilities
from .validator import calculate_effective_totals, parse_action, validate_action


class NegotiationEngine:
    def __init__(self, scenario: dict, event_logger: EventLogger | None = None):
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
        self.event_logger = event_logger or EventLogger()

    def step(self, action: Action | str, role: str | None = None) -> GameState | ValidationResult:
        state = self.state
        if state.outcome is not None:
            raise RuntimeError("Negotiation has ended")

        actor = role if role is not None else (
            action.actor if isinstance(action, Action) and action.actor is not None else state.current_actor
        )
        raw_output = action if isinstance(action, str) else None
        if raw_output is not None:
            action = parse_action(raw_output, actor)
        result = action if isinstance(action, ValidationResult) else validate_action(actor, action, state)
        utility_metrics = None
        if result.ok and action.action_type == "ACCEPT":
            result = validate_reservation_utilities(state.active_proposal, state)
            if result.ok:
                utility_metrics = calculate_utilities(state.active_proposal, state)
        parsed_action = json_safe(action.to_dict()) if isinstance(action, Action) else None
        private_summary = None
        if isinstance(parsed_action, dict) and isinstance(parsed_action.get("proposal"), dict):
            private_summary = parsed_action["proposal"].pop("decision_summary", None)
        self.event_logger.append(
            "ACTION_ATTEMPT",
            actor,
            raw_output=raw_output,
            parsed_action=parsed_action,
            validation_result=result.to_dict(),
            private_decision_summary=private_summary,
        )
        state.private_attempt_log.append({
            "actor": actor,
            "action": json_safe(action.to_dict()) if isinstance(action, Action) else repr(action),
            "validation": result.to_dict(),
        })
        if not result.ok:
            state.invalid_attempts += 1
            if state.invalid_attempts > 1:
                state.outcome = Outcome("INVALID_ACTION")
                self.event_logger.append(
                    "STATE_TRANSITION",
                    actor,
                    state_transition_summary="Game ended: invalid action retry exhausted",
                )
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

        transition_summary = None
        if action.action_type in ("PROPOSE", "COUNTER"):
            state.active_proposal = stored_proposal
            transition_summary = "Proposal updated"
        elif action.action_type == "ACCEPT":
            state.outcome = Outcome(
                "AGREEMENT",
                state.active_proposal.proposal_id,
                utility_metrics,
            )
            transition_summary = "Proposal accepted; game ended"
        elif action.action_type == "WALK_AWAY":
            state.outcome = Outcome("NO_DEAL")
            transition_summary = "Game ended: walk away"

        self.action_count += 1
        if state.outcome is None:
            # A round contains a proposal and the other party's response.
            if self.action_count >= 2 * self.max_rounds:
                state.outcome = Outcome("MAX_ROUNDS")
                transition_summary = "Maximum rounds reached"
            else:
                state.current_actor = "buyer" if state.current_actor == "seller" else "seller"
                state.round_number = 1 + self.action_count // 2
                transition_summary += f"; turn switched to {state.current_actor}"
        self.event_logger.append(
            "STATE_TRANSITION",
            actor,
            state_transition_summary=transition_summary,
        )
        return state
