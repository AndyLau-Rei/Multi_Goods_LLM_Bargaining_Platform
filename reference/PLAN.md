# Implementation Plan

This plan turns the platform requirements into small, reviewable tickets. Complete tickets in order within each phase. A later phase may start only when its prerequisite behavior is covered by tests.

## Phase 0 — Project skeleton and decisions

### T-001 — Create the repository structure

Create the Python package, test package, scenario directory, CLI entry point, and configuration directory. Add a short README with local run commands.

**Done when:** the project installs or runs locally and an empty scenario can be loaded.

### T-002 — Define the scenario configuration schema

Define versioned configuration for roles, catalog entries, public information, private profiles, legal terms, bundle rules, starting role, and maximum rounds.

**Done when:** the schema rejects missing IDs and ambiguous visibility, and a sample shop scenario loads successfully.

### T-003 — Define the domain models

Implement typed models for `Term`, `Condition`, `Proposal`, `Action`, `GameState`, `ValidationResult`, and `Outcome`.

**Done when:** models serialize and deserialize deterministically and distinguish original from effective prices.

## Phase 1 — Deterministic negotiation engine

### T-101 — Implement the state machine

Implement seller-first alternating turns, active proposal replacement, atomic acceptance, walk-away, maximum rounds, and terminal outcomes.

**Done when:** scripted agents can complete an agreement, counter repeatedly, walk away, and time out.

### T-102 — Implement action validation

Validate action type, actor, proposal ID, item IDs, quantities, units, permitted terms, and turn ownership. Add one retry and `INVALID_ACTION` termination.

**Done when:** invalid actions never mutate authoritative state and every attempt is logged.

### T-103 — Implement inventory and hard constraints

Validate stock, quantity limits, price floors, budgets, reservation utilities, legal ranges, and role ownership of resources.

**Done when:** each rejection has a stable machine-readable reason and does not reveal the opponent's private bound.

### T-104 — Implement public and private views

Build role-specific state views containing only allowed private data, public catalog data, the active proposal, remaining rounds, and the public transcript.

**Done when:** tests prove that seller-only and buyer-only values cannot enter the other prompt.

## Phase 2 — Multi-product and conditional bundles

### T-201 — Add typed purchase terms

Support product ID, quantity, unit price, line total, inventory, and seller cost/floor metadata. Keep product terms separate from non-monetary commitments.

**Done when:** a proposal can contain multiple products and effective totals are calculated exactly.

### T-202 — Add conditional discount rules

Implement `discount_if_item_in_bundle` with required item ID, minimum quantity, discount amount, and optional scenario cap.

**Done when:** a discount activates only when its condition is satisfied in the same proposal.

### T-203 — Enforce atomic bundle acceptance

Ensure an all-or-nothing proposal cannot be accepted with a term removed. Reject conditions referencing absent terms or unavailable items.

**Done when:** A-only acceptance of an A+B offer is rejected and a valid A+B acceptance commits both terms.

### T-204 — Add bounded pairing rules

Create a catalog-backed pairing library for approved resource combinations, such as infrastructure support paired with investment or gas-volume commitments.

**Done when:** agents can select valid pairings and cannot create unlisted benefits, quantities, or policy exceptions.

### T-205 — Add bundle regression tests

Cover missing B, nonexistent item, excess quantity, discount below seller floor, scenario-cap violation, stale proposal ID, duplicate conditions, and zero/negative quantities.

**Done when:** all edge cases produce deterministic validation results.

## Phase 3 — Utility, storage, and replay

### T-301 — Implement role-specific utility functions

Implement merchandise utility and an extension point for multi-issue government–firm scoring. Keep values and weights private to the evaluator and matching role.

**Done when:** accepted packages produce buyer utility, seller utility, joint utility, and reservation checks.

### T-302 — Implement event logging

Persist raw model output, parsed action, public action, validation result, state transition, private decision summary, and timestamp per attempt.

**Done when:** a run has a complete append-only event log.

### T-303 — Implement SQLite and JSONL export

Store scenario/configuration versions, model settings, transcripts, outcomes, metrics, and termination reasons. Add an export command.

**Done when:** an experiment can be exported without losing bundle conditions or validation details.

### T-304 — Implement deterministic replay

Replay a run from its scenario version and action log without calling an LLM. Detect divergence between recorded and recomputed state.

