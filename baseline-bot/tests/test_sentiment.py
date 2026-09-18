"""LLM sentiment analysis of announcements (S1).

Every test here mocks the client. Nothing in the suite spends a credit — the
grant is ~$25 and a top-up is recorded, so a test that called the real service
would be a bug in its own right.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from news.engine import NewsEngine
from news.sentiment import SYSTEM_PROMPT, SentimentAnalyzer, parse_views

LLM_CFG = {"enabled": True, "min_confidence": 0.6, "max_calls_per_session": 10}
UNIVERSE = ("NOVA", "ELXR", "CRVX", "DRFT", "AXON")


def reply(text, input_tokens=400, output_tokens=120):
    """A Messages API response carrying `text`."""
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens,
                              output_tokens=output_tokens),
    )


def analyzer(text=None, cfg=None, side_effect=None):
    client = Mock()
    if side_effect is not None:
        client.messages.create.side_effect = side_effect
    else:
        client.messages.create.return_value = reply(text or '{"views": []}')
    return SentimentAnalyzer({**LLM_CFG, **(cfg or {})}, api_key="k",
                             allowed_symbols=UNIVERSE, client=client), client


# =========================================================================
# Parsing — tolerant envelope, strict contents
# =========================================================================

def test_parses_a_clean_reply():
    views = parse_views('{"views": [{"symbol": "NOVA", "direction": "up", '
                        '"confidence": 0.8, "rationale": "oil discovery"}]}')
    assert len(views) == 1
    assert views[0].symbol == "NOVA"
    assert views[0].action == "buy"
    assert views[0].confidence == 0.8
    assert views[0].rationale == "oil discovery"


def test_tolerates_a_code_fence_and_preamble():
    """A stray fence must not cost us the signal."""
    views = parse_views('Here you go:\n```json\n{"views": [{"symbol": "AXON", '
                        '"direction": "down", "confidence": 0.7, '
                        '"rationale": "freight costs"}]}\n```')
    assert len(views) == 1 and views[0].action == "sell"


def test_neutral_view_has_no_action():
    views = parse_views('{"views": [{"symbol": "ELXR", "direction": "neutral", '
                        '"confidence": 0.9, "rationale": "uncorrelated"}]}')
    assert views[0].action is None


@pytest.mark.parametrize("body", [
    "",
    "no json here at all",
    "{not valid json",
    '{"views": "not a list"}',
    '{"views": [{"symbol": "NOVA"}]}',                       # no direction
    '{"views": [{"symbol": "NOVA", "direction": "sideways", "confidence": 1}]}',
    '{"views": [{"symbol": "NOVA", "direction": "up", "confidence": "high"}]}',
    '{"views": [{"symbol": "NOVA", "direction": "up", "confidence": 7}]}',
    '{"views": [{"symbol": 42, "direction": "up", "confidence": 0.9}]}',
    '{"views": [null]}',
])
def test_malformed_replies_yield_no_views(body):
    """Fail closed: a bad answer must never reach the trading path."""
    assert parse_views(body) == []


def test_confidence_floor_discards_weak_views():
    body = ('{"views": [{"symbol": "NOVA", "direction": "up", '
            '"confidence": 0.9, "rationale": "direct"}, '
            '{"symbol": "CRVX", "direction": "up", '
            '"confidence": 0.3, "rationale": "second order"}]}')
    kept = parse_views(body, min_confidence=0.6)
    assert [v.symbol for v in kept] == ["NOVA"]


# =========================================================================
# The prompt asks for what Sophia asked for
# =========================================================================

def test_prompt_supplies_the_supply_chain_not_just_tickers():
    """"I want the system to reason across the supply chain" — so the
    relationships have to be in the prompt as fact."""
    for symbol in UNIVERSE:
        assert symbol in SYSTEM_PROMPT
    assert "freight" in SYSTEM_PROMPT.lower()
    assert "NOVA leads AXON" in SYSTEM_PROMPT


def test_prompt_demands_confidence_and_rationale():
    assert "confidence" in SYSTEM_PROMPT
    assert "rationale" in SYSTEM_PROMPT


def test_prompt_never_asks_for_quantities_or_prices():
    """The model returns a view; sizing stays in deterministic risk code."""
    lowered = SYSTEM_PROMPT.lower()
    for forbidden in ("quantity", "shares", "limit price", "how many"):
        assert forbidden not in lowered


# =========================================================================
# Calling the service
# =========================================================================

def test_uses_a_non_streaming_call():
    """The service rejects stream=true with 400 STREAMING_UNSUPPORTED."""
    a, client = analyzer('{"views": []}')
    a.analyze("something happened", "NOVA", 1)
    client.messages.create.assert_called_once()
    assert "stream" not in client.messages.create.call_args.kwargs
    assert not client.messages.stream.called


def test_sends_the_system_prompt_and_the_announcement():
    a, client = analyzer('{"views": []}')
    a.analyze("Major oil discovery near Nova Energy fields.", "NOVA", 1)
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["system"] == SYSTEM_PROMPT
    assert "oil discovery" in kwargs["messages"][0]["content"]
    assert kwargs["model"] == "sonnet"


def test_result_carries_provenance():
    a, _ = analyzer('{"views": [{"symbol": "NOVA", "direction": "up", '
                    '"confidence": 0.8, "rationale": "why"}]}')
    result = a.analyze("m", "NOVA", 7)
    assert result.announcement_id == 7
    assert result.model == "sonnet"
    assert result.usage == {"input_tokens": 400, "output_tokens": 120}
    assert result.latency_ms >= 0


# --- budget and memoisation ---------------------------------------------

def test_one_call_per_announcement():
    """Re-deriving the same announcement is the only way to burn the grant."""
    a, client = analyzer('{"views": []}')
    for _ in range(5):
        a.analyze("m", "NOVA", 1)
    assert client.messages.create.call_count == 1


def test_distinct_announcements_each_get_a_call():
    a, client = analyzer('{"views": []}')
    a.analyze("m", "NOVA", 1)
    a.analyze("m", "NOVA", 2)
    assert client.messages.create.call_count == 2


def test_call_budget_is_a_hard_ceiling():
    a, client = analyzer('{"views": []}', cfg={"max_calls_per_session": 3})
    for i in range(10):
        a.analyze("m", "NOVA", i)
    assert client.messages.create.call_count == 3
    assert a.analyze("m", "NOVA", 99) is None


def test_reset_clears_results_but_not_the_budget():
    """News is per-window; the credit grant spans the whole event."""
    a, client = analyzer('{"views": []}', cfg={"max_calls_per_session": 2})
    a.analyze("m", "NOVA", 1)
    a.reset()
    a.analyze("m", "NOVA", 1)          # same id, new window -> charged again
    assert client.messages.create.call_count == 2
    assert a.analyze("m", "NOVA", 2) is None   # budget spent, not reset


# --- failure modes ------------------------------------------------------

def test_a_failed_call_returns_none():
    """402 INSUFFICIENT_CREDIT, a timeout and a transport error all land here."""
    a, _ = analyzer(side_effect=RuntimeError("402 INSUFFICIENT_CREDIT"))
    assert a.analyze("m", "NOVA", 1) is None
    assert a.stats()["failures"] == 1


def test_a_failed_call_is_not_memoised_as_success():
    a, client = analyzer(side_effect=[RuntimeError("timeout"),
                                      reply('{"views": []}')])
    assert a.analyze("m", "NOVA", 1) is None
    assert a.analyze("m", "NOVA", 1) is not None       # retried once
    assert client.messages.create.call_count == 2


def test_disabled_analyzer_never_calls():
    a, client = analyzer('{"views": []}', cfg={"enabled": False})
    assert a.analyze("m", "NOVA", 1) is None
    client.messages.create.assert_not_called()


def test_hallucinated_symbol_is_discarded():
    """A ticker outside the universe must not be able to create a bias."""
    a, _ = analyzer('{"views": [{"symbol": "TSLA", "direction": "up", '
                    '"confidence": 0.9, "rationale": "nope"}]}')
    assert a.analyze("m", "NOVA", 1).views == []


def test_missing_api_key_is_rejected_at_construction():
    with pytest.raises(ValueError, match="EXCHANGE_API_KEY"):
        SentimentAnalyzer({"enabled": True}, api_key="")


# =========================================================================
# Integration with the two-tier engine
# =========================================================================

def inline_engine(text, news_cfg=None):
    """Engine whose tier-1 dispatch runs inline, so tests stay deterministic."""
    a, client = analyzer(text)
    engine = NewsEngine({"enabled": True, **(news_cfg or {})},
                        analyzer=a, dispatch=lambda fn: fn())
    return engine, a, client


def test_keyword_tier_answers_immediately():
    """Tier 0 must not wait on the model: the event worker is single-threaded."""
    engine, _, _ = inline_engine('{"views": []}')
    bias = engine.on_announcement({
        "message": "Major oil discovery near Nova Energy fields.",
        "affected_symbol": "NOVA", "announcement_id": 1})
    assert bias is not None and bias.source == "keyword"


def test_llm_tier_replaces_the_keyword_bias():
    engine, _, _ = inline_engine(
        '{"views": [{"symbol": "NOVA", "direction": "up", '
        '"confidence": 0.85, "rationale": "supply shock lifts energy"}]}')
    engine.on_announcement({"message": "Major oil discovery.",
                            "affected_symbol": "NOVA", "announcement_id": 1})
    bias = engine.active_bias("NOVA")
    assert bias.source == "llm"
    assert bias.confidence == 0.85
    assert "energy" in bias.rationale


def test_llm_biases_symbols_the_announcement_never_named():
    """Sophia's actual requirement — the ripple, not the named ticker."""
    engine, _, _ = inline_engine(
        '{"views": ['
        '{"symbol": "AXON", "direction": "down", "confidence": 0.8, '
        '"rationale": "sanctions hit freight directly"},'
        '{"symbol": "NOVA", "direction": "down", "confidence": 0.65, '
        '"rationale": "freight demand for energy falls"}]}')
    engine.on_announcement({"message": "Sanctions imposed on freight routes.",
                            "affected_symbol": "AXON", "announcement_id": 1})
    assert engine.active_bias("AXON").action == "sell"
    assert engine.active_bias("NOVA").action == "sell"
    assert engine.active_bias("NOVA").source == "llm"


