const $ = (id) => document.getElementById(id);
let pollTimer;
let lastEventId = 0;
let watchGeneration = 0;

async function loadScenarios() {
  const response = await fetch("/scenarios");
  if (!response.ok) throw new Error("Scenario list could not be loaded");
  const scenarios = await response.json();
  const select = $("scenario");
  select.replaceChildren();
  scenarios.forEach((scenario) => {
    const option = document.createElement("option");
    option.value = scenario.id;
    option.textContent = scenario.name;
    select.appendChild(option);
  });
}

async function loadModels() {
  const response = await fetch("/models");
  if (!response.ok) throw new Error("Model list could not be loaded");
  const data = await response.json();
  ["seller-model", "buyer-model"].forEach((id) => {
    const select = $(id);
    select.replaceChildren();
    data.models.forEach((model) => {
      const option = document.createElement("option");
      option.value = model.alias;
      option.textContent = `${model.provider} · ${model.alias}`;
      select.appendChild(option);
    });
    const role = id === "seller-model" ? "seller" : "buyer";
    select.value = data.default_models[role];
  });
}

function setMessage(text, isError = false) {
  const message = $("message");
  message.textContent = text || "";
  message.className = `mb-4 min-h-5 text-sm ${isError ? "text-red-600" : "text-gray-400"}`;
}
function money(value) { return value === undefined || value === null ? "—" : String(value); }

function proposalText(proposal) {
  if (!proposal) return "";
  const lines = (proposal.terms || []).map((term) => {
    const effective = term.effective_unit_price ?? proposal.effective_prices?.[term.item_id];
    return `${term.item_id}: ${term.quantity} × ${money(effective ?? term.unit_price)} effective`;
  });
  (proposal.conditions || []).forEach((condition) => {
    lines.push(`Condition: ${condition.discounted_item_id} discount ${money(condition.discount_per_unit)} if ${condition.required_item_id} ≥ ${condition.minimum_required_quantity}`);
  });
  return lines.join("\n");
}

function renderEvents(events, roundNumber, inProgress) {
  const transcript = $("transcript");
  transcript.querySelector("[data-thinking]")?.remove();
  const publicEvents = events.filter((event) => event.event_type === "ACTION_ATTEMPT" && event.public_transcript);
  events.forEach((event) => {
    if (event.event_id <= lastEventId) return;
    lastEventId = event.event_id;
    if (event.event_type === "STATE_TRANSITION" && event.state_transition_summary) {
      const system = document.createElement("p");
      system.className = "text-center text-xs text-gray-400";
      system.textContent = event.state_transition_summary;
      transcript.appendChild(system);
      return;
    }
    if (event.event_type !== "ACTION_ATTEMPT" || !event.public_transcript) return;
    const action = event.public_transcript || {};
    const bubble = document.createElement("article");
    bubble.className = `flex max-w-[88%] gap-3 ${event.actor === "buyer" ? "ml-auto flex-row-reverse" : "mr-auto"}`;
    const badge = document.createElement("span");
    badge.className = `mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${event.actor === "buyer" ? "bg-blue-100 text-blue-700" : "bg-gray-200 text-gray-600"}`;
    badge.textContent = event.actor === "buyer" ? "B" : "S";
    const content = document.createElement("div");
    content.className = `rounded-2xl px-4 py-3 ${event.actor === "buyer" ? "rounded-tr-md border border-blue-100 bg-blue-50 text-gray-800" : "rounded-tl-md bg-gray-100 text-gray-800"}`;
    const title = document.createElement("strong");
    title.className = "mb-1 block text-xs font-semibold capitalize text-gray-500";
    title.textContent = `${event.actor || "actor"} · ${action.action_type || "action"}`;
    const body = document.createElement("pre");
    body.className = "m-0 whitespace-pre-wrap font-sans text-sm leading-6";
    body.textContent = action.proposal ? proposalText(action.proposal) : `Proposal: ${action.proposal_id || "—"}`;
    content.append(title, body);
    bubble.append(badge, content);
    transcript.appendChild(bubble);
  });
  if (publicEvents.length) {
    transcript.querySelector("[data-empty]")?.remove();
  } else if (!transcript.querySelector("[data-empty]")) {
    const empty = document.createElement("p");
    empty.dataset.empty = "";
    empty.className = "text-center text-sm text-gray-400";
    empty.textContent = "No public actions yet.";
    transcript.prepend(empty);
  }
  if (inProgress) {
    const thinking = document.createElement("p");
    thinking.dataset.thinking = "";
    thinking.className = "flex items-center justify-center gap-2 text-xs text-gray-400";
    thinking.innerHTML = '<span class="h-3 w-3 animate-spin rounded-full border-2 border-gray-200 border-t-gray-500" aria-hidden="true"></span>Agent is thinking...';
    transcript.appendChild(thinking);
  }
  $("metric-round").textContent = money(roundNumber);
}

