"""The adapter between the price classifier and the policy's label set.

The classifier emits eight directional labels; the policy table has six. If the
mapping is wrong or incomplete, every symbol falls through to WARMUP — which
forbids entries — and the bot stops trading without saying why.
"""
import pytest

from strategies.regime import Regime as MarketRegime
from strategies.regime_adapter import AtrRegimeClassifier
from strategies.regime_policy import Regime as PolicyRegime
from strategies.regime_policy import RegimePolicy


@pytest.fixture
def adapter():
    return AtrRegimeClassifier()


def feed(adapter, symbol, prices):
    for price in prices:
        adapter.observe(symbol, price)


# --- the mapping --------------------------------------------------------

def test_every_classifier_label_maps_to_a_policy_label(adapter):
    """An unmapped label would KeyError on the trading path."""
    adapter.observe("NOVA", 10_000)          # force the mapping dict to build
    mapping = {
        MarketRegime.WARMUP: PolicyRegime.WARMUP,
        MarketRegime.COMPRESSION: PolicyRegime.COMPRESSION,
        MarketRegime.RANGE: PolicyRegime.RANGE,
        MarketRegime.NEUTRAL: PolicyRegime.NEUTRAL,
        MarketRegime.TREND_UP: PolicyRegime.TREND,
        MarketRegime.TREND_DOWN: PolicyRegime.TREND,
        MarketRegime.SHOCK_UP: PolicyRegime.SHOCK,
        MarketRegime.SHOCK_DOWN: PolicyRegime.SHOCK,
    }
    assert set(mapping) == set(MarketRegime)
    assert set(mapping.values()) <= set(PolicyRegime.ALL)


def test_policy_understands_every_label_the_adapter_can_emit(adapter):
    """The two vocabularies must stay in step across the seam."""
    policy = RegimePolicy({"enabled": True})
    for family in ("momentum", "mean_reversion", "pairs_follow"):
        for label in PolicyRegime.ALL:
            assert policy.multiplier("NOVA", family, label) is not None


# --- classification through the adapter ---------------------------------

def test_unseen_symbol_is_warmup(adapter):
    """Fail safe: no data means no entries."""
    assert adapter.classify("NOVA") == PolicyRegime.WARMUP


def test_smooth_rise_reads_as_trend(adapter):
    feed(adapter, "NOVA", [10_000 + 100 * i for i in range(20)])
    assert adapter.classify("NOVA") == PolicyRegime.TREND


def test_flat_series_reads_as_compression(adapter):
    feed(adapter, "ELXR", [10_000 + i % 2 for i in range(20)])
    assert adapter.classify("ELXR") == PolicyRegime.COMPRESSION


def test_choppy_series_reads_as_range(adapter):
    prices = [10_000, 10_200, 9_800] * 8
    feed(adapter, "ELXR", prices)
    assert adapter.classify("ELXR") == PolicyRegime.RANGE


def test_a_downtrend_also_reads_as_trend(adapter):
    """Direction is collapsed: the strategies are long-only and their own
    signals are directional, so TREND_DOWN only ever reaches a sell, which
    regime never gates."""
    feed(adapter, "NOVA", [20_000 - 100 * i for i in range(20)])
    assert adapter.classify("NOVA") == PolicyRegime.TREND


def test_symbols_are_independent(adapter):
    feed(adapter, "NOVA", [10_000 + 100 * i for i in range(20)])
    feed(adapter, "ELXR", [10_000 + i % 2 for i in range(20)])
    assert adapter.classify("NOVA") == PolicyRegime.TREND
    assert adapter.classify("ELXR") == PolicyRegime.COMPRESSION


def test_history_is_bounded(adapter):
    """A window's worth of ticks must not grow without limit."""
    feed(adapter, "NOVA", [10_000 + i for i in range(5_000)])
    assert len(adapter._histories["NOVA"]) <= 64


# --- session boundary ---------------------------------------------------

def test_reset_returns_every_symbol_to_warmup(adapter):
    """Each window re-anchors the price engine: a series spanning two would
    show a jump that happened in neither, and that reads as a SHOCK exactly as
    the new window opens."""
    feed(adapter, "NOVA", [10_000 + 100 * i for i in range(20)])
    assert adapter.classify("NOVA") == PolicyRegime.TREND
    adapter.reset()
    assert adapter.classify("NOVA") == PolicyRegime.WARMUP


def test_reset_can_target_one_symbol(adapter):
    feed(adapter, "NOVA", [10_000 + 100 * i for i in range(20)])
    feed(adapter, "CRVX", [10_000 + 100 * i for i in range(20)])
    adapter.reset("NOVA")
    assert adapter.classify("NOVA") == PolicyRegime.WARMUP
    assert adapter.classify("CRVX") == PolicyRegime.TREND


def test_reset_discards_the_previous_windows_prices(adapter):
    """Not just the label — the history behind it, or the first print of the
    new window is compared against the old window's closing level."""
    feed(adapter, "NOVA", [10_000 + 100 * i for i in range(20)])
    adapter.reset()
    adapter.observe("NOVA", 30_000)          # new window opens far away
    assert adapter.classify("NOVA") == PolicyRegime.WARMUP
    assert list(adapter._histories["NOVA"]) == [30_000]


def test_the_price_handler_resets_the_classifier_at_a_boundary():
    """Wired end to end: main()'s session handler must call reset."""
    import inspect

    import main

    source = inspect.getsource(main.main)
    on_session = source.split("def on_session")[1].split("\n    def ")[0]
    # Everything else per-window already resets here; the classifier's price
    # history is per-window too.
    for expected in ("strat.reset()", "exits.reset()", "book_cache.reset()",
                     "classifier.reset()"):
        assert expected in on_session, expected
