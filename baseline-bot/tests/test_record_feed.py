"""Unit tests for the recorder's record shaping (no live SSE)."""
from record_feed import make_record


def test_make_record_shape_and_types():
    payload = {"symbol": "NOVA", "last_price": 15100, "timestamp": "2026-09-18T04:00:00Z"}
    rec = make_record(payload, "sess_1", 42)
    assert rec == {
        "session_id": "sess_1",
        "symbol": "NOVA",
        "last_price": 15100,
        "timestamp": "2026-09-18T04:00:00Z",
        "seq": 42,
    }


def test_make_record_coerces_price_to_int():
    rec = make_record({"symbol": "ELXR", "last_price": "20000", "timestamp": "t"}, None, 1)
    assert rec["last_price"] == 20000
    assert isinstance(rec["last_price"], int)
    assert rec["session_id"] is None
