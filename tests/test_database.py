from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from stellar_intelligence_bot.core.events import Chain, ChainEvent
from stellar_intelligence_bot.core.markets import MarketPair
from stellar_intelligence_bot.core.signal_engine import order_book_signals, whale_signal
from stellar_intelligence_bot.storage.database import Database


@pytest.mark.asyncio
async def test_event_cursor_and_signal_persistence_are_idempotent(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'signals.db'}")
    await database.initialize()
    event = ChainEvent(
        event_id="payment:1",
        chain=Chain.STELLAR,
        event_type="payment",
        occurred_at=datetime.now(UTC),
        source_ref="https://stellar.expert/tx/1",
        attributes={"amount": Decimal("100"), "asset": "native", "source_account": "G1"},
    )

    try:
        assert await database.save_event_with_cursor(event, "stellar:operations", "1")
        assert not await database.save_event_with_cursor(event, "stellar:operations", "1")
        assert await database.get_cursor("stellar:operations") == "1"
        signal = whale_signal(event, Decimal("10"))
        assert signal is not None
        assert await database.save_signal(signal, timedelta(minutes=15))
        assert not await database.save_signal(signal, timedelta(minutes=15))
        repeat = ChainEvent(
            event_id="payment:2",
            chain=Chain.STELLAR,
            event_type="payment",
            occurred_at=event.occurred_at + timedelta(minutes=5),
            source_ref="https://stellar.expert/tx/2",
            attributes={
                "amount": Decimal("200"),
                "asset": "native",
                "source_account": "G1",
            },
        )
        assert await database.save_event_with_cursor(repeat, "stellar:operations", "2")
        repeat_signal = whale_signal(repeat, Decimal("10"))
        assert repeat_signal is not None
        assert not await database.save_signal(repeat_signal, timedelta(minutes=15))
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_trade_event_cursor_and_volume_bucket_commit_together(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'trades.db'}")
    await database.initialize()
    issuer = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
    pair = MarketPair.parse(f"native/USDC:{issuer}")
    event = ChainEvent(
        event_id="trade:1",
        chain=Chain.STELLAR,
        event_type="trade",
        occurred_at=datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
        source_ref="https://stellar.expert/tx/1",
        attributes={"pair": pair.key},
    )
    try:
        assert await database.save_trade_event(
            event, "stellar:trades", "1", pair, Decimal("2"), Decimal("6")
        )
        assert not await database.save_trade_event(
            event, "stellar:trades", "1", pair, Decimal("2"), Decimal("6")
        )
        assert await database.get_cursor("stellar:trades") == "1"
        assert await database.current_window_volume(
            pair, datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        ) == Decimal("2")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_volume_baseline_uses_only_preceding_windows(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'baseline.db'}")
    await database.initialize()
    issuer = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
    pair = MarketPair.parse(f"native/USDC:{issuer}")
    try:
        for index, amount in enumerate((Decimal("10"), Decimal("20"), Decimal("30"))):
            occurred_at = datetime(2026, 1, 1, 0, index * 5, tzinfo=UTC)
            event = ChainEvent(
                event_id=f"trade:{index}",
                chain=Chain.STELLAR,
                event_type="trade",
                occurred_at=occurred_at,
                source_ref=f"https://stellar.expert/tx/{index}",
                attributes={"pair": pair.key},
            )
            await database.save_trade_event(
                event,
                "stellar:trades",
                str(index),
                pair,
                amount,
                amount * 2,
            )

        baseline = await database.volume_baseline(pair, 2, datetime(2026, 1, 1, 0, 15, tzinfo=UTC))
        assert baseline == (Decimal("25"), 2)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_market_signal_evaluation_measures_price_and_sampled_extremes(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'evaluation.db'}")
    await database.initialize()
    issuer = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
    pair = MarketPair.parse(f"native/USDC:{issuer}")
    start = datetime(2026, 1, 1, tzinfo=UTC)

    def snapshot(index: int, minute: int, bid: str, ask: str) -> ChainEvent:
        return ChainEvent(
            event_id=f"snapshot:{index}",
            chain=Chain.STELLAR,
            event_type="market_snapshot",
            occurred_at=start + timedelta(minutes=minute),
            source_ref=f"https://horizon.stellar.org/order_book?sample={index}",
            attributes={
                "pair": pair.key,
                "best_bid": Decimal(bid),
                "best_ask": Decimal(ask),
                "bid_depth_base": Decimal(100),
                "ask_depth_base": Decimal(100),
                "largest_bid_amount": Decimal(100),
                "largest_ask_amount": Decimal(100),
            },
        )

    try:
        initial = snapshot(0, 0, "99", "101")
        await database.save_market_snapshot(
            initial,
            pair,
            initial.occurred_at,
            Decimal(99),
            Decimal(101),
            Decimal(100),
            Decimal(100),
            dict(initial.attributes),
        )
        signals = order_book_signals(
            initial,
            pair.key,
            large_order_threshold=Decimal(1000),
            spread_threshold_percent=Decimal(1),
        )
        signal = next(candidate for candidate in signals if candidate.category == "wide_spread")
        assert signal.observed_facts["reference_price"] == "100"
        assert await database.save_signal(signal, timedelta(minutes=15))

        for index, minute, bid, ask in (
            (1, 2, "104", "106"),
            (2, 4, "94", "96"),
            (3, 5, "102", "104"),
        ):
            item = snapshot(index, minute, bid, ask)
            await database.save_market_snapshot(
                item,
                pair,
                item.occurred_at,
                Decimal(bid),
                Decimal(ask),
                Decimal(100),
                Decimal(100),
                dict(item.attributes),
            )

        assert (
            await database.evaluate_due_signals(now=start + timedelta(minutes=5, seconds=30)) == 1
        )
        report = await database.evaluation_report(
            since=start - timedelta(seconds=1), horizon_minutes=5
        )
        assert len(report) == 1
        assert report[0]["category"] == "wide_spread"
        assert report[0]["measured"] == 1
        assert report[0]["mean_observed_change_percent"] == Decimal("3.00000000")
        assert report[0]["mean_max_observed_increase_percent"] == Decimal("5.00000000")
        assert report[0]["mean_max_observed_decrease_percent"] == Decimal("-5.00000000")
        assert report[0]["data_quality_counts"] == {"sampled": 1}
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_evaluation_records_missing_observations_instead_of_falling_back(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'missing.db'}")
    await database.initialize()
    issuer = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
    pair = MarketPair.parse(f"native/USDC:{issuer}")
    event_time = datetime(2026, 1, 1, tzinfo=UTC)
    event = ChainEvent(
        event_id="snapshot:missing",
        chain=Chain.STELLAR,
        event_type="market_snapshot",
        occurred_at=event_time,
        source_ref="https://horizon.stellar.org/order_book",
        attributes={
            "pair": pair.key,
            "best_bid": Decimal(99),
            "best_ask": Decimal(101),
            "bid_depth_base": Decimal(100),
            "ask_depth_base": Decimal(100),
        },
    )
    try:
        await database.save_market_snapshot(
            event,
            pair,
            event_time,
            Decimal(99),
            Decimal(101),
            Decimal(100),
            Decimal(100),
            dict(event.attributes),
        )
        signal = next(
            item
            for item in order_book_signals(
                event,
                pair.key,
                large_order_threshold=Decimal(1000),
                spread_threshold_percent=Decimal(1),
            )
            if item.category == "wide_spread"
        )
        await database.save_signal(signal, timedelta(minutes=15))
        target = event_time + timedelta(minutes=5)

        assert await database.evaluate_due_signals(now=target + timedelta(minutes=4)) == 0
        assert await database.evaluate_due_signals(now=target + timedelta(minutes=5)) == 1
        report = await database.evaluation_report(
            since=event_time - timedelta(seconds=1), horizon_minutes=5
        )
        assert report[0]["measured"] == 0
        assert report[0]["missing"] == 1
        assert report[0]["mean_observed_change_percent"] is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_runtime_health_reports_stale_and_recovered_states(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'health.db'}")
    await database.initialize()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    try:
        assert not (await database.health_status(stale_after=timedelta(minutes=5), now=start))[
            "healthy"
        ]
        await database.record_health(started_at=start, successful=True, now=start)
        assert (
            await database.health_status(
                stale_after=timedelta(minutes=5), now=start + timedelta(minutes=1)
            )
        )["healthy"]

        await database.record_health(
            started_at=start,
            successful=False,
            error="source unavailable",
            now=start + timedelta(minutes=2),
        )
        failed = await database.health_status(
            stale_after=timedelta(minutes=5), now=start + timedelta(minutes=2)
        )
        assert not failed["healthy"]
        assert failed["last_error"] == "source unavailable"

        await database.record_health(
            started_at=start, successful=True, now=start + timedelta(minutes=3)
        )
        recovered = await database.health_status(
            stale_after=timedelta(minutes=5), now=start + timedelta(minutes=3)
        )
        assert recovered["healthy"]
        assert recovered["last_error"] is None

        restarted_at = start + timedelta(minutes=4)
        await database.start_health(restarted_at)
        restarted = await database.health_status(stale_after=timedelta(minutes=5), now=restarted_at)
        assert not restarted["healthy"]
        assert restarted["started_at"] == restarted_at
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_retention_prunes_expired_records_but_preserves_cursors(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'retention.db'}")
    await database.initialize()
    issuer = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
    pair = MarketPair.parse(f"native/USDC:{issuer}")
    old = datetime(2026, 1, 1, tzinfo=UTC)
    now = old + timedelta(days=100)
    event = ChainEvent(
        event_id="old:market",
        chain=Chain.STELLAR,
        event_type="market_snapshot",
        occurred_at=old,
        source_ref="https://horizon.stellar.org/order_book",
        attributes={
            "pair": pair.key,
            "best_bid": Decimal(99),
            "best_ask": Decimal(101),
            "bid_depth_base": Decimal(100),
            "ask_depth_base": Decimal(100),
            "largest_bid_amount": Decimal(2000),
        },
    )
    try:
        await database.save_market_snapshot(
            event,
            pair,
            old,
            Decimal(99),
            Decimal(101),
            Decimal(100),
            Decimal(100),
            dict(event.attributes),
        )
        await database.advance_cursor("retained-stream", "cursor-10")
        signal = next(
            item
            for item in order_book_signals(
                event,
                pair.key,
                large_order_threshold=Decimal(1000),
                spread_threshold_percent=Decimal(1),
            )
            if item.category == "visible_liquidity"
        )
        await database.save_signal(signal, timedelta(minutes=15))

        result = await database.prune_before(now - timedelta(days=90))
        assert result == {
            "events": 1,
            "trades": 0,
            "market_snapshots": 1,
            "volume_windows": 0,
            "signals": 1,
            "signal_evaluations": 5,
        }
        assert await database.get_cursor("retained-stream") == "cursor-10"
        assert await database.evaluation_report(since=old) == []
    finally:
        await database.close()
