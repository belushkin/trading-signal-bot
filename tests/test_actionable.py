from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from stellar_intelligence_bot.app.actionable import AssistantSettings, build_idea, process_ideas
from stellar_intelligence_bot.core.markets import MarketPair
from stellar_intelligence_bot.storage.database import Database
from stellar_intelligence_bot.storage.models import (
    AssistantSettingsRecord,
    EventRecord,
    MarketSnapshotRecord,
    OpportunityEvaluationRecord,
    OpportunityRecord,
    SignalRecord,
)

ISSUER = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
PAIR = MarketPair.parse(f"native/USDC:{ISSUER}")


def settings() -> AssistantSettings:
    return AssistantSettings(
        balances={"native": Decimal("100"), PAIR.quote.key: Decimal("100")},
        reserve=Decimal("10"), fee=Decimal("0.001"), allocation=Decimal("10"),
        min_movement_percent=Decimal("1"), minimum_severity="HIGH",
    )


def signal(at: datetime, movement: str = "2") -> SignalRecord:
    return SignalRecord(
        signal_id="signal", event_id="event", deduplication_key="dedup", occurred_at=at,
        category="price_movement", severity="HIGH", confidence="HIGH",
        observed_facts={"pair": PAIR.key, "observed_percent": movement, "window_minutes": 5},
        interpretation="Price moved", expires_at=at + timedelta(hours=1),
    )


def snapshot(at: datetime, bid: str = "1", ask: str = "1.25") -> MarketSnapshotRecord:
    return MarketSnapshotRecord(
        pair_key=PAIR.key, observed_at=at, best_bid=Decimal(bid), best_ask=Decimal(ask),
        bid_depth=Decimal("1000"), ask_depth=Decimal("1000"),
        observed_facts={"order_book_levels": {
            "version": 1, "price_units": "quote_per_base", "bid_amount_units": "quote",
            "ask_amount_units": "base",
            "bids": [{"price": bid, "amount": "1000"}],
            "asks": [{"price": ask, "amount": "1000"}],
        }},
    )


@pytest.mark.parametrize(("movement", "side"), [("2", "BUY"), ("-2", "SELL")])
def test_sizing_target_and_costs_are_directional(movement: str, side: str) -> None:
    now = datetime.now(UTC)
    idea = build_idea(signal(now, movement), snapshot(now), PAIR, settings())
    assert idea is not None
    actual_side, result = idea
    assert actual_side == side
    quantity = Decimal(str(result["quantity"]))
    entry = Decimal(str(result["entry_limit"]))
    target = Decimal(str(result["target_exit"]))
    fees = Decimal(str(result["estimated_fees_quote"]))
    profit = quantity * (target - entry if side == "BUY" else entry - target) - fees
    assert profit >= quantity * entry * Decimal("0.01")
    assert profit == Decimal(str(result["estimated_profit_quote"]))
    assert quantity.as_tuple().exponent >= -7


def test_incomplete_liquidity_and_units_reject() -> None:
    now = datetime.now(UTC)
    book = snapshot(now)
    book.observed_facts["order_book_levels"]["price_units"] = "unknown"
    assert build_idea(signal(now), book, PAIR, settings()) is None
    book = snapshot(now)
    book.observed_facts["order_book_levels"]["asks"][0]["amount"] = "0.001"
    assert build_idea(signal(now), book, PAIR, settings()) is None
    assert build_idea(signal(now, "0.5"), snapshot(now), PAIR, settings()) is None


@pytest.mark.asyncio
async def test_persistence_idempotence_and_sampled_outcomes(tmp_path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'ideas.db'}")
    await db.initialize()
    now = datetime.now(UTC)
    try:
        async with db.sessions.begin() as session:
            session.add(AssistantSettingsRecord(
                id=1, updated_at=now - timedelta(minutes=1),
                settings=settings().model_dump(mode="json"),
            ))
            session.add(EventRecord(
                event_id="event", chain="stellar", event_type="market_snapshot",
                occurred_at=now, source_ref="horizon", attributes={},
            ))
            session.add(signal(now))
            session.add(snapshot(now))
        await process_ideas(db, (PAIR,), now)
        await process_ideas(db, (PAIR,), now)
        async with db.sessions.begin() as session:
            idea = (await session.scalars(select(OpportunityRecord))).one()
            target = Decimal(str(idea.parameters["target_exit"]))
            session.add(snapshot(now + timedelta(minutes=1), "1", "1.25"))
            session.add(snapshot(
                now + timedelta(minutes=5), str(target + Decimal("0.01")),
                str(target + Decimal("0.1")),
            ))
        await process_ideas(db, (PAIR,), now + timedelta(minutes=5))
        await process_ideas(db, (PAIR,), now + timedelta(minutes=10))
        async with db.sessions() as session:
            evaluations = (await session.scalars(
                select(OpportunityEvaluationRecord).order_by(OpportunityEvaluationRecord.horizon_minutes)
            )).all()
            assert len(evaluations) == 6
            assert evaluations[0].status == "observed"
            assert evaluations[0].result["entry_price_observed"] is True
            assert evaluations[0].result["target_price_observed_after_entry"] is True
            assert evaluations[0].result["execution"] == "unknown"
            assert evaluations[1].status == "unknown"
            assert evaluations[1].result["price_change_percent"] is None
    finally:
        await db.close()
