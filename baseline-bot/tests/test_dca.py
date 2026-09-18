from unittest.mock import Mock

from exchange.results import Outcome, SubmitResult
from strategies.dca import DcaOverlay


class FakeClock:
    now = 1000.0

    def __call__(self):
        return self.now


def ack():
    return SubmitResult(Outcome.ACKNOWLEDGED, "k", order_id="o1", status="FILLED")


def test_buys_cash_amount_once_per_interval():
    clock, orders = FakeClock(), Mock()
    orders.submit_intent.return_value = ack()
    dca = DcaOverlay(["NOVA"], orders, 500_000, interval_seconds=60, clock=clock)
    dca.on_price("NOVA", 20000)                 # first tick buys immediately
    dca.on_price("NOVA", 20000)                 # same interval: no second buy
    clock.now += 61
    dca.on_price("NOVA", 20000)
    assert orders.submit_intent.call_count == 2
    sym, side, qty, limit = orders.submit_intent.call_args.args
    assert (sym, side, qty, limit) == ("NOVA", "buy", 25, 20010)


def test_refused_buy_is_retried_after_retry_seconds():
    clock, orders = FakeClock(), Mock()
    orders.submit_intent.return_value = None    # slot busy
    dca = DcaOverlay(["NOVA"], orders, 500_000, interval_seconds=60,
                     retry_seconds=5, clock=clock)
    dca.on_price("NOVA", 20000)
    dca.on_price("NOVA", 20000)
    assert orders.submit_intent.call_count == 1
    clock.now += 6
    dca.on_price("NOVA", 20000)
    assert orders.submit_intent.call_count == 2


def test_skips_symbols_the_guard_has_cooled():
    orders = Mock()
    dca = DcaOverlay(["NOVA"], orders, 500_000, allow=lambda s: False, clock=FakeClock())
    dca.on_price("NOVA", 20000)
    assert not orders.submit_intent.called