**Done when:** replay reproduces the original proposal transitions, effective prices, outcome, and utility.

## Phase 4 — LLM integration

### T-401 — Implement the provider-neutral model adapter

Define a provider interface for structured generation, model/version metadata, timeout, retry, and usage logging. Add one concrete provider implementation.

**Done when:** the engine can replace a scripted agent with an LLM agent without changing state-machine code.

### T-402 — Write role-specific prompts

Create seller and buyer prompts covering objectives, private constraints, visible information, permitted actions, bundle semantics, remaining rounds, and structured output requirements.

**Done when:** prompts are generated from canonical state and contain no opponent-private data.

### T-403 — Parse and validate structured model actions

Use provider-native structured output where available. Otherwise parse JSON, reject extra fields as configured, and return machine-readable validation errors for the retry.

**Done when:** malformed output cannot commit state and is visible in the event log.

### T-404 — Run an end-to-end LLM negotiation

Run two independent role contexts through the engine on the sample A+B scenario. Capture model, prompt, action, validation, and outcome metadata.

**Done when:** one complete run can be inspected and replayed without exposing hidden values in the opponent context.

## Phase 5 — Experiments and evaluation

### T-501 — Implement the experiment matrix

Run repeated trials across models, role assignments, information conditions, time pressure, bundle availability, and valuation settings.

**Done when:** each condition has a stable ID, fixed configuration, repetition count, and exportable results.

### T-502 — Implement outcome metrics

Compute agreement rate, no-deal rate, joint and role utility, hard-constraint compliance, rounds, concessions, bundle frequency, composition, discount size, retries, and termination causes.

**Done when:** metrics are computed from persisted events rather than model text.

### T-503 — Add baseline and ablation runs

Compare a single-product baseline with bundle-enabled negotiation while holding profiles and model settings fixed. Add bundle-rule and information-condition ablations.

**Done when:** reports identify which design choice each comparison tests.

### T-504 — Add failure analysis reports

Group invalid actions, missed bundle conditions, irrational acceptance, premature walk-away, timeout, and constraint violations by model and scenario.

**Done when:** each failure links to the exact action and validation event that caused it.

## Phase 6 — Government–firm scenario

### T-601 — Encode scenario terms

Add typed gas-price terms, stability periods, gas-volume commitments, investment, land, infrastructure support, subsidies, and fairness rules.

**Done when:** every term has an owner, legal range, unit, duration semantics, visibility, and validation rule.

### T-602 — Complete missing policy and utility parameters

Obtain and version the omitted scoring rows, price tiers, issue weights, reservation utilities, and legal caps. Do not infer missing values from the documents.

**Done when:** the scenario can run without placeholders or undocumented assumptions.

### T-603 — Add government and firm prompt profiles

Encode the firm's stability and cost-control priorities and the government's investment, employment, resource-allocation, and fairness priorities.

**Done when:** each role has explicit hard constraints, soft preferences, private information, and a utility function.

### T-604 — Validate government–firm bundle experiments

Run controlled tests for price-plus-commitment packages, infrastructure-plus-investment pairings, and no-deal cases caused by incompatible hard constraints.

**Done when:** transcripts and metrics show which typed resources were exchanged and whether every condition was honored.

## Phase 7 — API and interface

### T-701 — Add a local CLI

Support running one scenario, a batch experiment, replay, and export from the command line.

**Done when:** a new user can run the sample scenario from documented commands.

### T-702 — Add a backend API

Expose scenario creation, run start, live event retrieval, outcome retrieval, and replay. Keep private data behind role-appropriate access controls.

**Done when:** API responses never return the other agent's private profile to a role-scoped client.

### T-703 — Add a negotiation viewer

Display active proposal, effective prices, bundle conditions, public transcript, round count, outcome, and metrics. Keep hidden reasoning and private profiles out of the opponent view.

**Done when:** a completed run can be reviewed from the persisted event log.

## Definition of done for the platform

- Deterministic tests pass for state transitions, bundles, constraints, privacy, replay, and termination.
- An A+B conditional discount is represented and validated as typed data.
- Two LLM agents can negotiate without direct access to each other's private state.
- Every run is versioned, auditable, exportable, and replayable.
- Baseline, bundle, information, and government–firm experiments produce comparable metrics.
- Documentation states every configurable assumption that is not specified by the source documents.