async function refresh(runId, generation) {
  const [eventsResponse, statusResponse] = await Promise.all([
    fetch(`/runs/${encodeURIComponent(runId)}/events`),
    fetch(`/runs/${encodeURIComponent(runId)}/status`),
  ]);
  if (!eventsResponse.ok || !statusResponse.ok) throw new Error("Run could not be loaded");
  const events = await eventsResponse.json();
  const status = await statusResponse.json();
  if (generation !== watchGeneration) return false;
  const outcome = status.outcome || {};
  const outcomeLabel = status.status || outcome.status || "IN_PROGRESS";
  renderEvents(events.events || [], events.round_number, outcomeLabel === "IN_PROGRESS");
  $("metric-run").textContent = runId;
  $("metric-outcome").textContent = outcomeLabel;
  $("metric-outcome").className = `rounded-full px-3 py-1 text-xs font-semibold ${outcomeLabel === "AGREEMENT" ? "bg-green-50 text-green-700" : outcomeLabel === "IN_PROGRESS" ? "bg-gray-100 text-gray-500" : "bg-amber-50 text-amber-700"}`;
  const metrics = Object.values(status.metrics || {})[0] || {};
  $("metric-agreement").textContent = money(metrics.agreement_rate);
  $("metric-bundles").textContent = money(metrics.bundle_frequency);
  $("metric-average-rounds").textContent = money(metrics.average_rounds);
  return outcomeLabel;
}

async function watch(runId) {
  clearInterval(pollTimer);
  const generation = ++watchGeneration;
  lastEventId = 0;
  $("transcript").replaceChildren();
  renderEvents([], 1, true);
  setMessage(`Loading run ${runId}…`);
  let busy = false;
  let stopped = false;
  const tick = async () => {
    if (busy || stopped) return;
    busy = true;
    try {
      const status = await refresh(runId, generation);
      if (generation !== watchGeneration) return;
      if (status === "ERROR") {
        setMessage("Negotiation failed due to a system error.", true);
      } else {
        setMessage(status === "IN_PROGRESS" ? `Run ${runId} is in progress…` : `Run ${runId} complete.`);
      }
      if (status !== "IN_PROGRESS") { stopped = true; clearInterval(pollTimer); }
    } catch (error) {
      if (generation !== watchGeneration) return;
      stopped = true;
      $("transcript").querySelector("[data-thinking]")?.remove();
      setMessage(error.message, true);
      clearInterval(pollTimer);
    }
    finally { busy = false; }
  };
  await tick();
  if (!stopped && generation === watchGeneration) pollTimer = setInterval(tick, 1000);
}

$("start").addEventListener("click", async () => {
  try {
    const request = { scenario_id: $("scenario").value, agent_type: $("agent").value };
    if (request.agent_type === "llm") {
      request.seller_model = $("seller-model").value;
      request.buyer_model = $("buyer-model").value;
    }
    const response = await fetch("/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) });
    if (!response.ok) throw new Error("Could not start negotiation");
    const data = await response.json(); $("run-id").value = data.run_id; await watch(data.run_id);
  } catch (error) { setMessage(error.message); }
});
$("load").addEventListener("click", () => { const runId = $("run-id").value.trim(); if (runId) watch(runId); });
loadModels().catch((error) => setMessage(error.message, true));
loadScenarios().catch((error) => setMessage(error.message, true));
