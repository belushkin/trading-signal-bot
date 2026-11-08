from datetime import UTC, datetime
from decimal import Decimal

import pytest

from stellar_intelligence_bot.chains.stellar.collector import (
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
    with pytest.raises(ValueError, match="Trade amounts"):
        parse_trade(
            {
                "paging_token": "44",
                "base_amount": "0",
                "counter_amount": "6",
                "ledger_close_time": "2026-01-01T00:00:00Z",
            },
            pair,
        )


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
