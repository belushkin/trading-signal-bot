from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from stellar_intelligence_bot.app.trade_evidence import account_diagnostics, trade_window_evidence
from stellar_intelligence_bot.chains.base import TradeProvenance
from stellar_intelligence_bot.core.events import Chain, ChainEvent
from stellar_intelligence_bot.core.markets import MarketPair
from stellar_intelligence_bot.core.signal_engine import order_book_signals
from stellar_intelligence_bot.storage.database import Database
from stellar_intelligence_bot.storage.models import SignalEvaluationRecord, TradeRecord

ISSUER = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
PAIR = MarketPair.parse(f"native/USDC:{ISSUER}")
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _trade_event(trade_id: str, at: datetime, price: str = "0.1") -> ChainEvent:
    return ChainEvent(
        event_id=trade_id,
        chain=Chain.STELLAR,
        event_type="trade",
        occurred_at=at,
        source_ref=f"https://horizon.stellar.org/trades/{trade_id}",
        attributes={"pair": PAIR.key, "price": Decimal(price)},
    )


async def _save_trade(
    db: Database,
    trade_id: str,
    at: datetime,
    price: str = "0.1",
    provenance: TradeProvenance | None = None,
) -> bool:
    quote = Decimal(price) * 10
    return await db.save_trade_event(
        _trade_event(trade_id, at, price), "trades", trade_id, PAIR, Decimal(10), quote, provenance
    )


async def _signal_with_snapshot(db: Database, with_snapshot: bool = True) -> None:
    event = ChainEvent(
        event_id="snapshot:1",
        chain=Chain.STELLAR,
        event_type="market_snapshot",
        occurred_at=T0,
        source_ref="https://horizon.stellar.org/order_book",
        attributes={
            "pair": PAIR.key,
            "best_bid": Decimal(99),
            "best_ask": Decimal(101),
            "bid_depth_base": Decimal(100),
            "ask_depth_base": Decimal(100),
        },
    )
    await db.save_market_snapshot(
        event, PAIR, T0, Decimal(99), Decimal(101), Decimal(100), Decimal(100),
        dict(event.attributes),
    )
    signal = next(
        s
        for s in order_book_signals(
            event, PAIR.key, large_order_threshold=Decimal(1000),
            spread_threshold_percent=Decimal(1),
        )
        if s.category == "wide_spread"
    )
    await db.save_signal(signal, timedelta(minutes=15))


async def _five_minute(db: Database) -> SignalEvaluationRecord:
    async with db.sessions() as session:
        return (
            await session.scalars(
                select(SignalEvaluationRecord).where(SignalEvaluationRecord.horizon_minutes == 5)
            )
        ).one()


