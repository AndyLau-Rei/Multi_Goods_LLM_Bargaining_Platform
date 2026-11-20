const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

class Element {
  constructor(tag = "div") {
    this.tagName = tag;
    this.children = [];
    this.className = "";
    this.textContent = "";
    this.dataset = {};
    this.listeners = {};
    this.classList = { add() {}, remove() {} };
  }
  append(...elements) { this.children.push(...elements); }
  appendChild(element) { this.children.push(element); return element; }
  prepend(element) { this.children.unshift(element); }
  replaceChildren(...elements) { this.children = elements; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this[name] = value; }
  querySelector(selector) {
    const attr = selector.match(/^\[data-([\w-]+)\]$/)?.[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    if (!attr) return null;
    return this.children.find((element) => element.dataset[attr] !== undefined) || null;
  }
  querySelectorAll(selector) {
    const attr = selector.match(/^\[data-([\w-]+)\]$/)?.[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    if (!attr) return [];
    const found = [];
    const visit = (element) => {
      if (element.dataset[attr] !== undefined) found.push(element);
      element.children.forEach(visit);
    };
    this.children.forEach(visit);
    return found;
  }
  removeAttribute(name) { if (name === "title") delete this.title; }
}

const elements = new Map();
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  },
  createElement(tag) { return new Element(tag); },
};
const sandbox = {
  document,
  fetch: () => new Promise(() => {}),
  setInterval: () => 1,
  clearInterval() {},
  console,
};
vm.runInNewContext(fs.readFileSync("bargaining_platform/static/app.js", "utf8"), sandbox);
const render = vm.runInNewContext("renderEvents", sandbox);
const transcript = document.getElementById("transcript");
const proposal = {
  action_type: "PROPOSE",
  terms: [
    { item_id: "A", quantity: 2, unit: "box", unit_price: 100, effective_unit_price: 90 },
    { item_id: "B", quantity: 1, unit: "piece", unit_price: 50 },
  ],
  conditions: [{
    discounted_item_id: "A", discount_per_unit: 10,
    required_item_id: "B", minimum_required_quantity: 1,
  }],
};
const events = [
  { event_type: "PLATFORM_PROMPT", actor: "platform", recipient: "seller", message: "Please make the opening proposal.", round_number: 1 },
  { event_type: "PLATFORM_MESSAGE", actor: "platform", message: "The seller submitted a proposal. Buyer is reviewing it.", round_number: 1 },
  { event_id: 2, event_type: "SELLER_ACTION", actor: "seller", action_type: "PROPOSE", public_proposal: proposal, round_number: 1 },
  { event_type: "PLATFORM_PROMPT", actor: "platform", recipient: "buyer", message: "Please review the seller's proposal.", round_number: 1 },
  { event_id: 4, event_type: "BUYER_ACTION", actor: "buyer", action_type: "COUNTER", public_proposal: proposal, round_number: 1 },
  { event_type: "PLATFORM_MESSAGE", actor: "platform", message: "The buyer submitted a response. Seller is reviewing it.", round_number: 1 },
  { event_type: "PLATFORM_PROMPT", actor: "platform", recipient: "seller", message: "Please review the buyer's proposal.", round_number: 1 },
  { event_id: 6, event_type: "SELLER_ACTION", actor: "seller", action_type: "COUNTER", public_proposal: proposal, round_number: 1 },
];
render(events, 1, false);
render(events, 1, false, [
  { event_id: 2, actor: "seller", decision_summary: "seller's private rationale" },
  { event_id: 4, actor: "buyer", decision_summary: "buyer's private rationale" },
]);

const nodes = [];
const visit = (element) => { nodes.push(element); element.children.forEach(visit); };
visit(transcript);
const bubbles = nodes.filter((element) => element.tagName === "article");
assert.deepStrictEqual(bubbles.map((bubble) => bubble.children[0].textContent), ["S", "B", "S"]);
const messages = nodes.filter((element) => element.tagName === "p").map((element) => element.textContent);
assert(messages.includes("The seller submitted a proposal. Buyer is reviewing it."));
assert(messages.includes("The buyer submitted a response. Seller is reviewing it."));
assert(messages.includes("Please review the buyer's proposal."));
assert.strictEqual(transcript.children[0].children[0].textContent, "Platform prompt to seller");
assert.strictEqual(transcript.children[1].textContent, "The seller submitted a proposal. Buyer is reviewing it.");
assert.strictEqual(transcript.children[2].children[1].children[1].textContent,
  "A: 2 box at 100 per box (effective 90 per box)\nB: 1 piece at 50 per piece\nBundle condition: A receives 10 off per unit when B quantity is at least 1.");
assert(bubbles[0].className.includes("mr-auto"));
assert(bubbles[1].className.includes("ml-auto"));
const sellerOutput = bubbles[0].children[1];
const buyerOutput = bubbles[1].children[1];
const sellerSummary = sellerOutput.querySelector("[data-private-summary]");
const buyerSummary = buyerOutput.querySelector("[data-private-summary]");
assert.strictEqual(sellerOutput.dataset.eventId, "2");
assert.strictEqual(sellerSummary.textContent, "seller's private rationale");
assert.strictEqual(sellerSummary.role, "tooltip");
assert(sellerOutput.className.includes("group"));
assert(sellerSummary.className.includes("hidden"));
assert(sellerSummary.className.includes("group-hover:block"));
assert.strictEqual(buyerSummary.textContent, "buyer's private rationale");
assert.strictEqual(bubbles[2].children[1].querySelector("[data-private-summary]"), null);
assert.strictEqual(transcript.children[3].children[0].textContent, "Platform prompt to buyer");
assert.strictEqual(transcript.children[3].className.includes("bg-blue-50"), true);
assert.strictEqual(transcript.children[3].className.includes("text-center"), true);
assert.strictEqual(transcript.children[6].children[0].textContent, "Platform prompt to seller");
assert(!JSON.stringify(events).match(/decision_summary|seller's private rationale|buyer's private rationale|raw_output|proposal.?id|api.?key|provider/i));

transcript.replaceChildren();
vm.runInNewContext("lastEventCount = 0", sandbox);
render([
  { event_type: "PLATFORM_MESSAGE", actor: "platform", message: "The previous action was invalid. The originating agent is retrying.", round_number: 1 },
], 1, true);
const retryMessage = transcript.children.find((element) => element.textContent.startsWith("The previous action was invalid"));
assert(retryMessage);
assert(!retryMessage.textContent.includes("raw"));

transcript.replaceChildren();
vm.runInNewContext("lastEventCount = 0", sandbox);
render([{
  event_type: "SELLER_ACTION", actor: "seller", action_type: "PROPOSE",
  public_proposal: { action_type: "PROPOSE", terms: [{ item_id: "A", quantity: 1, unit: "piece", unit_price: 25 }], conditions: [] },
  round_number: 1,
}], 1, false);
assert.strictEqual(transcript.children[0].children[1].children[1].textContent, "A: 1 piece at 25 per piece");

console.log("viewer rendering tests passed");
