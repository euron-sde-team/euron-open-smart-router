from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from mockllm import app as mock


@pytest.fixture
def client() -> Iterator[TestClient]:
    mock.LATENCY_SCALE = 0
    mock.faults.clear()
    with TestClient(mock.app) as c:
        yield c
    mock.faults.clear()


def _oracle(case: str, task: str, difficulty: int, expected: str = "42") -> dict[str, str]:
    return {
        "benchmark_case": case,
        "benchmark_task": task,
        "benchmark_difficulty": str(difficulty),
        "benchmark_expected": expected,
    }


def _ask(model: str, meta: dict[str, str]) -> str:
    return mock.answer(model, [{"role": "user", "content": "q"}], meta)


def test_p_correct_falls_with_difficulty() -> None:
    assert mock.p_correct(5.0, 1) > mock.p_correct(5.0, 3) > mock.p_correct(5.0, 5) > 0.4
    assert mock.p_correct(2.0, 5) < 0.01


def test_draw_is_deterministic_per_case_and_model() -> None:
    assert mock._draw("c1", "mock-pro") == mock._draw("c1", "mock-pro")
    assert mock._draw("c1", "mock-pro") != mock._draw("c1", "mock-mini")
    assert all(0 <= mock._draw(f"c{i}", "m") <= 1 for i in range(50))


def test_strong_models_pass_easy_cases_and_weak_models_fail_hard_ones() -> None:
    easy = [_ask("mock-pro", _oracle(f"e{i}", "math", 2)) for i in range(200)]
    hard = [_ask("mock-mini", _oracle(f"h{i}", "math", 5)) for i in range(200)]
    assert sum(a.startswith("42") for a in easy) > 190
    assert sum(a.startswith("42") for a in hard) < 10


def test_skill_is_per_task() -> None:
    # mock-claude is the strongest coder; mock-fast is a poor one.
    claude = sum(
        _ask("mock-claude", _oracle(f"k{i}", "coding", 4)).startswith("42") for i in range(200)
    )
    fast = sum(
        _ask("mock-fast", _oracle(f"k{i}", "coding", 4)).startswith("42") for i in range(200)
    )
    assert claude > 180 > 20 > fast


def test_verbosity_pads_correct_answers() -> None:
    meta = _oracle("v1", "classification", 1, "positive")
    assert _ask("mock-fast", meta) == "positive"
    assert _ask("mock-pro", meta).startswith("positive ") and len(_ask("mock-pro", meta)) > 20


def test_without_oracle_answers_from_knowledge() -> None:
    text = mock.answer("mock-fast", [{"role": "user", "content": "What is photosynthesis?"}], None)
    assert "chlorophyll" in text


def test_openai_chat_and_usage(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        json={"model": "mock-fast", "messages": [{"role": "user", "content": "hello"}]},
    )
    body = r.json()
    assert r.status_code == 200
    assert body["choices"][0]["message"]["content"]
    assert (
        body["usage"]["total_tokens"]
        == body["usage"]["prompt_tokens"] + body["usage"]["completion_tokens"]
    )
    assert "x-tokenshield-cost-usd" not in r.headers


def test_max_tokens_truncates(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-pro",
            "max_tokens": 2,
            "messages": [{"role": "user", "content": "explain"}],
        },
    )
    assert r.json()["choices"][0]["finish_reason"] == "length"
    assert r.json()["usage"]["completion_tokens"] == 2


def test_stream_ends_with_usage_and_done(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-fast",
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    lines = [ln for ln in r.text.splitlines() if ln.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    assert '"usage"' in lines[-2]


def test_tokenshield_route_reports_cost(client: TestClient) -> None:
    r = client.post(
        "/tokenshield/v1/chat/completions",
        json={"model": "mock-mini", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert r.status_code == 200
    assert float(r.headers["x-tokenshield-cost-usd"]) > 0
    assert r.headers["x-tokenshield-request-id"].startswith("req_ts")


def test_budget_fault_answers_402_like_tokenshield(client: TestClient) -> None:
    client.post("/admin/faults", json={"model": "mock-mini", "mode": "budget", "count": 1})
    body = {"model": "mock-mini", "messages": [{"role": "user", "content": "hello"}]}
    r = client.post("/tokenshield/v1/chat/completions", json=body)
    assert r.status_code == 402
    assert r.json()["error"]["code"] == "budget_exceeded"
    assert "x-tokenshield-request-id" in r.headers
    # The fault was for one call only.
    assert client.post("/tokenshield/v1/chat/completions", json=body).status_code == 200


def test_error_and_throttle_faults(client: TestClient) -> None:
    body = {"model": "mock-pro", "messages": [{"role": "user", "content": "hello"}]}
    client.post("/admin/faults", json={"model": "mock-pro", "mode": "error"})
    assert client.post("/v1/chat/completions", json=body).status_code == 500
    assert client.get("/admin/faults").json()["mock-pro"]["mode"] == "error"
    client.post("/admin/faults", json={"model": "mock-pro", "mode": "throttle"})
    r = client.post("/v1/chat/completions", json=body)
    assert r.status_code == 429 and r.headers["retry-after"] == "1"
    client.delete("/admin/faults")
    assert client.post("/v1/chat/completions", json=body).status_code == 200


def test_timed_fault_expires(client: TestClient) -> None:
    client.post("/admin/faults", json={"model": "mock-pro", "mode": "error", "seconds": 0.001})
    mock.faults["mock-pro"].until = 0  # already in the past
    body = {"model": "mock-pro", "messages": [{"role": "user", "content": "hello"}]}
    assert client.post("/v1/chat/completions", json=body).status_code == 200


def test_anthropic_messages(client: TestClient) -> None:
    r = client.post(
        "/v1/messages",
        json={
            "model": "mock-claude",
            "max_tokens": 200,
            "system": "be brief",
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": _oracle("a1", "chat", 1, "hi there"),
        },
    )
    body = r.json()
    assert body["content"][0]["text"].startswith("hi there")
    assert body["stop_reason"] == "end_turn"
    assert body["usage"]["input_tokens"] > 0


def test_anthropic_stream(client: TestClient) -> None:
    r = client.post(
        "/v1/messages",
        json={
            "model": "mock-claude",
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert "event: message_stop" in r.text and "content_block_delta" in r.text


def test_stats_models_and_health(client: TestClient) -> None:
    client.post("/stats/reset")
    client.post(
        "/v1/chat/completions",
        json={"model": "mock-fast", "messages": [{"role": "user", "content": "x"}]},
    )
    stats = client.get("/stats").json()
    assert stats["chat"] == 1 and stats["by_model"] == {"mock-fast": 1}
    assert {m["id"] for m in client.get("/v1/models").json()["data"]} >= {"mock-pro", "mock-llama"}
    assert client.get("/healthz").json() == {"status": "ok"}
