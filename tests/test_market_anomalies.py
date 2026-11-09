from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from stellar_intelligence_bot.chains.base import CollectedEvent
from stellar_intelligence_bot.core.events import Chain, ChainEvent
from stellar_intelligence_bot.core.market_anomalies import (
    BookObservation,
    TradeObservation,
    large_trade_signal,
    market_anomaly_signals,
    volume_anomaly_signal,
)
from stellar_intelligence_bot.core.markets import MarketPair
from stellar_intelligence_bot.storage.database import Database
from stellar_intelligence_bot.storage.models import (
    MarketSnapshotRecord,
    SignalEvaluationRecord,
    SignalRecord,
    VolumeWindowRecord,
)
from stellar_intelligence_bot.workers.processor import EventProcessor

START = datetime(2026, 1, 1, tzinfo=UTC)
PAIR = "native/USDC:issuer"


def event(at: datetime) -> ChainEvent:
    return ChainEvent(
        event_id=f"snapshot:{at.isoformat()}",
        chain=Chain.STELLAR,
        event_type="market_snapshot",
        occurred_at=at,
        source_ref="https://horizon.stellar.org/order_book",
        attributes={"pair": PAIR},
    )


def observations(minutes: int, *, widened: bool = False) -> list[BookObservation]:
    result = []
    for step in range(minutes * 4 + 1):
        bid, ask = Decimal("99.9"), Decimal("100.1")
        if widened and step == minutes * 4:
            bid, ask = Decimal("98"), Decimal("102")
        result.append(BookObservation(START + timedelta(seconds=step * 15), bid, ask))
    return result


def evaluate(items: list[BookObservation], *, now: datetime | None = None):
    at = items[-1].observed_at
    return market_anomaly_signals(
        event(at), PAIR, items, now=now or at, spread_floor_percent=Decimal("1"),
        windows=(5,),
    )


def test_spread_requires_fresh_continuous_pair_specific_history() -> None:
    books = observations(60, widened=True)
    signals = evaluate(books)
    spread = next(signal for signal in signals if signal.category == "spread_anomaly")
    assert Decimal(spread.observed_facts["observed_percent"]) > Decimal(3)
    assert spread.observed_facts["sample_count"] == 240
    assert spread.observed_facts["engine_version"] == "market-anomaly-v1"
    assert not evaluate(books[-10:])
    assert not evaluate(books, now=books[-1].observed_at + timedelta(minutes=3))
    assert not evaluate([*books[:100], *books[120:]])


def test_price_requires_multiple_points_and_preceding_windows() -> None:
    books = observations(20)
    for index in range(len(books) - 6, len(books)):
        books[index] = BookObservation(books[index].observed_at, Decimal("102.9"), Decimal("103.1"))
    signals = evaluate(books)
    movement = next(signal for signal in signals if signal.category == "price_movement")
    assert movement.observed_facts["window_minutes"] == 5
    assert Decimal(movement.observed_facts["observed_percent"]) == 3
    assert not evaluate(books[-21:])
    single_outlier = observations(20)
    last = single_outlier[-1]
    single_outlier[-1] = BookObservation(last.observed_at, Decimal("102.9"), Decimal("103.1"))
    assert not evaluate(single_outlier)


def test_future_and_crossed_books_do_not_change_baseline() -> None:
    books = observations(60, widened=True)
    at = books[-1].observed_at
    future = BookObservation(at + timedelta(seconds=15), Decimal("1"), Decimal("1000"))
    crossed = BookObservation(at - timedelta(seconds=7), Decimal("102"), Decimal("101"))
    expected = market_anomaly_signals(
        event(at), PAIR, books, now=at, spread_floor_percent=Decimal("1"), windows=()
    )
    actual = market_anomaly_signals(
        event(at), PAIR, [*books, future, crossed], now=at,
        spread_floor_percent=Decimal("1"), windows=(),
    )
    assert actual == expected


def test_volume_requires_completed_continuous_watermarked_trade_scans() -> None:
    books = [
        BookObservation(item.observed_at, item.bid, item.ask, item.observed_at)
        for item in observations(20)
    ]
    at = books[-1].observed_at
    amounts = {
        START: Decimal(10),
        START + timedelta(minutes=5): Decimal(10),
        START + timedelta(minutes=10): Decimal(10),
        START + timedelta(minutes=15): Decimal(100),
        START + timedelta(minutes=20): Decimal(999999),
    }

    def evaluate_volume(items: list[BookObservation], now: datetime = at):
        return volume_anomaly_signal(
            event(at), PAIR, items, amounts, now=now, samples=3, multiplier=Decimal(3)
        )

    signal = evaluate_volume(books)
    assert signal is not None
    assert signal.observed_facts["observed_base_volume"] == "100"
    assert signal.observed_facts["baseline_mean_base_volume"] == "10"
    assert signal.observed_facts["threshold_base_volume"] == "30"
    assert "venues combined" in signal.observed_facts["source"]
    assert evaluate_volume(books[20:]) is None
    assert evaluate_volume([*books[:20], *books[30:]]) is None
    assert evaluate_volume(books, now=at + timedelta(minutes=3)) is None
    assert evaluate_volume(observations(20)) is None


