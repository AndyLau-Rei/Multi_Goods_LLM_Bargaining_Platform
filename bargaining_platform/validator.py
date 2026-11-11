"""Validate schemas, inventory, conditions, and hard bounds."""

from .models import Action, GameState, ValidationResult


def validate_action(role: str, action: Action, state: GameState) -> ValidationResult:
    return ValidationResult(ok=True, message="stub")
