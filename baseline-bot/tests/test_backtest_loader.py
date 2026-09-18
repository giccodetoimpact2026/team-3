"""Unit tests for the recording loader: session splitting and ordering."""
import json

from backtest.loader import load_segments, load_series, sessions

LINES = [
    {"event": "session_boundary", "session_id": "s1", "seq": 1},
    {"session_id": "s1", "symbol": "NOVA", "last_price": 100, "seq": 3},
    {"session_id": "s1", "symbol": "NOVA", "last_price": 101, "seq": 2},  # out of order
    {"session_id": "s1", "symbol": "ELXR", "last_price": 200, "seq": 4},
    {"event": "session_boundary", "session_id": "s2", "seq": 5},
    {"session_id": "s2", "symbol": "NOVA", "last_price": 110, "seq": 6},
]


def _write(tmp_path, lines):
    p = tmp_path / "feed.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(p)


def test_segments_split_on_boundary(tmp_path):
    segs = load_segments(_write(tmp_path, LINES))
    assert [s["session_id"] for s in segs] == ["s1", "s2"]
    assert set(segs[0]["series"].keys()) == {"NOVA", "ELXR"}


def test_ticks_sorted_by_seq(tmp_path):
    segs = load_segments(_write(tmp_path, LINES))
    nova = segs[0]["series"]["NOVA"]
    assert [t[0] for t in nova] == [2, 3]        # seq ascending
    assert [t[2] for t in nova] == [101, 100]    # prices follow the seq order


def test_load_series_defaults_to_last_session(tmp_path):
    seg = load_series(_write(tmp_path, LINES))
    assert seg["session_id"] == "s2"
    assert list(seg["series"].keys()) == ["NOVA"]


def test_load_series_can_select_a_session(tmp_path):
    seg = load_series(_write(tmp_path, LINES), session_id="s1")
    assert seg["session_id"] == "s1"


def test_sessions_lists_distinct_ids_in_order(tmp_path):
    assert sessions(_write(tmp_path, LINES)) == ["s1", "s2"]


def test_truncated_final_line_is_ignored(tmp_path):
    p = tmp_path / "feed.jsonl"
    p.write_text(json.dumps(LINES[1]) + "\n{\"partial\": ")  # hard-kill leftover
    segs = load_segments(str(p))
    assert segs and "NOVA" in segs[0]["series"]
