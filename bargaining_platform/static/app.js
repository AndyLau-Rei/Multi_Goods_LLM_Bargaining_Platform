const $ = (id) => document.getElementById(id);
let pollTimer;

function setMessage(text) { $("message").textContent = text || ""; }
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

function renderEvents(events, roundNumber) {
  const transcript = $("transcript");
  transcript.replaceChildren();
  const publicEvents = events.filter((event) => event.event_type === "ACTION_ATTEMPT" && event.public_transcript);
  events.forEach((event) => {
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
  if (!publicEvents.length) {
    const empty = document.createElement("p");
    empty.className = "text-center text-sm text-gray-400";
    empty.textContent = "No public actions yet.";
    transcript.prepend(empty);
  }
  $("metric-round").textContent = money(roundNumber);
}

async function refresh(runId) {
  const [eventsResponse, statusResponse] = await Promise.all([
    fetch(`/runs/${encodeURIComponent(runId)}/events`),
    fetch(`/runs/${encodeURIComponent(runId)}/status`),
  ]);
  if (!eventsResponse.ok || !statusResponse.ok) throw new Error("Run could not be loaded");
  const events = await eventsResponse.json();
  const status = await statusResponse.json();
  renderEvents(events.events || [], events.round_number);
  const outcome = status.outcome || {};
  $("metric-run").textContent = runId;
  const outcomeLabel = outcome.status || "IN_PROGRESS";
  $("metric-outcome").textContent = outcomeLabel;
  $("metric-outcome").className = `rounded-full px-3 py-1 text-xs font-semibold ${outcomeLabel === "AGREEMENT" ? "bg-green-50 text-green-700" : outcomeLabel === "IN_PROGRESS" ? "bg-gray-100 text-gray-500" : "bg-amber-50 text-amber-700"}`;
  const utilities = outcome.utility_metrics || {};
  $("metric-buyer").textContent = money(utilities.buyer_utility);
  $("metric-seller").textContent = money(utilities.seller_utility);
  $("metric-joint").textContent = money(utilities.joint_utility);
  return outcome.status && outcome.status !== "IN_PROGRESS";
}

async function watch(runId) {
  clearInterval(pollTimer);
  setMessage(`Loading run ${runId}…`);
  const tick = async () => {
    try {
      const done = await refresh(runId);
      setMessage(done ? `Run ${runId} complete.` : `Run ${runId} is in progress…`);
      if (done) clearInterval(pollTimer);
    } catch (error) { setMessage(error.message); clearInterval(pollTimer); }
  };
  await tick();
  if ($("metric-outcome").textContent === "IN_PROGRESS") pollTimer = setInterval(tick, 1000);
}

$("start").addEventListener("click", async () => {
  try {
    const response = await fetch("/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scenario_id: $("scenario").value, agent_type: $("agent").value }) });
    if (!response.ok) throw new Error("Could not start negotiation");
    const data = await response.json(); $("run-id").value = data.run_id; await watch(data.run_id);
  } catch (error) { setMessage(error.message); }
});
$("load").addEventListener("click", () => { const runId = $("run-id").value.trim(); if (runId) watch(runId); });
