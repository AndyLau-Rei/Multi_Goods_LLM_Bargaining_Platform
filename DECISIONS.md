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

### Post-Phase 1 Review Fixes (T-201)

- **Strict Term Schema**: `Term` no longer accepts an arbitrary `details` dict. Only declared typed fields are accepted, and action validation checks them before a proposal becomes public to prevent privacy leaks.
- **Immutable Proposals**: The engine uses `copy.deepcopy()` when storing a validated proposal, so acceptance commits the stored snapshot rather than a caller-owned object that can change later.
- **Fail-Fast on Conditions**: Until T-202 is implemented, reject any proposal containing a condition. Do not accept a conditional discount without calculating and validating it.
- **Action Permissions & Utilities**: Enforce each role's `allowed_actions` in the validator. At acceptance, the temporary purchase-only reservation check fails closed when utility cannot be checked; T-301 owns full utility calculation and metrics.
- **Exact Integer Math**: Prefer native Python `int` for integer-priced totals to avoid `Decimal` precision limits. Decimal-priced totals use an explicitly sized local `Decimal` context; do not rely on its default precision.
