# Multi-Good LLM Bargaining Platform

## 1. Purpose

Build a repeatable platform in which two LLM agents negotiate over multiple products and linked commitments in a finite number of turns. When the buyer resists Product A's standalone price, the seller may propose an A+B package with a discount on A that applies only if the accepted agreement also includes B.

The same engine should support both ordinary merchandise and the proposal's government–firm scenario. Represent a product purchase as quantity and price; represent non-product issues as typed terms such as a gas-price stability period, volume or investment commitment, land, infrastructure support, or subsidy. Start with a backend simulation runner; add a user interface after the negotiation engine works.

## 2. Design decisions

1. **One authoritative orchestrator:** it owns the complete state and calls one agent at a time. Agents return actions; they never edit state directly.
2. **Private role contexts:** each agent sees its own values and constraints, public scenario data, and the public transcript. It never sees the opponent's private profile or decision notes.
3. **Bundles are proposals, not a separate action:** a proposal contains one or more typed terms and conditions. Single-item and multi-item negotiations use the same protocol.
4. **Atomic acceptance in the MVP:** accepting an offer accepts all its terms and conditions. No partial acceptance or later interpretation of natural-language conditions.
5. **Bounded resource catalog:** agents may combine approved resources but cannot invent an unlisted product, subsidy, commitment, or policy exception.
6. **Hard constraints are enforced in code:** prompts describe them, but deterministic validation enforces inventory, allowed terms, price floors, and bundle conditions.

This extends the paper's alternating-offer setup and separate agent information, replacing the single price with a typed multi-term package (LLMs at the Bargaining Table, Sections 2–3).

## 3. Architecture

~~~mermaid
sequenceDiagram
    participant R as Experiment Runner
    participant O as Orchestrator
    participant S as Seller Agent
    participant B as Buyer Agent
    R->>O: Scenario, private profiles, run config
    O->>S: Seller view and public transcript
    S-->>O: Structured proposal
    O->>O: Parse, validate, score, append public action
    O->>B: Buyer view and public transcript
    B-->>O: Accept, counteroffer, or walk away
    O->>O: Validate transition and update active offer
    O-->>R: Outcome and audit log
~~~

Keep the first version synchronous and sequential. There is no need for a message queue because the next call depends on the preceding action. An optional API can wrap the runner later.

## 4. Core data model

Keep public scenario data separate from private agent profiles.

- **Scenario:** ID/version, roles, starting proposer, public catalog, permitted terms, information visibility, bundle rules, and maximum rounds.
- **Item or issue:** stable ID, type, unit, valid range, quantity/capacity, and allowed proposer. A product has stock and fulfillment-cost bounds. A commitment has explicit quantity, duration, and a validation rule.
- **Private role profile:** objective, reservation utility, per-item values or costs, issue weights, hard constraints, private information, and allowed actions.

Example:

~~~json
{
  "scenario_id": "shop-v1",
  "max_rounds": 8,
  "starting_role": "seller",
  "catalog": [
    {"id": "A", "type": "product", "unit": "piece", "seller_stock": 20},
    {"id": "B", "type": "product", "unit": "piece", "seller_stock": 20}
  ],
  "private_profiles": {
    "seller": {
      "unit_cost": {"A": 70, "B": 35},
      "minimum_unit_price": {"A": 80, "B": 40}
    },
    "buyer": {
      "unit_value": {"A": 130, "B": 75},
      "budget": 200
    }
  }
}
~~~

The orchestrator may load both profiles to validate and score a run; the prompt builder must filter them by role.

## 5. Conditional bundle representation

Represent discount conditions as data, never as free-form text that the engine must interpret.

Example: A costs 120 per unit and B costs 60. If the buyer purchases at least one B in this same all-or-nothing proposal, A receives a 20 per-unit discount. The effective total is 160.

