# DECISIONS.md

## Project identity
- Distribution: `bargaining-platform`
- Import: `bargaining_platform`
- Layout: flat package at repo root (no src/ layout), per Section 9
- Python: `requires-python = ">=3.10"`, developed on 3.11
- Build backend: setuptools (`build-backend = "setuptools.build_meta"`)
- Tests: pytest via `[project.optional-dependencies] dev`

## Ticket boundaries
- T-001: file loading + five-key presence check only. Must NOT validate catalog
  fields, item types, visibility, or bundle rules. T-002 will replace this gate.
- T-002: owns the versioned scenario schema and its rejection behavior.
- T-101: defines the state machine against a `validate_action(...) -> ValidationResult`
  interface whose body returns ok=True unconditionally. T-102 and T-103 replace it.
- T-201: multi-term line and total computation (no conditions).
- T-202: condition evaluation and discount application producing effective prices.
- T-203: atomicity — reject acceptance that drops a term or uses a stale proposal ID.

## Backlog (do not resolve before their phase)
- T-204 split, T-302/T-303 storage split, T-401 provider interface detail, T-602 blocking input.