"""Common interface for blockchain collectors."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from stellar_intelligence_bot.core.events import ChainEvent
from stellar_intelligence_bot.core.markets import MarketPair


@dataclass(frozen=True, slots=True)
class CollectedEvent:
    event: ChainEvent | None
    stream_key: str | None = None
    cursor: str | None = None
    pair: MarketPair | None = None
    base_amount: Decimal | None = None
    quote_amount: Decimal | None = None
    is_market_snapshot: bool = False


class ChainCollector(Protocol):
    """Produces normalized events without exposing transaction execution."""

    def events(self) -> AsyncIterator[CollectedEvent]:
        """Yield new events as they are collected."""
        ...

    async def close(self) -> None:
        """Release network and other collector resources."""
        ...
