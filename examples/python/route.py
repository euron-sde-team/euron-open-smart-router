"""Route three requests with model="auto" and show what SmartRouter decided.

    pip install openai
    SMARTROUTER_KEY=tsr_... python examples/python/route.py
"""

import os

import httpx
from openai import OpenAI

BASE_URL = os.environ.get("SMARTROUTER_URL", "http://localhost:4230/v1")
KEY = os.environ["SMARTROUTER_KEY"]

PROMPTS = [
    "Classify the sentiment as positive or negative. One word.\n\nThe delivery was fast and the staff were lovely.",
    "Summarize in one sentence: the release moves to Friday because the payment provider changed its API.",
    "Implement a thread-safe LRU cache class in Python with O(1) get and put, production-ready, "
    "and explain the edge cases.",
]

client = OpenAI(base_url=BASE_URL, api_key=KEY)
last_request_id = ""
for prompt in PROMPTS:
    raw = client.chat.completions.with_raw_response.create(
        model="auto", messages=[{"role": "user", "content": prompt}]
    )
    h = raw.headers
    last_request_id = h["x-smartrouter-request-id"]
    print(f"{h['x-smartrouter-task']:<15} complexity {h['x-smartrouter-complexity']}  ->  {h['x-smartrouter-model']}")
    print(f"  cost ${h.get('x-smartrouter-cost-usd', '?')} vs ${h.get('x-smartrouter-baseline-cost-usd', '?')} on {h.get('x-smartrouter-baseline-model', '?')}")
    print(f"  why: {h['x-smartrouter-reason']}")
    print(f"  answer: {raw.parse().choices[0].message.content[:80]}")

# Tell the router how the last answer went; it lands on the decision in the dashboard.
httpx.post(
    f"{BASE_URL}/feedback",
    json={"request_id": last_request_id, "score": 1, "comment": "good answer"},
    headers={"authorization": f"Bearer {KEY}"},
).raise_for_status()
print(f"\nfeedback recorded for {last_request_id}")