~~~json
{
  "action": "PROPOSE",
  "proposal_id": "offer-03",
  "acceptance_mode": "all_or_nothing",
  "terms": [
    {"type": "purchase", "item_id": "A", "quantity": 1, "unit_price": 120},
    {"type": "purchase", "item_id": "B", "quantity": 1, "unit_price": 60}
  ],
  "conditions": [
    {
      "type": "discount_if_item_in_bundle",
      "discounted_item_id": "A",
      "discount_per_unit": 20,
      "required_item_id": "B",
      "minimum_required_quantity": 1
    }
  ],
  "decision_summary": "Discount A if the buyer also purchases B."
}
~~~

The offer evaluator should:

1. Verify each item exists, the seller may offer it, and requested quantities fit stock.
2. Verify each condition references terms in this proposal.
3. Compute discounts from typed rules and calculate effective line and total prices.
4. Reject discounts below the seller's item-level price floor or beyond a scenario-level cap.
5. Store original and effective prices in the audit record.

If the proposal is all-or-nothing, the buyer cannot accept A while dropping B. If the buyer wants A alone, the seller must make a separate proposal with its own price. Add partial acceptance only after defining how linked discounts are recalculated.

For bounded bargaining-chip composition, add a pairing library mapping approved resources to compatible commitments. Infrastructure support, for example, may pair with an investment or gas-volume commitment. An agent may select and combine valid templates but may not invent subsidy amounts or policy benefits outside configured limits.

## 6. Turn engine and validation

The game state should contain the round/current actor, active proposal ID and terms, public transcript, engine-only private profiles, outcome/termination status, and invalid-action/retry counts.

Recommended transition:

1. Seller makes the first proposal.
2. The other party may accept the complete active proposal, counter with a complete replacement, or walk away.
3. A counteroffer becomes the only active proposal and the other party acts next.
4. Acceptance is valid only for the current proposal ID and commits all terms together.
5. Stop on acceptance, walk-away, or the configured round limit.

Define one round as one valid proposal/response pair and persist action count as well. Treat each model call as untrusted input:

- Validate schema, role permissions, item IDs, quantities, units, allowed terms, and bundle conditions.
- Do not silently repair invalid proposals.
- Return a short machine-readable error and ask the same agent to correct invalid output without a retry limit. Proposal schema requires `decision_summary`.
- Log every attempt, including rejected actions, without exposing private constraints.

At final acceptance, check both roles' hard constraints and reservation utility. If acceptance violates them, do not commit the agreement; record the attempted violation and end as `NO_DEAL`. Never disclose the other party's private bound in an error message.

## 7. Utility and outcomes

The single-item utility model does not represent bundles by itself. Implement a scenario-specific evaluator over all accepted terms.

For merchandise:

- Buyer utility = value of received quantities − effective package payment.
- Seller utility = effective revenue − fulfillment cost.
- Keep each side's values, costs, budget, and reservation utility private.

For the government–firm scenario, use a configured multi-issue score. The firm may value stable, controllable gas costs and dislike policy-dependent pricing; the government may value investment, employment, gas consumption, and fair allocation. Give each role its own score function and hard constraints. Do not assume every commitment converts directly to cash.

A deal is valid only if all terms are legal, all bundle conditions hold, and both roles' agreement constraints are met. Report agreement/no-deal rates, joint and role-specific utility, constraint compliance, rounds, concession sizes/direction, bundle frequency/composition/discount, invalid actions, retries, and termination reasons.

Store scenario/model/prompt versions, model settings and seed if supported, profiles, public transcript, private decision summaries, parsed actions, validation results, computed utilities, and final outcome. SQLite is sufficient locally; JSONL export is useful for analysis.

## 8. Agent call flow

Build every request from canonical state rather than relying on implicit chat memory:

~~~python
def decide(role, state):
    view = make_private_view(role, state)
    prompt = render_prompt(role, view)
    raw = model_adapter.generate(prompt, action_schema=ACTION_SCHEMA)
    action = parse_action(raw)
    validate_action(role, action, state)
    return action
~~~