def test_neutral_review_clears_the_provisional_bias():
    """A keyword guess must not stand once the model has disagreed with it."""
    engine, _, _ = inline_engine(
        '{"views": [{"symbol": "NOVA", "direction": "neutral", '
        '"confidence": 0.9, "rationale": "already priced in"}]}')
    engine.on_announcement({"message": "Record growth reported.",
                            "affected_symbol": "NOVA", "announcement_id": 1})
    assert engine.active_bias("NOVA") is None


def test_keyword_bias_survives_an_llm_failure():
    """Fail closed means falling back, not falling over."""
    a, client = analyzer(side_effect=RuntimeError("timeout"))
    engine = NewsEngine({"enabled": True}, analyzer=a, dispatch=lambda fn: fn())
    engine.on_announcement({"message": "Major oil discovery.",
                            "affected_symbol": "NOVA", "announcement_id": 1})
    bias = engine.active_bias("NOVA")
    assert bias is not None and bias.source == "keyword"


def test_unclassified_keyword_still_gets_an_llm_opinion():
    """The keyword tier is coarse; a headline it cannot label is exactly where
    the model earns its place."""
    engine, _, client = inline_engine(
        '{"views": [{"symbol": "CRVX", "direction": "up", '
        '"confidence": 0.75, "rationale": "demand cycle turns"}]}')
    engine.on_announcement({"message": "Quarterly guidance revised.",
                            "affected_symbol": "CRVX", "announcement_id": 1})
    assert client.messages.create.called
    assert engine.active_bias("CRVX").source == "llm"


