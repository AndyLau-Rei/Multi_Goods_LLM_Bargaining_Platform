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
  querySelector(selector) {
    const attr = selector.match(/^\[data-(\w+)\]$/)?.[1];
    if (!attr) return null;
    return this.children.find((element) => element.dataset[attr] !== undefined) || null;
  }
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
  { event_type: "PLATFORM_MESSAGE", actor: "platform", message: "The seller submitted a proposal. Buyer is reviewing it.", round_number: 1 },
  { event_type: "SELLER_ACTION", actor: "seller", action_type: "PROPOSE", public_proposal: proposal, round_number: 1 },
  { event_type: "PLATFORM_MESSAGE", actor: "platform", message: "Please review the seller's proposal.", round_number: 1 },
  { event_type: "BUYER_ACTION", actor: "buyer", action_type: "ACCEPT", round_number: 1 },
  { event_type: "SELLER_ACTION", actor: "seller", action_type: "WALK_AWAY", round_number: 2 },
];
render(events, 1, false);

const nodes = [];
const visit = (element) => { nodes.push(element); element.children.forEach(visit); };
visit(transcript);
const bubbles = nodes.filter((element) => element.tagName === "article");
assert.deepStrictEqual(bubbles.map((bubble) => bubble.children[0].textContent), ["S", "B", "S"]);
const messages = nodes.filter((element) => element.tagName === "p").map((element) => element.textContent);
assert(messages.includes("The seller submitted a proposal. Buyer is reviewing it."));
assert(messages.includes("Please review the seller's proposal."));
assert.strictEqual(transcript.children[0].textContent, "The seller submitted a proposal. Buyer is reviewing it.");
assert.strictEqual(transcript.children[1].children[1].children[1].textContent,
  "A: 2 box at 100 per box (effective 90 per box)\nB: 1 piece at 50 per piece\nBundle condition: A receives 10 off per unit when B quantity is at least 1.");
assert(bubbles[0].className.includes("mr-auto"));
assert(bubbles[1].className.includes("ml-auto"));
assert.strictEqual(transcript.children[3].children[1].children[1].textContent, "Buyer accepted the current proposal.");
assert.strictEqual(transcript.children[4].children[1].children[1].textContent, "Seller walked away from the negotiation.");
assert(!JSON.stringify(nodes.map((element) => element.textContent)).match(/decision_summary|raw_output|proposal.?id|private|api.?key|provider/i));

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