def test_large_trade_requires_covered_venue_specific_history_and_absolute_floor() -> None:
    at = START + timedelta(hours=2)

    def trade(index: int, when: datetime, base: str, quote: str, venue: str) -> TradeObservation:
        return TradeObservation(
            ChainEvent(
                event_id=f"trade:{index}", chain=Chain.STELLAR, event_type="trade",
                occurred_at=when, source_ref=f"https://horizon.stellar.org/trades/{index}",
                attributes={"trade_type": venue},
            ),
            Decimal(base), Decimal(quote), venue,
        )

    history = [
        trade(index, at - timedelta(seconds=3600 - index), "10", "20", "orderbook")
        for index in range(100)
    ]
    current = trade(101, at, "10000", "20000", "orderbook")

    def evaluate(item: TradeObservation, prior: list[TradeObservation], **kwargs: object):
        return large_trade_signal(
            item, prior, PAIR, now=at, minimum_xlm=Decimal(10000),
            coverage_start=at - timedelta(hours=1), **kwargs,
        )

    signal = evaluate(current, history)
    assert signal is not None
    assert signal.event_id == "trade:101"
    assert signal.observed_facts["trade_type"] == "orderbook"
    assert signal.observed_facts["observed_quote_notional"] == "20000"
    assert signal.observed_facts["baseline_median_quote_notional"] == "20"
    assert signal.observed_facts["threshold_quote_notional"] == "20000"
    assert evaluate(trade(102, at, "9999", "19998", "orderbook"), history) is None
    assert evaluate(trade(103, at, "10000", "20000", "liquidity_pool"), history) is None
    assert evaluate(current, history[:-1]) is None
    future = trade(104, at + timedelta(seconds=1), "1", "1", "orderbook")
    assert evaluate(current, [*history[:-1], future]) is None
    assert large_trade_signal(
        current, history, PAIR, now=at + timedelta(minutes=3),
        minimum_xlm=Decimal(10000), coverage_start=at - timedelta(hours=1),
    ) is None
    assert large_trade_signal(
        current, history, PAIR, now=at,
        minimum_xlm=Decimal(10000), coverage_start=at - timedelta(minutes=59),
    ) is None


