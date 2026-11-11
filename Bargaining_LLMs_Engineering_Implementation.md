# Engineering Implementation of LLM Bargaining

## Scope

This summarizes the single-item buyer–seller implementation in *LLMs at the Bargaining Table*. It does not cover multi-item bundles.

## Runtime

Run two separate Gemini 1.0 Ultra instances behind a negotiation orchestrator. The seller makes the first offer; buyer and seller then alternate until one accepts or either ends the conversation. Agents act without human intervention, and the interface is their only communication channel. (Section 1.1, p. 4; Section 3, p. 7)

The orchestrator owns the turn number, public transcript, current offer, and terminal outcome. Keep seller cost and buyer value in role-specific prompts. Configure whether each agent knows only its own type, both types are public, or the buyer additionally knows the seller's cost. (Sections 2–3, pp. 6–7; Section 5.2, p. 16)

## Prompt and action protocol

Prompts state each role's goal and reservation bound: the seller must not accept below cost; the buyer must not accept above value. They also specify the information condition, patience instruction, and required output format. Each model response includes a strategy in parentheses followed by one action: offer, counteroffer, accept, reject, or end conversation. The environment removes the parenthesized strategy before forwarding the action. (Section 3.1, pp. 8–10; Appendix B, pp. 22–28)

A minimal turn loop is:

1. Build the acting role's prompt from its private information and public history.
2. Call the model and parse its response.
3. Validate and append only the public action.
4. Switch roles; stop on acceptance or termination.
5. Record the deal indicator and price.

The paper specifies this interaction and message format, but not a production parser, invalid-output retry policy, API settings, or maximum-turn limit. Implementations must choose and log those settings.

## Experiment runner

For the uninformed, patient condition, test 10 buyer values and 10 seller costs from 1000 to 1900, with 10 independent conversations per pair. For the patience study, fix seller cost at 1000 and vary buyer value from 1000 to 2900; vary patience through natural-language prompt instructions rather than explicit discount-factor inputs. The asymmetric-information condition gives the buyer the seller's cost. (Section 4, pp. 11–12; Section 5.1–5.2, pp. 14–16)

## Evaluation

Compute gains from trade as GFT = x(v − c), where x indicates whether trade occurs. Compute normalized price bias as NPB = (p − (v + c)/2)/(v − c); positive bias favors the seller. Track individual rationality, which requires c ≤ p ≤ v. (Section 2, p. 7)

## Reproduction boundary

The paper provides the game model, prompts, action format, information conditions, and experiment settings, but no complete software implementation. Reported behavior is specific to Gemini 1.0 Ultra and is presented as proof of concept. (Sections 1.1–1.2, p. 4)