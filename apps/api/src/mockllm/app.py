"""Mock LLM provider for the demo and the test suites: OpenAI and Anthropic APIs, plus a
TokenShield-style route.

Simulated capability: every upstream model has a skill per task and a latency profile. On a
benchmark case (metadata.benchmark_* present, sent only to simulated endpoints) the model answers
correctly with probability sigmoid(2.4 x (skill - difficulty)), decided deterministically from the
case and model, so the benchmark is reproducible. Real providers never see the annotation.

Faults (for health, fallback and outage demos):
  POST   /admin/faults {"model", "mode": error|timeout|throttle|budget, "count"?, "seconds"?}
  GET    /admin/faults
  DELETE /admin/faults
Stats: GET /stats (calls per endpoint and per upstream model), POST /stats/reset.
TokenShield emulation: /tokenshield/v1/chat/completions adds x-tokenshield-cost-usd and
x-tokenshield-request-id, and the "budget" fault answers 402 budget_exceeded like TokenShield.
Set MOCK_LATENCY_SCALE=0 to remove simulated latency (tests).
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
import time
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import orjson
import tiktoken
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

app = FastAPI(title="SmartRouter mock LLM", docs_url=None, redoc_url=None)
calls: Counter[str] = Counter()
by_model: Counter[str] = Counter()
_enc = tiktoken.get_encoding("o200k_base")
LATENCY_SCALE = float(os.environ.get("MOCK_LATENCY_SCALE", "1"))
TASKS = (
    "classification",
    "extraction",
    "summarization",
    "translation",
    "math",
    "reasoning",
    "coding",
    "chat",
)


@dataclass(frozen=True)
class Profile:
    skills: dict[str, float]
    latency_ms: int
    verbosity: int  # sentences of explanation after the answer
    price: tuple[float, float]  # USD per 1M (in, out), used by the TokenShield emulation


def _skills(default: float, **overrides: float) -> dict[str, float]:
    return {t: overrides.get(t, default) for t in TASKS}


PROFILES: dict[str, Profile] = {
    "mock-fast": Profile(
        _skills(3.3, classification=3.8, chat=3.6, math=2.6, reasoning=2.4, coding=2.5),
        120,
        0,
        (0.10, 0.40),
    ),
    "mock-mini": Profile(
        _skills(2.9, classification=3.4, chat=3.2, math=2.2, reasoning=2.0, coding=2.1),
        300,
        0,
        (0.05, 0.20),
    ),
    "mock-pro": Profile(_skills(4.9, reasoning=5.1, coding=5.0), 1100, 2, (3.00, 12.00)),
    "mock-claude": Profile(
        _skills(4.7, coding=5.7, reasoning=5.3, math=4.6), 1300, 2, (3.00, 15.00)
    ),
    "mock-llama": Profile(
        _skills(2.6, classification=3.2, chat=3.0, translation=2.2, math=1.8, reasoning=1.8),
        700,
        1,
        (0.0, 0.0),
    ),
}
DEFAULT = Profile(_skills(3.0), 400, 1, (1.0, 2.0))

KNOWLEDGE = {
    "photosynthesis": "Photosynthesis is how plants turn light into chemical energy: chlorophyll captures "
    "sunlight, water is split to release oxygen, and carbon dioxide is fixed into glucose.",
    "refund": "You can request a refund within 30 days from Settings, then Billing.",
    "password": "Choose 'Forgot password' on the sign-in page and follow the emailed link.",
}
PADDING = (
    "This follows from the details given.",
    "The conclusion holds under the stated assumptions.",
    "Let me know if you want more depth.",
)


@dataclass
class Fault:
    mode: str
    remaining: int | None
    until: float | None


faults: dict[str, Fault] = {}


def _active_fault(model: str) -> str | None:
    f = faults.get(model)
    if f is None:
        return None
    if f.until is not None and time.time() > f.until:
        faults.pop(model, None)
        return None
    if f.remaining is not None:
        if f.remaining <= 0:
            faults.pop(model, None)
            return None
        f.remaining -= 1
    return f.mode


def _profile(model: str) -> Profile:
    return PROFILES.get(model, DEFAULT)


def _last_user(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""


def _tokens(text: str) -> int:
    return len(_enc.encode(text, disallowed_special=()))


def _prompt_tokens(messages: list[dict[str, Any]], system: str | None = None) -> int:
    parts: list[str] = [system or ""]
    for m in messages:
        c = m.get("content")
        parts.append(c if isinstance(c, str) else orjson.dumps(c).decode())
    text = "".join(parts)
    return _tokens(text) + 3 * len(messages) + 3


def p_correct(skill: float, difficulty: int) -> float:
    return 1.0 / (1.0 + math.exp(-2.4 * (skill - difficulty)))


def _draw(case: str, model: str) -> float:
    h = int(hashlib.sha256(f"{case}|{model}".encode()).hexdigest()[:8], 16)
    return h / 0xFFFFFFFF


def answer(model: str, messages: list[dict[str, Any]], metadata: dict[str, Any] | None) -> str:
    profile = _profile(model)
    pad = " ".join(PADDING[: profile.verbosity])
    meta = metadata or {}
    if "benchmark_expected" in meta:
        task = str(meta.get("benchmark_task", "chat"))
        difficulty = int(meta.get("benchmark_difficulty", 3))
        correct = _draw(str(meta.get("benchmark_case", "")), model) < p_correct(
            profile.skills.get(task, 3.0), difficulty
        )
        if correct:
            return f"{meta['benchmark_expected']} {pad}".strip()
        return f"I am not certain about this one. {pad}".strip()
    prompt = _last_user(messages)
    low = prompt.lower()
    for topic, text in KNOWLEDGE.items():
        if topic in low:
            return f"{text} {pad}".strip()
    clean = re.sub(r"\[\[.*?\]\]", "", prompt).strip()[:80] or "your request"
    return f'Here is a concise answer about "{clean}". {pad}'.strip()


async def _delay(model: str, prompt: str) -> None:
    if "[[fast]]" in prompt:  # make bench: measure the gateway, not the simulated model
        return
    if m := re.search(r"\[\[slow:(\d+(?:\.\d+)?)\]\]", prompt):
        await asyncio.sleep(float(m.group(1)))
        return
    base = _profile(model).latency_ms
    jitter = int(hashlib.sha256(prompt.encode()).hexdigest()[:4], 16) % max(1, base // 4)
    if LATENCY_SCALE > 0:
        await asyncio.sleep((base + jitter) / 1000 * LATENCY_SCALE)


def _fault_response(mode: str, tokenshield: bool) -> Response | None:
    if mode == "error":
        return JSONResponse(
            {"error": {"message": "mock upstream error", "type": "server_error"}}, status_code=500
        )
    if mode == "throttle":
        return JSONResponse(
            {"error": {"message": "rate limited", "type": "rate_limit_error"}},
            status_code=429,
            headers={"retry-after": "1"},
        )
    if mode == "budget":
        headers = (
            {
                "x-tokenshield-request-id": "req_mockts"
                + hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]
            }
            if tokenshield
            else {}
        )
        return JSONResponse(
            {
                "error": {
                    "code": "budget_exceeded",
                    "type": "budget_error",
                    "message": "Budget 'Team monthly' cannot cover this request",
                    "details": {},
                }
            },
            status_code=402,
            headers=headers,
        )
    return None


async def _chat(request: Request, tokenshield: bool) -> Response:
    body = await request.json()
    model = str(body.get("model", "mock"))
    calls["tokenshield" if tokenshield else "chat"] += 1
    by_model[model] += 1
    messages = body.get("messages") or []
    prompt = _last_user(messages)
    mode = _active_fault(model)
    if mode == "timeout":
        await asyncio.sleep(3600)
    if "[[error:500]]" in prompt:
        mode = "error"
    if mode and (resp := _fault_response(mode, tokenshield)):
        return resp
    await _delay(model, prompt)
    text = answer(model, messages, body.get("metadata"))
    max_tokens = body.get("max_completion_tokens") or body.get("max_tokens")
    toks = _enc.encode(text)
    finish = "stop"
    if max_tokens and len(toks) > int(max_tokens):
        text, finish = _enc.decode(toks[: int(max_tokens)]), "length"
    p_tok, c_tok = _prompt_tokens(messages), min(len(toks), int(max_tokens or len(toks)))
    usage = {"prompt_tokens": p_tok, "completion_tokens": c_tok, "total_tokens": p_tok + c_tok}
    rid = "chatcmpl-mock" + hashlib.sha256(orjson.dumps(messages) + model.encode()).hexdigest()[:12]
    headers: dict[str, str] = {}
    if tokenshield:
        pin, pout = _profile(model).price
        headers["x-tokenshield-cost-usd"] = f"{(p_tok * pin + c_tok * pout) / 1e6:.9f}"
        headers["x-tokenshield-request-id"] = "req_ts" + rid[-12:]
        headers["x-tokenshield-cache"] = "MISS"
    created = int(time.time())
    if body.get("stream"):
        include_usage = (body.get("stream_options") or {}).get("include_usage")

        async def gen() -> AsyncIterator[bytes]:
            base = {
                "id": rid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
            }
            yield (
                b"data: "
                + orjson.dumps(
                    base
                    | {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": ""},
                                "finish_reason": None,
                            }
                        ]
                    }
                )
                + b"\n\n"
            )
            words = re.findall(r"\S+\s*", text)
            for i in range(0, len(words), 4):
                yield (
                    b"data: "
                    + orjson.dumps(
                        base
                        | {
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": "".join(words[i : i + 4])},
                                    "finish_reason": None,
                                }
                            ]
                        }
                    )
                    + b"\n\n"
                )
            yield (
                b"data: "
                + orjson.dumps(
                    base | {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
                )
                + b"\n\n"
            )
            if include_usage:
                yield b"data: " + orjson.dumps(base | {"choices": [], "usage": usage}) + b"\n\n"
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
    return JSONResponse(
        {
            "id": rid,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish,
                }
            ],
            "usage": usage,
        },
        headers=headers,
    )


@app.post("/v1/chat/completions")
async def chat(request: Request) -> Response:
    return await _chat(request, tokenshield=False)


@app.post("/tokenshield/v1/chat/completions")
async def tokenshield_chat(request: Request) -> Response:
    return await _chat(request, tokenshield=True)


@app.post("/v1/messages")
async def anthropic_messages(request: Request) -> Response:
    body = await request.json()
    model = str(body.get("model", "mock"))
    calls["messages"] += 1
    by_model[model] += 1
    messages = body.get("messages") or []
    prompt = _last_user(messages)
    mode = _active_fault(model)
    if mode == "timeout":
        await asyncio.sleep(3600)
    if mode and (resp := _fault_response(mode, False)):
        return resp
    await _delay(model, prompt)
    text = answer(model, messages, body.get("metadata"))
    toks = _enc.encode(text)
    out = min(len(toks), int(body.get("max_tokens") or len(toks)))
    stop = "max_tokens" if out < len(toks) else "end_turn"
    text = _enc.decode(toks[:out])
    p_tok = _prompt_tokens(messages, body.get("system"))
    mid = "msg_mock" + hashlib.sha256(orjson.dumps(messages)).hexdigest()[:12]
    usage = {"input_tokens": p_tok, "output_tokens": out}
    if body.get("stream"):

        async def gen() -> AsyncIterator[bytes]:
            def ev(name: str, data: dict[str, Any]) -> bytes:
                return f"event: {name}\n".encode() + b"data: " + orjson.dumps(data) + b"\n\n"

            yield ev(
                "message_start",
                {
                    "type": "message_start",
                    "message": {"id": mid, "usage": {**usage, "output_tokens": 1}},
                },
            )
            yield ev(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            words = re.findall(r"\S+\s*", text)
            for i in range(0, len(words), 4):
                yield ev(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "".join(words[i : i + 4])},
                    },
                )
            yield ev(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop},
                    "usage": {"output_tokens": out},
                },
            )
            yield ev("message_stop", {"type": "message_stop"})

        return StreamingResponse(gen(), media_type="text/event-stream")
    return JSONResponse(
        {
            "id": mid,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop,
            "usage": usage,
        }
    )


@app.get("/v1/models")
@app.get("/tokenshield/v1/models")
async def models() -> dict[str, Any]:
    calls["models"] += 1
    return {"object": "list", "data": [{"id": n, "object": "model"} for n in PROFILES]}


@app.get("/stats")
async def stats() -> dict[str, Any]:
    return {**calls, "by_model": dict(by_model)}


@app.post("/stats/reset")
async def reset() -> dict[str, bool]:
    calls.clear()
    by_model.clear()
    return {"ok": True}


@app.post("/admin/faults")
async def set_fault(request: Request) -> dict[str, Any]:
    body = await request.json()
    seconds = body.get("seconds")
    faults[str(body["model"])] = Fault(
        str(body["mode"]), body.get("count"), time.time() + float(seconds) if seconds else None
    )
    return {"ok": True, "faults": {k: v.mode for k, v in faults.items()}}


@app.get("/admin/faults")
async def list_faults() -> dict[str, Any]:
    return {
        k: {"mode": v.mode, "remaining": v.remaining, "until": v.until} for k, v in faults.items()
    }


@app.delete("/admin/faults")
async def clear_faults() -> dict[str, bool]:
    faults.clear()
    return {"ok": True}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
