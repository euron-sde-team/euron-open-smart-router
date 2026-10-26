# Examples

Small, runnable clients for a running SmartRouter (`make up && make demo`). Each sends the
same three requests (an easy classification, a summary, a hard coding task) with
`model="auto"` and prints which model the router chose, why, and what it cost against the
baseline. The point: your application code does not change beyond `base_url` and the key.

| Example | Run |
| --- | --- |
| [Python, OpenAI SDK](python/route.py) | `pip install openai && SMARTROUTER_KEY=tsr_... python examples/python/route.py` |
| [Node, OpenAI SDK](node/route.mjs) | `npm i openai && SMARTROUTER_KEY=tsr_... node examples/node/route.mjs` |

`SMARTROUTER_URL` defaults to `http://localhost:4230/v1`. `make demo` prints a key; you can
also create one under API keys in the dashboard. Each run ends by rating the last answer
through `POST /v1/feedback`, which shows up in the decision log.