def test_dispatch_keeps_the_model_off_the_calling_thread():
    """The default dispatcher must not run the analysis inline."""
    a, client = analyzer('{"views": []}')
    calls = []
    engine = NewsEngine({"enabled": True}, analyzer=a,
                        dispatch=lambda fn: calls.append(fn))
    engine.on_announcement({"message": "Major oil discovery.",
                            "affected_symbol": "NOVA", "announcement_id": 1})
    assert len(calls) == 1
    client.messages.create.assert_not_called()   # deferred, not executed
    calls[0]()
    client.messages.create.assert_called_once()


def test_engine_reset_clears_the_analyzer_cache():
    engine, a, client = inline_engine('{"views": []}')
    engine.on_announcement({"message": "Major oil discovery.",
                            "affected_symbol": "NOVA", "announcement_id": 1})
    engine.reset()
    assert a.result_for(1) is None


def test_config_report_includes_llm_provenance():
    """The dashboard has to be able to show where a number came from."""
    engine, _, _ = inline_engine('{"views": []}')
    report = engine.get_config()["llm"]
    assert report["enabled"] is True
    assert report["model"] == "sonnet"
    assert "calls" in report and "budget" in report


def test_bias_serialises_for_the_dashboard():
    engine, _, _ = inline_engine(
        '{"views": [{"symbol": "NOVA", "direction": "up", '
        '"confidence": 0.85, "rationale": "supply shock"}]}')
    engine.on_announcement({"message": "Major oil discovery.",
                            "affected_symbol": "NOVA", "announcement_id": 41})
    payload = engine.active_bias("NOVA").as_dict()
    assert payload["source"] == "llm"
    assert payload["confidence"] == 0.85
    assert payload["announcement_id"] == 41
    assert payload["rationale"] == "supply shock"
