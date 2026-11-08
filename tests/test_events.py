from datetime import UTC, datetime
from decimal import Decimal

import pytest

from stellar_intelligence_bot.core.events import Chain, ChainEvent


def test_chain_event_accepts_chain_specific_attributes() -> None:
    event = ChainEvent(
        event_id="stellar:tx-1:payment-0",
        chain=Chain.STELLAR,
        event_type="payment",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_ref="https://example.test/transactions/tx-1",
        attributes={"amount": Decimal("12.5"), "asset": "USD:issuer"},
    )

    assert event.chain is Chain.STELLAR
    assert event.attributes["amount"] == Decimal("12.5")


def test_chain_event_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ChainEvent(
            event_id="event-1",
            chain=Chain.STELLAR,
            event_type="payment",
            occurred_at=datetime(2026, 1, 1),
            source_ref="source-1",
        )


def test_chain_event_attributes_are_immutable() -> None:
    attributes = {"amount": Decimal("1")}
    event = ChainEvent(
        event_id="event-1",
        chain=Chain.XRPL,
        event_type="payment",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_ref="source-1",
        attributes=attributes,
    )
    attributes["amount"] = Decimal("2")

    assert event.attributes["amount"] == Decimal("1")
    with pytest.raises(TypeError):
        event.attributes["amount"] = Decimal("3")  # type: ignore[index]
