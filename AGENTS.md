# Project Agent Instructions

## Goal

Build a reproducible platform in which two LLM agents negotiate over multiple products and linked commitments for a finite number of turns. The seller must be able to offer a standalone product and then propose a conditional bundle, such as: buy product B and receive a discount on product A. The platform must support controlled experiments and the government–firm scenario described in the project proposal.

## Context

- The reference paper models alternating buyer–seller offers for one item. This project extends that pattern to multiple products, typed commitments, and conditional bundles.
- The proposal requires private valuations or priorities, hard constraints, bounded bargaining resources, limited rounds, complete transcripts, and repeated evaluation.
- Example government–firm issues include gas-price terms, gas-volume commitments, investment, land, infrastructure support, subsidies, and fairness constraints.
- The seller owns a bounded catalog and inventory. Agents may combine catalog entries and approved pairing rules, but may not invent products, benefits, quantities, or policy exceptions.
- The orchestrator is authoritative. Agents return structured actions; they never mutate the game state directly.

## Constraints

### Information and safety

- Keep private role profiles separate from the public scenario and transcript.
- Never expose an agent's hidden costs, values, reservation utility, private notes, or validation details to the opponent.
- Treat every model response as untrusted input. Validate schema, role permissions, item IDs, quantities, units, prices, conditions, inventory, and hard constraints in deterministic code.
- Do not silently repair an invalid action. Allow one configurable retry, then terminate with an explicit `INVALID_ACTION` outcome.
- Do not interpret bundle conditions from free-form prose. Conditions must be typed data.

### Negotiation rules

- The seller makes the first proposal; agents alternate turns.
- The MVP supports complete proposals, counteroffers, acceptance of the current proposal, and walk-away.
- Acceptance is atomic: it accepts every term and condition in the current proposal. Partial acceptance is disabled until its discount semantics are specified.
- A bundle discount applies only when its required item or commitment is present in the same accepted proposal and satisfies its minimum quantity.
- A discount may not violate the seller's item-level price floor, inventory, scenario cap, or legal constraints.
- Stop on agreement, walk-away, invalid-action policy, or maximum rounds. Record both round count and model-call count.
- Use versioned scenario configuration. Do not fill omitted proposal values, scoring rows, utility weights, or legal rules with guesses.

### Engineering scope

- Keep the first implementation synchronous and backend-first.
- Separate model generation, prompt construction, parsing, validation, state transitions, utility calculation, and storage.
- Prefer a small Python engine with SQLite and JSONL export. Add an API or UI after deterministic engine behavior is stable.
- Use structured output or function calling when supported by the selected model provider.
- Every run must record scenario, prompt, model, and configuration versions, public transcript, parsed actions, validation results, private decision summaries, outcome, and metrics.

## Acceptance

The implementation is acceptable when all of the following hold:

- A seller can offer product A and later counter with an A+B package.
- The A discount is applied only when the accepted package contains the required B quantity.
- A buyer can accept only the current proposal ID, and acceptance commits the full package atomically.
- Invalid items, quantities, prices, conditions, inventory, and hard-constraint violations are rejected deterministically.
- Each agent receives only its private profile, permitted public catalog data, current proposal, and public transcript.
- Maximum-round, walk-away, invalid-action, and no-deal outcomes are persisted.
- A completed run can be replayed from versioned configuration and its action log.
- Metrics include agreement rate, joint and role-specific utility, constraint compliance, rounds, concessions, bundle frequency, discount size, retries, and termination reason.
- A deterministic test suite covers normal trade, no-deal, bundle success, missing bundle item, invalid discount, stale proposal ID, excess quantity, and private-information leakage.
