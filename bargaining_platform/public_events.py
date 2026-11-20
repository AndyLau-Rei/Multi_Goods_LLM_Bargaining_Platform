"""Convert private event logs into the negotiation viewer's public event format."""

from copy import deepcopy

from .prompt_builder import render_platform_instruction


def public_events(events: list, transcript: list[dict], outcome: dict | None = None) -> list[dict]:
    """Build viewer events without exposing private action or provider records."""
    result = []
    actions = iter(transcript)
    result.append(_platform_prompt("seller", render_platform_instruction(), 1))
    invalid_by_role = {"seller": 0, "buyer": 0}

    for event in events:
        event_type = _value(event, "event_type")
        actor = _value(event, "actor")
        round_number = _value(event, "round_number")
        if event_type == "ACTION_ATTEMPT":
            validation = _value(event, "validation_result", {}) or {}
            if not validation.get("ok"):
                invalid_by_role[actor] = invalid_by_role.get(actor, 0) + 1
                if invalid_by_role[actor] < 3:
                    result.append(_platform(
                        "The previous action was invalid. The originating agent is retrying.",
                        round_number,
                    ))
                continue
            invalid_by_role[actor] = 0
            action = next(actions)
            action_type = action.get("action_type")
            record = {
                "event_id": _value(event, "event_id"),
                "event_type": "SELLER_ACTION" if actor == "seller" else "BUYER_ACTION",
                "actor": actor,
                "action_type": action_type,
                "round_number": round_number,
            }
            proposal = action.get("proposal")
            if action_type in {"PROPOSE", "COUNTER"} and isinstance(proposal, dict):
                record["public_proposal"] = _public_proposal(action_type, proposal)
            result.append(record)
            if action_type in {"PROPOSE", "COUNTER"}:
                public_action = {"action_type": action_type, "proposal": deepcopy(proposal)}
                public_action["proposal"].pop("proposal_id", None)
                public_action["proposal"].pop("decision_summary", None)
                instruction = render_platform_instruction(public_action)
                if actor == "seller":
                    result.extend((
                        _platform("The seller submitted a proposal. Buyer is reviewing it.", round_number),
                        _platform_prompt(
                            "buyer",
                            instruction,
                            round_number,
                        ),
                    ))
                else:
                    result.extend((
                        _platform("The buyer submitted a response. Seller is reviewing it.", round_number),
                        _platform_prompt(
                            "seller",
                            instruction,
                            round_number,
                        ),
                    ))
            elif action_type == "ACCEPT":
                result.append(_platform("The proposal was accepted.", round_number))
            elif action_type == "WALK_AWAY":
                result.append(_platform("The negotiation ended without agreement.", round_number))
        elif event_type == "STATE_TRANSITION":
            result.append({
                "event_type": "STATE_TRANSITION",
                "actor": actor,
                "state_transition_summary": _value(event, "state_transition_summary"),
                "round_number": round_number,
            })

    if outcome and outcome.get("status") not in {"AGREEMENT", "IN_PROGRESS"}:
        if outcome.get("status") in {"NO_DEAL", "MAX_ROUNDS", "INVALID_ACTION"}:
            last = result[-1] if result else {}
            if not (last.get("event_type") == "PLATFORM_MESSAGE" and last.get("message") == "The negotiation ended without agreement."):
                result.append(_platform("The negotiation ended without agreement.", last.get("round_number", 1)))
    return result


def _public_proposal(action_type: str, proposal: dict) -> dict:
    public = {"action_type": action_type, "terms": [], "conditions": []}
    effective = proposal.get("effective_prices") or {}
    for term in proposal.get("terms", []):
        clean = {key: deepcopy(term[key]) for key in ("item_id", "quantity", "unit", "unit_price") if key in term}
        effective_price = effective.get(term.get("item_id"))
        if effective_price is not None and effective_price != term.get("unit_price"):
            clean["effective_unit_price"] = deepcopy(effective_price)
        public["terms"].append(clean)
    for condition in proposal.get("conditions", []):
        public["conditions"].append({
            key: deepcopy(condition[key])
            for key in ("discounted_item_id", "discount_per_unit", "required_item_id", "minimum_required_quantity")
            if key in condition
        })
    return public


def _platform(message: str, round_number: int) -> dict:
    return {
        "event_type": "PLATFORM_MESSAGE",
        "actor": "platform",
        "message": message,
        "round_number": round_number,
    }


def _platform_prompt(recipient: str, message: str, round_number: int) -> dict:
    return {
        "event_type": "PLATFORM_PROMPT",
        "actor": "platform",
        "recipient": recipient,
        "message": message,
        "round_number": round_number,
    }


def _value(event, key: str, default=None):
    return event.get(key, default) if isinstance(event, dict) else getattr(event, key, default)
