from datetime import UTC, datetime
from decimal import Decimal

from stellar_intelligence_bot.core.events import Chain, ChainEvent
from stellar_intelligence_bot.core.signal_engine import (
    order_book_signals,
    volume_signal,
    whale_signal,
)
from stellar_intelligence_bot.core.signals import Severity


def make_event(event_type: str, attributes: dict[str, object]) -> ChainEvent:
    return ChainEvent(
        event_id=f"event:{event_type}",
        chain=Chain.STELLAR,
        event_type=event_type,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_ref="https://stellar.expert/tx",
        attributes=attributes,  # type: ignore[arg-type]
    )


def test_whale_signal_requires_explicit_threshold_and_does_not_infer_direction() -> None:
    event = make_event(
        "payment",
        {"amount": Decimal("100"), "asset": "native", "source_account": "GABC"},
    )

    assert whale_signal(event, None) is None
    signal = whale_signal(event, Decimal("20"))
    assert signal is not None
    assert signal.severity is Severity.WATCH
    assert "unknown" in signal.interpretation
    assert signal.observed_facts["amount"] == "100"


def test_volume_signal_requires_complete_baseline() -> None:
    event = make_event("market_snapshot", {"pair": "native/USDC"})

    assert volume_signal(event, "native/USDC", Decimal(100), Decimal(10), 0, Decimal(3)) is None
    assert (
        volume_signal(event, "native/USDC", Decimal(100), Decimal(10), 20, Decimal(3))
        is not None
    )
    assert volume_signal(event, "native/USDC", Decimal(20), Decimal(10), 20, Decimal(3)) is None


def test_orderbook_signals_report_visible_order_without_claiming_execution() -> None:
    event = make_event(
        "market_snapshot",
        {
            "best_bid": "0.99",
            "best_ask": "1.01",
            "largest_bid_amount": "12000",
            "largest_ask_amount": "100",
            "bid_depth_base": "15000",
            "ask_depth_base": "100",
        },
    )

    signals = order_book_signals(
        event,
        "XLM/USDC",
        large_order_threshold=Decimal(10000),
        spread_threshold_percent=Decimal("1"),
    )
    assert {signal.category for signal in signals} == {"visible_liquidity", "wide_spread"}
    assert "not evidence of execution" in signals[0].interpretation
