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
- **Fail-Fast on Conditions**: Before T-202, proposals containing conditions were rejected. T-202 now evaluates typed conditions and rejects any discount that cannot be calculated, exceeds the scenario cap, or violates the seller floor.
- **Action Permissions & Utilities**: Enforce each role's `allowed_actions` in the validator. At acceptance, the temporary purchase-only reservation check fails closed when utility cannot be checked; T-301 owns full utility calculation and metrics.
- **Exact Integer Math**: Prefer native Python `int` for integer-priced totals to avoid `Decimal` precision limits. Decimal-priced totals use an explicitly sized local `Decimal` context; do not rely on its default precision.

### Phase 2 Review Fixes (Post T-205)

- **JSON Money Serialization**: Because Python's `Decimal` is not JSON serializable, all financial amounts are serialized as `str` in JSON transcripts and exports via `MoneyJSONEncoder`, preserving exact precision.
- **Defensive Pure Functions**: Helper functions in `validator.py` perform structural checks on incoming terms before arithmetic and return controlled errors rather than raising unhandled exceptions.

### Phase 3 Decisions (Utility, Storage, and Replay)

- **Exact Utility Math**: Utility calculations must NEVER use floating-point arithmetic. Use `Fraction(Decimal(...))` to guarantee exact precision for buyer/seller/joint utilities, converting back to `Decimal` only at the final step.
- **Reservation Enforcement**: The engine must reject the final acceptance if either party's computed utility falls below their configured `reservation_utility`. Use the exact error code `RESERVATION_UTILITY_NOT_MET`.
- **Immutable Event Logging**: The `Event` dataclass is strictly immutable (`frozen=True`). The `EventLogger` must return a `tuple` of events, preventing external mutation. All logged structured data must pass through `json_safe()` before being stored.
- **Complete Run Persistence**: To enable deterministic replay, the `runs` table in SQLite MUST store the complete `scenario_json` alongside the run metadata and outcomes.
- **Deterministic Replay & Tamper Detection**: Replay must reconstruct the exact game state by reading the database action log, bypassing all LLMs/agents. It must throw `ReplayDivergenceError` if any state, effective price, or utility diverges from the original record.
- **JSONL Export Format**: JSONL exports must preserve exact Decimal precision (as strings) and explicitly use `newline="\n"` to ensure cross-platform compatibility.

### Phase 4 Decisions (LLM Integration)

- **Runner Isolation**: The end-to-end agent loop is orchestrated in `runner.py`, which chains `make_private_view -> render_prompt -> adapter.generate -> parse_action -> engine.step`. The engine remains completely agnostic to LLM implementations.
- **Deterministic Fallback & Mock Demo**: The CLI supports `--agent {scripted, llm, mock}`. If `LLM_API_KEY` is missing, the runner defaults to `MockLLMAdapter`. The mock demo uses an in-memory copy of the scenario with `reservation_utility` set to 0 to guarantee a happy-path `AGREEMENT` demonstration without mutating the base scenario file.
- **Metadata Logging**: Every LLM generation must log prompt text, model version, token usage, latency, and raw output into the EventLogger, without mutating the public transcript.
- **Parser Failure Handling**: Malformed LLM outputs are caught by `parse_action` and routed through the existing T-102 retry counter. No separate retry loop is allowed.
- **Database Schema Migration**: To support new columns (like `metadata_json`) while maintaining backward compatibility, schema migration (`_create_schema`) MUST be executed before reading persisted runs, ensuring pre-T-404 databases can still be replayed safely.
- **Replay Safety**: Replay logic must handle missing `parsed_action` fields gracefully for events that represent parser failures (e.g., `MALFORMED_JSON`).

### Phase 5 Decisions (Experiments and Evaluation)

- **Experiment Identity**: Condition IDs hash the canonical variation plus a config namespace, so identical overrides in different experiments remain distinct. Each repetition gets a unique run ID and is persisted separately.
- **Comparison Scope**: The compare command accepts exactly one variation per config and assigns separate baseline and treatment namespaces. It rejects larger matrices rather than silently omitting conditions from the report.
- **Persisted Metrics**: Aggregate metrics read SQLite runs and structured events; bundle prices are recomputed from the saved proposal and scenario instead of trusting pre-validation action payload prices. Average discount size means the mean per-unit discount on discounted lines.
- **Failure Analysis**: Failure reports classify structured validation errors and terminal actions, linking each finding to its event and round. Parser, hard-constraint, bundle, walk-away, and timeout failures are reported without interpreting model prose.

### Prompt Template Decisions (Post Phase 5)

- **Runtime Prompt Selection**: The run and experiment CLI commands accept independent `--seller-prompt` and `--buyer-prompt` file paths. The runner passes those paths to `render_prompt`, allowing any readable text template to be selected without changing the engine.
- **Fixed Default Templates**: When no path is supplied, each role uses its multi-product template. Single-product templates remain available by explicit path; catalog size does not select a template automatically.
- **Prompt Privacy Boundary**: Template variables come from the role-specific private view and are deep-copied and converted with `json_safe()` before rendering. A missing selected template raises `FileNotFoundError`.