@pytest.mark.asyncio
async def test_processor_persists_and_deduplicates_baseline_spread(tmp_path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'anomalies.db'}")
    await db.initialize()
    pair = MarketPair.parse("native/USDC:GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")
    end = datetime.now(UTC) - timedelta(seconds=45)
    try:
        async with db.sessions.begin() as session:
            for index in range(240):
                session.add(
                    MarketSnapshotRecord(
                        pair_key=pair.key,
                        observed_at=end - timedelta(hours=1) + timedelta(seconds=index * 15),
                        best_bid=Decimal("99.9"),
                        best_ask=Decimal("100.1"),
                        bid_depth=Decimal(1),
                        ask_depth=Decimal(1),
                        spread_percent=Decimal("0.2"),
                        observed_facts={},
                    )
                )
        processor = EventProcessor(
            db, whale_thresholds={}, baseline_samples=20, anomaly_multiplier=Decimal(3),
            large_order_threshold=Decimal(10000), spread_threshold_percent=Decimal(1),
            price_windows_minutes=(5,),
        )
        for offset in (0, 15):
            at = end + timedelta(seconds=offset)
            snapshot = ChainEvent(
                event_id=f"snapshot:{at.isoformat()}",
                chain=Chain.STELLAR,
                event_type="market_snapshot",
                occurred_at=at,
                source_ref="https://horizon.stellar.org/order_book",
                attributes={"pair": pair.key, "best_bid": "98", "best_ask": "102"},
            )
            await processor.process(CollectedEvent(snapshot, pair=pair, is_market_snapshot=True))
        async with db.sessions() as session:
            signals = list((await session.scalars(select(SignalRecord))).all())
            evaluations = await session.scalar(
                select(func.count()).select_from(SignalEvaluationRecord)
            )
        assert [signal.category for signal in signals] == ["spread_anomaly"]
        assert evaluations == 5
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_processor_generates_volume_only_after_proven_coverage(tmp_path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'volume.db'}")
    await db.initialize()
    pair = MarketPair.parse("native/USDC:GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")
    now = datetime.now(UTC) - timedelta(seconds=10)
    watermark = now - timedelta(seconds=5)
    end = watermark.replace(minute=watermark.minute // 5 * 5, second=0, microsecond=0)
    start = end - timedelta(minutes=20)
    try:
        async with db.sessions.begin() as session:
            at = start - timedelta(seconds=30)
            while at < now - timedelta(seconds=15):
                session.add(
                    MarketSnapshotRecord(
                        pair_key=pair.key, observed_at=at, best_bid=Decimal("99.9"),
                        best_ask=Decimal("100.1"), bid_depth=Decimal(1), ask_depth=Decimal(1),
                        spread_percent=Decimal("0.2"),
                        observed_facts={"trade_scan_complete_through": at.isoformat()},
                    )
                )
                at += timedelta(seconds=30)
            for index in range(4):
                session.add(
                    VolumeWindowRecord(
                        pair_key=pair.key, window_start=start + timedelta(minutes=5 * index),
                        base_volume=Decimal(100 if index == 3 else 10), trade_count=1,
                    )
                )
        snapshot = ChainEvent(
            event_id="volume-snapshot", chain=Chain.STELLAR, event_type="market_snapshot",
            occurred_at=now, source_ref="https://horizon.stellar.org/order_book",
            attributes={"pair": pair.key, "best_bid": "99.9", "best_ask": "100.1",
                        "trade_scan_complete_through": watermark.isoformat()},
        )
        processor = EventProcessor(
            db, whale_thresholds={}, baseline_samples=3, anomaly_multiplier=Decimal(3),
            large_order_threshold=Decimal(10000), spread_threshold_percent=Decimal(1),
            price_windows_minutes=(5,),
        )
        await processor.process(CollectedEvent(snapshot, pair=pair, is_market_snapshot=True))
        async with db.sessions() as session:
            signals = list((await session.scalars(select(SignalRecord))).all())
        assert [signal.category for signal in signals] == ["volume_anomaly"]
        assert signals[0].observed_facts["baseline_mean_base_volume"].startswith("10")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_processor_persists_large_trade_and_venue_evidence(tmp_path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'large-trade.db'}")
    await db.initialize()
    pair = MarketPair.parse("native/USDC:GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")
    now = datetime.now(UTC) - timedelta(seconds=5)
    at_trade = now - timedelta(seconds=5)
    try:
        async with db.sessions.begin() as session:
            at = now - timedelta(minutes=75)
            while at < now - timedelta(seconds=15):
                session.add(
                    MarketSnapshotRecord(
                        pair_key=pair.key, observed_at=at, best_bid=Decimal("1.99"),
                        best_ask=Decimal("2.01"), bid_depth=Decimal(1), ask_depth=Decimal(1),
                        spread_percent=Decimal("1"),
                        observed_facts={"trade_scan_complete_through": at.isoformat()},
                    )
                )
                at += timedelta(seconds=30)
        for index in range(101):
            trade_at = (
                at_trade - timedelta(minutes=30, seconds=100 - index)
                if index < 100 else at_trade
            )
            size = Decimal(10 if index < 100 else 10000)
            trade_event = ChainEvent(
                event_id=f"trade:{index}", chain=Chain.STELLAR, event_type="trade",
                occurred_at=trade_at, source_ref=f"https://horizon.stellar.org/trades/{index}",
                attributes={"pair": pair.key, "trade_type": "orderbook"},
            )
            await db.save_trade_event(
                trade_event, "trade-stream", str(index), pair, size, size * 2
            )
        snapshot = ChainEvent(
            event_id="large-trade-snapshot", chain=Chain.STELLAR, event_type="market_snapshot",
            occurred_at=now, source_ref="https://horizon.stellar.org/order_book",
            attributes={"pair": pair.key, "best_bid": "1.99", "best_ask": "2.01",
                        "trade_scan_complete_through": at_trade.isoformat()},
        )
        processor = EventProcessor(
            db, whale_thresholds={}, baseline_samples=20, anomaly_multiplier=Decimal(3),
            large_order_threshold=Decimal(10000), spread_threshold_percent=Decimal(1),
            price_windows_minutes=(5,),
        )
        await processor.process(CollectedEvent(snapshot, pair=pair, is_market_snapshot=True))
        async with db.sessions() as session:
            signal = await session.scalar(
                select(SignalRecord).where(SignalRecord.category == "large_trade")
            )
            evaluation_count = await session.scalar(
                select(func.count()).select_from(SignalEvaluationRecord)
                .where(SignalEvaluationRecord.signal_id == signal.signal_id)
            ) if signal is not None else 0
        assert signal is not None
        assert signal.event_id == "trade:100"
        assert signal.observed_facts["trade_type"] == "orderbook"
        assert signal.observed_facts["baseline_trade_count"] == 100
        assert evaluation_count == 5
    finally:
        await db.close()