@pytest.mark.asyncio
async def test_provenance_persists_and_duplicates_are_rejected(tmp_path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'p.db'}")
    await db.initialize()
    provenance = TradeProvenance(
        venue="liquidity_pool", base_is_seller=False, base_asset="native",
        counter_asset=f"USDC:{ISSUER}", operation_id="42", liquidity_pool_id="abc",
        observed_at=T0,
    )
    try:
        assert await _save_trade(db, "t1", T0, provenance=provenance)
        assert not await _save_trade(db, "t1", T0, provenance=provenance)
        async with db.sessions() as session:
            rows = (await session.scalars(select(TradeRecord))).all()
        assert len(rows) == 1
        row = rows[0]
        assert (row.venue, row.base_is_seller, row.operation_id) == ("liquidity_pool", False, "42")
        assert row.counter_asset == f"USDC:{ISSUER}"
        assert row.transaction_hash is None and row.base_account is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_legacy_trade_has_null_provenance_and_backfill_is_idempotent(tmp_path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}")
    await db.initialize()
    try:
        await _save_trade(db, "t1", T0)
        provenance = TradeProvenance(venue="order_book", base_is_seller=True, operation_id="7")
        entry = ("t1", provenance, Decimal(10), Decimal(1))
        mismatch = ("t1", provenance, Decimal(11), Decimal(1))
        missing = ("zzz", provenance, Decimal(10), Decimal(1))
        assert await db.apply_trade_provenance([entry, mismatch, missing]) == {
            "updated": 1, "already_populated": 0, "not_stored": 1, "amount_mismatch": 1,
        }
        assert (await db.apply_trade_provenance([entry]))["updated"] == 0
        async with db.sessions() as session:
            row = (await session.scalars(select(TradeRecord))).one()
        assert row.base_is_seller is True and row.observed_at is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_skipped_trade_advances_watermark_and_health_counts(tmp_path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    await db.initialize()
    try:
        await db.record_skipped_trade("zero_base_dust", stream_key="trades", cursor="9", now=T0)
        assert await db.get_cursor("trades") == "9"
        await db.record_health(started_at=T0, successful=False, error="boom", now=T0)
        await db.record_health(started_at=T0, successful=False, error="boom", now=T0)
        await db.record_health(started_at=T0, successful=True, now=T0)
        health = await db.health_status(now=T0, stale_after=timedelta(minutes=5))
        assert health["skipped_trade_count"] == 1
        assert health["interruption_count"] == 1
        assert health["consecutive_failures"] == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_evaluation_prefers_snapshot_over_trades(tmp_path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'e1.db'}")
    await db.initialize()
    try:
        await _signal_with_snapshot(db)
        target = T0 + timedelta(minutes=5)
        later = ChainEvent(
            event_id="snapshot:2", chain=Chain.STELLAR, event_type="market_snapshot",
            occurred_at=target, source_ref="x", attributes={"pair": PAIR.key},
        )
        await db.save_market_snapshot(
            later, PAIR, target, Decimal(109), Decimal(111), Decimal(1), Decimal(1),
            {"pair": PAIR.key},
        )
        for i, minute in enumerate((-2, -1, 1, 2, 3)):
            await _save_trade(db, f"t{i}", T0 + timedelta(minutes=minute), "0.5")
        await db.evaluate_due_signals(now=target + timedelta(seconds=30))
        row = await _five_minute(db)
        assert row.status == "measured" and row.price_basis == "snapshot_mid"
        assert row.observation_ref == "snapshot:2"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_trade_fallback_is_labelled_and_never_mixes_bases(tmp_path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'e2.db'}")
    await db.initialize()
    try:
        await _signal_with_snapshot(db)
        target = T0 + timedelta(minutes=5)
        await _save_trade(db, "a", T0 - timedelta(minutes=1), "1.00")
        await _save_trade(db, "b", T0 + timedelta(minutes=2), "1.05")
        await _save_trade(db, "c", target + timedelta(minutes=1), "1.10")
        await db.evaluate_due_signals(now=target + timedelta(minutes=5))
        row = await _five_minute(db)
        assert row.status == "measured"
        assert row.price_basis == "trade_price" and row.data_quality == "trade_sampled"
        assert row.reference_source == "trade:a" and row.observation_ref == "trade:c"
        assert row.reference_price == Decimal("1.00")
        assert abs(row.return_percent - Decimal(10)) < Decimal("0.0001")
        report = await db.evaluation_report(since=T0 - timedelta(seconds=1), horizon_minutes=5)
        assert [item["price_basis"] for item in report] == ["trade_price"]
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trades", "reason"),
    [
        ([], "no_snapshot_and_no_trade_in_grace"),
        ([(-1, "1"), (11, "1")], "no_snapshot_and_no_trade_in_grace"),
        ([(5, "1"), (6, "1"), (7, "1")], "no_reference_trade_in_lookback"),
        ([(-1, "1"), (6, "1")], "insufficient_trade_observations"),
    ],
)
async def test_missing_observation_reasons_are_recorded(tmp_path, trades, reason) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'e3.db'}")
    await db.initialize()
    try:
        await _signal_with_snapshot(db)
        for i, (minute, price) in enumerate(trades):
            await _save_trade(db, f"t{i}", T0 + timedelta(minutes=minute), price)
        await db.evaluate_due_signals(now=T0 + timedelta(minutes=11))
        row = await _five_minute(db)
        assert row.status == "missing_observation"
        assert row.missing_reason == reason
        assert row.return_percent is None and row.price_basis is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_reevaluate_missing_is_explicit_and_idempotent(tmp_path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'e4.db'}")
    await db.initialize()
    try:
        await _signal_with_snapshot(db)
        now = T0 + timedelta(minutes=11)
        await db.evaluate_due_signals(now=now)
        async with db.sessions.begin() as session:
            row = (
                await session.scalars(
                    select(SignalEvaluationRecord).where(
                        SignalEvaluationRecord.horizon_minutes == 5
                    )
                )
            ).one()
            row.missing_reason = None
        for i, (minute, price) in enumerate(((-1, "1"), (2, "1.1"), (6, "1.2"))):
            await _save_trade(db, f"t{i}", T0 + timedelta(minutes=minute), price)
        await db.reevaluate_missing_with_trades(now=now, dry_run=True)
        assert (await _five_minute(db)).status == "missing_observation"
        await db.reevaluate_missing_with_trades(now=now)
        row = await _five_minute(db)
        assert row.status == "measured" and row.price_basis == "trade_price"
        first = row.evaluated_at
        await db.reevaluate_missing_with_trades(now=now + timedelta(hours=1))
        assert (await _five_minute(db)).evaluated_at == first
    finally:
        await db.close()


def _row(trade_id, base, venue, seller, op, minute=0) -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id, pair_key=PAIR.key, occurred_at=T0 + timedelta(minutes=minute),
        base_amount=Decimal(base), quote_amount=Decimal(base), price=Decimal(1), venue=venue,
        base_is_seller=seller, operation_id=op, base_account="GA", counter_account="GB",
    )


def test_trade_evidence_venue_aggregation_and_direction_labels() -> None:
    trades = [
        _row("1", "6", "order_book", True, "a"),
        _row("2", "2", "order_book", False, "a"),
        _row("3", "2", "liquidity_pool", None, "b"),
        _row("4", "10", None, None, None),
    ]
    evidence = trade_window_evidence(trades)
    text = str(evidence)
    assert evidence["trade_count"] == 4
    assert "aggressor" in text
    assert "GA" not in text and "GB" not in text
    venues = evidence["venues"]
    assert venues["order_book"]["count"] == 2
    assert Decimal(str(venues["order_book"]["base_volume"])) == 8
    assert Decimal(str(venues["liquidity_pool"]["base_volume"])) == 2
    assert Decimal(str(venues["unknown"]["base_volume"])) == 10
    assert Decimal(str(sum(Decimal(str(v["base_volume"])) for v in venues.values()))) == 20


def test_account_diagnostics_exposes_accounts_only_through_diagnostics() -> None:
    result = account_diagnostics([_row("1", "6", "order_book", True, "a")], 5)
    assert "GA" in str(result)
