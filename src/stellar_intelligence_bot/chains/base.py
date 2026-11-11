"""Common interface for blockchain collectors."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from stellar_intelligence_bot.core.events import ChainEvent
from stellar_intelligence_bot.core.markets import MarketPair


@dataclass(frozen=True, slots=True)
class TradeProvenance:
    """Source facts about one trade; None means the source did not provide the field."""

    venue: str | None = None
    base_is_seller: bool | None = None
    base_asset: str | None = None
    counter_asset: str | None = None
    operation_id: str | None = None
    transaction_hash: str | None = None
    base_account: str | None = None
    counter_account: str | None = None
    liquidity_pool_id: str | None = None
    observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CollectedEvent:
    event: ChainEvent | None
    stream_key: str | None = None
    cursor: str | None = None
    pair: MarketPair | None = None
    base_amount: Decimal | None = None
    quote_amount: Decimal | None = None
    is_market_snapshot: bool = False
    provenance: TradeProvenance | None = None
    skipped_reason: str | None = None


class ChainCollector(Protocol):
    """Produces normalized events without exposing transaction execution."""

    def events(self) -> AsyncIterator[CollectedEvent]:
        """Yield new events as they are collected."""
        ...

    async def close(self) -> None:
        """Release network and other collector resources."""
        ...
