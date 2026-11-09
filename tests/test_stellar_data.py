from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from stellar_intelligence_bot.chains.stellar.collector import (
    StellarCollector,
    _parse_order_levels,
    parse_payment_operation,
    parse_trade,
)
from stellar_intelligence_bot.core.markets import MarketPair


def test_parse_payment_operation_keeps_transfer_facts_neutral() -> None:
    event = parse_payment_operation(
        {
            "type": "payment",
            "id": "123",
            "paging_token": "123",
            "amount": "250.5",
            "asset_type": "native",
            "from": "G" + "A" * 55,
            "to": "G" + "B" * 55,
            "transaction_hash": "abc",
            "created_at": "2026-01-01T00:00:00Z",
        }
    )

    assert event is not None
    assert event.attributes["amount"] == Decimal("250.5")
    assert event.attributes["asset"] == "native"
    assert event.occurred_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert "buy" not in event.attributes


def test_non_payment_operations_are_skipped() -> None:
    assert parse_payment_operation({"type": "manage_sell_offer"}) is None


def test_parse_trade_uses_explicit_base_and_quote_amounts() -> None:
    issuer = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
    pair = MarketPair.parse(f"native/USDC:{issuer}")
    event, base, quote = parse_trade(
        {
            "paging_token": "44",
            "id": "trade-44",
            "base_amount": "2",
            "counter_amount": "6",
            "ledger_close_time": "2026-01-01T00:00:00Z",
            "transaction_hash": "tx",
        },
        pair,
    )

    assert base == Decimal("2")
    assert quote == Decimal("6")
    assert event.attributes["price"] == Decimal("3")


def test_parse_trade_rejects_zero_base_amount() -> None:
    issuer = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
    pair = MarketPair.parse(f"native/USDC:{issuer}")
    with pytest.raises(ValueError, match="Trade base amount"):
        parse_trade(
            {
                "paging_token": "44",
                "base_amount": "0",
                "counter_amount": "6",
                "ledger_close_time": "2026-01-01T00:00:00Z",
            },
            pair,
        )


def test_zero_quote_trade_preserves_horizon_price_and_actual_amounts() -> None:
    pair = MarketPair.parse("native/USDC:GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")
    item, base, quote = parse_trade(
        {
            "paging_token": "44", "base_amount": "0.0000001", "counter_amount": "0",
            "ledger_close_time": "2026-01-01T00:00:00Z",
            "price": {"n": "147361", "d": "1000000"},
        },
        pair,
    )
    assert base == Decimal("0.0000001")
    assert quote == 0
    assert item.attributes["price"] == Decimal("0.147361")
    assert item.attributes["price_source"] == "horizon_rational"


def test_trade_reference_uses_horizon_operation_link_when_hash_is_unavailable() -> None:
    issuer = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
    pair = MarketPair.parse(f"native/USDC:{issuer}")
    event, _, _ = parse_trade(
        {
            "id": "trade-44",
            "paging_token": "44",
            "base_amount": "2",
            "counter_amount": "6",
            "ledger_close_time": "2026-01-01T00:00:00Z",
            "_links": {"operation": {"href": "https://horizon.stellar.org/operations/44"}},
        },
        pair,
    )

    assert event.source_ref == "https://horizon.stellar.org/operations/44"


@pytest.mark.asyncio
async def test_order_book_bid_depth_converts_quote_to_base() -> None:
    pair = MarketPair.parse("native/USDC:GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")
    client = MagicMock()
    client.base_url = "https://horizon.stellar.org"
    client.order_book = AsyncMock(
        return_value={
            "bids": [{"price": "0.5", "amount": "10", "price_r": {"n": 1, "d": 2}}],
            "asks": [{"price": "0.6", "amount": "8", "price_r": {"n": 3, "d": 5}}],
        }
    )
    collector = StellarCollector(client, (pair,), MagicMock())
    item = await collector._market_snapshot(pair)
    assert item.event is not None
    assert item.event.attributes["bid_depth_base"] == Decimal("20")
    assert item.event.attributes["ask_depth_base"] == Decimal("8")
    assert item.event.attributes["bid_depth_units"] == "base"
    assert "selling_asset_type=native" in item.event.source_ref
    assert "buying_asset_code=USDC" in item.event.source_ref


@pytest.mark.asyncio
async def test_trade_watermark_requires_an_existing_cursor_and_completed_scan() -> None:
    pair = MarketPair.parse("native/USDC:GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF")
    watermark = datetime.now(UTC) - timedelta(seconds=5)

    async def no_records(*args: object, **kwargs: object):
        if False:
            yield {}

    client = MagicMock()
    client.base_url = "https://horizon.stellar.org"
    client.records = no_records
    client.get_json = AsyncMock(
        return_value={"history_latest_ledger_closed_at": watermark.isoformat()}
    )
    client.order_book = AsyncMock(return_value={"bids": [], "asks": []})
    cursors = MagicMock()
    cursors.get_cursor = AsyncMock(return_value=None)
    collector = StellarCollector(client, (pair,), cursors)
    initial = [item async for item in collector.events()]
    assert initial[0].event is not None
    assert "trade_scan_complete_through" not in initial[0].event.attributes

    cursors.get_cursor = AsyncMock(return_value="existing")
    subsequent = [item async for item in collector.events()]
    assert subsequent[0].event is not None
    assert subsequent[0].event.attributes["trade_scan_complete_through"] == watermark.isoformat()

    async def failing_trade_scan(path: str, *args: object, **kwargs: object):
        if path == "trades":
            raise ValueError("trade scan failed")
        if False:
            yield {}

    client.records = failing_trade_scan
    with pytest.raises(ValueError, match="trade scan failed"):
        _ = [item async for item in collector.events()]


def test_order_book_uses_unrounded_rational_prices() -> None:
    levels = _parse_order_levels(
        [{"price": "0.0000019", "price_r": {"n": 1, "d": 500001}, "amount": "10"}],
        "bids",
    )
    assert levels[0][0] == Decimal(1) / Decimal(500001)


def test_order_book_rejects_invalid_rational_denominator() -> None:
    with pytest.raises(ValueError, match="denominator"):
        _parse_order_levels([{"price_r": {"n": 1, "d": 0}, "amount": "10"}], "bids")
