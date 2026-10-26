// Route three requests with model "auto" and show what SmartRouter decided.
//
//   npm i openai
//   SMARTROUTER_KEY=tsr_... node examples/node/route.mjs
import OpenAI from "openai";

const baseURL = process.env.SMARTROUTER_URL ?? "http://localhost:4230/v1";
const apiKey = process.env.SMARTROUTER_KEY;
if (!apiKey) throw new Error("set SMARTROUTER_KEY (make demo prints one)");

const prompts = [
  "Classify the sentiment as positive or negative. One word.\n\nThe delivery was fast and the staff were lovely.",
  "Summarize in one sentence: the release moves to Friday because the payment provider changed its API.",
  "Implement a thread-safe LRU cache class in Python with O(1) get and put, production-ready, and explain the edge cases.",
];

const client = new OpenAI({ baseURL, apiKey });
let lastRequestId = "";
for (const content of prompts) {
  const { data, response } = await client.chat.completions
    .create({ model: "auto", messages: [{ role: "user", content }] })
    .withResponse();
  const h = (name) => response.headers.get(`x-smartrouter-${name}`) ?? "?";
  lastRequestId = h("request-id");
  console.log(`${h("task").padEnd(15)} complexity ${h("complexity")}  ->  ${h("model")}`);
  console.log(`  cost $${h("cost-usd")} vs $${h("baseline-cost-usd")} on ${h("baseline-model")}`);
  console.log(`  why: ${h("reason")}`);
  console.log(`  answer: ${(data.choices[0]?.message.content ?? "").slice(0, 80)}`);
}

// Tell the router how the last answer went; it lands on the decision in the dashboard.
const fb = await fetch(`${baseURL}/feedback`, {
  method: "POST",
  headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json" },
  body: JSON.stringify({ request_id: lastRequestId, score: 1, comment: "good answer" }),
});
if (!fb.ok) throw new Error(`feedback failed: ${fb.status} ${await fb.text()}`);
console.log(`\nfeedback recorded for ${lastRequestId}`);