The private view contains the role's own profile, permitted public catalog information, current proposal, public transcript, and rounds remaining. It excludes the opponent's private profile, hidden costs/values, and private decision summaries. Append only public actions to both agents' shared history. Keep model generation and state mutation in separate modules.

## 9. Project structure

~~~text
bargaining_platform/
  scenario.py          # scenario and profile loading
  models.py            # Action, Offer, Term, GameState
  prompt_builder.py    # role-specific view and prompt
  model_adapter.py     # provider-neutral LLM call
  validator.py         # schemas, inventory, conditions, hard bounds
  utility.py           # role utility and agreement checks
  engine.py            # turn state machine
  experiments.py       # repeated controlled runs
  storage.py           # SQLite and JSONL export
  cli.py               # one negotiation or batch runner
  scenarios/
    shop_bundle.json
    government_firm.json
~~~

Start with Python and its standard library for the engine and SQLite. Add a provider SDK only for the LLM adapter. Use Pydantic or equivalent validation only if the chosen LLM interface does not already enforce the action schema. Add FastAPI and a UI only when a dashboard is needed.

## 10. Implementation phases

### Phase 1 — deterministic engine

- Define scenario, profile, term, action, offer, and outcome schemas.
- Implement turn transitions and hard-constraint validation.
- Use scripted agents to verify alternation, acceptance, counteroffers, and termination.

### Phase 2 — multi-item and bundle rules

- Add typed purchase terms and conditional discounts.
- Implement all-or-nothing acceptance and exact effective-price calculation.
- Test A being declined at its standalone price and an A+B offer activating the discount.
- Test missing B, nonexistent item, excess quantity, discount below seller floor, stale proposal ID, and partial acceptance of an all-or-nothing offer.

### Phase 3 — LLM integration

- Add separate role-specific contexts and a provider adapter.
- Request schema-constrained actions and concise decision summaries.
- Log raw output, parsed action, validation result, model version, and prompt version.
- Confirm hidden valuations and constraints never appear in the other agent's prompt.

### Phase 4 — repeated experiments

- Run controlled variations in model, role assignment, information, time pressure, and preferences.
- Compare a no-bundle baseline against bundle-enabled negotiations with fixed profiles.
- Repeat each condition and export the metrics above. The project proposal calls for repeated trials across models, roles, and information conditions.

### Phase 5 — scenario expansion and interface

- Encode government–firm issues as typed terms with private role-specific utility functions.
- Add bounded pairing-library rules for approved resource combinations.
- Add an API or dashboard only after batch runs are reliable.

## 11. Scenario data to complete

The project documents define gas-price stability, gas-volume commitments, investment, land, infrastructure support, subsidies, and fairness. They do not fully specify every scoring threshold, utility weight, or bundle policy; the extracted scoring table also contains omitted rows and ellipses. Put these values in versioned configuration and have the scenario owner complete them. Do not fill gaps with invented values.

Before a government–firm experiment, decide and encode gas-price tiers/units/duration; which concessions are legal for each role; links between gas-price terms and volume/investment/infrastructure; private reservation utilities and issue weights; discount, quantity, duration, and subsidy caps; and whether bundles are all-or-nothing or support partial acceptance.

## 12. Acceptance checklist

The MVP is ready when:

- A seller can offer A, then counter with an A+B package.
- The A discount applies only when the required B quantity is in the accepted package.
- A buyer can accept only the current proposal ID, and acceptance commits the full package.
- Inventory, price floors, permitted terms, and hard constraints are validated deterministically.
- Each prompt contains only that role's private profile and public negotiation history.
- Maximum-round termination and no-deal outcomes are recorded.
- Each run can be replayed from its versioned configuration and action log.
- Repeated runs produce agreement, utility, compliance, round, concession, and bundle-use metrics.

## Source basis

- Project goal, problem statement, and proposed interaction/evaluation platform: Proposal_extract.docx.
- Government–firm roles, objectives, scenario issues, and resource-pairing idea: Scenario-Update.docx.
- Alternating offers, private information, role-specific utility, and agent prompting: LLMs at the Bargaining Table, Sections 2–3.
