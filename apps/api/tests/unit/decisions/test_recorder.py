import uuid
from datetime import UTC, datetime

from smartrouter.decisions.recorder import DecisionRecord, decode, encode


def test_records_round_trip_through_the_stream_encoding() -> None:
    rec = DecisionRecord(
        request_id="req_1",
        requested_model="auto",
        strategy="balanced",
        task="math",
        complexity=4,
        status="SUCCESS",
        project_id=uuid.uuid4(),
        api_key_id=uuid.uuid4(),
        signals=["math: math verb"],
        fallback_chain=[{"model": "pro", "outcome": "error", "ms": 12, "status": 502}],
        candidates=[{"model": "pro", "eligible": True}],
        actual_cost_nusd=1234,
        created_at=datetime(2026, 10, 24, 12, 0, tzinfo=UTC),
    )
    assert decode(encode(rec)) == rec


def test_missing_ids_stay_none() -> None:
    rec = DecisionRecord("req_2", "auto", "cost", "chat", 1, "REJECTED")
    out = decode(encode(rec))
    assert out.project_id is None and out.api_key_id is None
