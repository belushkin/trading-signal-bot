"""Cursor-aware Stellar operations, trades, and order-book observations."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import urlencode

from stellar_intelligence_bot.chains.base import CollectedEvent
from stellar_intelligence_bot.chains.stellar.client import HorizonClient
from stellar_intelligence_bot.core.events import Chain, ChainEvent
from stellar_intelligence_bot.core.markets import MarketPair

EXPLORER_TX = "https://stellar.expert/explorer/public/tx"


class StellarCollector:
    def __init__(
        self,
        client: HorizonClient,
        pairs: tuple[MarketPair, ...],
        cursors: CursorReader,
    ) -> None:
        self.client = client
        self.pairs = pairs
        self.cursors = cursors

    async def events(self) -> AsyncIterator[CollectedEvent]:
        async for item in self._ledger_events():
            yield item
        async for item in self._operation_events():
            yield item
        for pair in self.pairs:
            async for item in self._trade_events(pair):
                yield item
            yield await self._market_snapshot(pair)

    async def _operation_events(self) -> AsyncIterator[CollectedEvent]:
        stream = "stellar:operations"
        cursor = await self.cursors.get_cursor(stream)
        async for record in self.client.records(
            "operations", cursor=cursor, params={"include_failed": "false"}
        ):
            event = parse_payment_operation(record)
            page_cursor = str(record["paging_token"])
            if event is not None:
                yield CollectedEvent(event, stream_key=stream, cursor=page_cursor)
            else:
                yield CollectedEvent(None, stream_key=stream, cursor=page_cursor)

    async def _ledger_events(self) -> AsyncIterator[CollectedEvent]:
        stream = "stellar:ledgers"
        cursor = await self.cursors.get_cursor(stream)
        async for record in self.client.records("ledgers", cursor=cursor):
            page_cursor = str(record["paging_token"])
            sequence = record.get("sequence")
            if not isinstance(sequence, int):
                raise ValueError("Malformed Horizon ledger: missing integer sequence")
            closed_at = _parse_time(record.get("closed_at"))
            event = ChainEvent(
                event_id=f"stellar:ledger:{sequence}",
                chain=Chain.STELLAR,
                event_type="ledger",
                occurred_at=closed_at,
                source_ref=f"https://stellar.expert/explorer/public/ledger/{sequence}",
                attributes={
                    "sequence": sequence,
                    "hash": str(record.get("hash", "")),
                    "successful_transaction_count": int(
                        record.get("successful_transaction_count", 0)
                    ),
                    "failed_transaction_count": int(record.get("failed_transaction_count", 0)),
                },
            )
            yield CollectedEvent(event, stream_key=stream, cursor=page_cursor)

    async def _trade_events(self, pair: MarketPair) -> AsyncIterator[CollectedEvent]:
        stream = f"stellar:trades:{pair.key}"
        cursor = await self.cursors.get_cursor(stream)
        async for record in self.client.records(
            "trades", cursor=cursor, params=pair.horizon_params()
        ):
            try:
                event, base_amount, quote_amount = parse_trade(record, pair)
            except (KeyError, ValueError, InvalidOperation) as exc:
                raise ValueError(f"Malformed Horizon trade on {pair.key}") from exc
            yield CollectedEvent(
                event,
                stream_key=stream,
                cursor=str(record["paging_token"]),
                pair=pair,
                base_amount=base_amount,
                quote_amount=quote_amount,
            )

    async def _market_snapshot(self, pair: MarketPair) -> CollectedEvent:
        payload = await self.client.order_book(pair)
        observed_at = datetime.now(UTC)
        bids = _parse_order_levels(payload.get("bids", []), "bids")
        asks = _parse_order_levels(payload.get("asks", []), "asks")
        best_bid = max((price for price, _ in bids), default=None)
        best_ask = min((price for price, _ in asks), default=None)
        bid_depth = sum((amount for _, amount in bids), Decimal(0))
        ask_depth = sum((amount for _, amount in asks), Decimal(0))
        trade_facts: dict[str, str | int | Decimal | None] = {
            "pair": pair.key,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "bid_depth_base": bid_depth,
            "ask_depth_base": ask_depth,
            "largest_bid_amount": max((amount for _, amount in bids), default=Decimal(0)),
            "largest_ask_amount": max((amount for _, amount in asks), default=Decimal(0)),
            "bid_levels": len(bids),
            "ask_levels": len(asks),
        }
        source_ref = (
            f"{self.client.base_url}/order_book?"
            f"{urlencode(pair.horizon_params() | {'limit': '20'})}"
        )
        event = ChainEvent(
            event_id=f"stellar:order_book:{pair.key}:{observed_at.isoformat()}",
            chain=Chain.STELLAR,
            event_type="market_snapshot",
            occurred_at=observed_at,
            source_ref=source_ref,
            attributes=trade_facts,
        )
        return CollectedEvent(event, pair=pair, is_market_snapshot=True)

    async def close(self) -> None:
        await self.client.close()


class CursorReader(Protocol):
    async def get_cursor(self, stream_key: str) -> str | None: ...


def parse_payment_operation(record: Mapping[str, Any]) -> ChainEvent | None:
    operation_type = record.get("type")
    payment_types = {
        "payment",
        "path_payment_strict_send",
        "path_payment_strict_receive",
    }
    if operation_type not in payment_types:
        return None
    amount = _decimal(record.get("amount"))
    asset_code = record.get("asset_code")
    asset_issuer = record.get("asset_issuer")
    asset = "native" if record.get("asset_type") == "native" else f"{asset_code}:{asset_issuer}"
    transaction_hash = record.get("transaction_hash", "unknown")
    operation_id = str(record.get("id", record.get("paging_token", "")))
    occurred_at = _parse_time(record.get("created_at"))
    facts: dict[str, str | Decimal] = {
        "amount": amount,
        "asset": asset,
        "source_account": str(record.get("from", record.get("source_account", ""))),
        "destination_account": str(record.get("to", "")),
        "operation_type": str(operation_type),
    }
    return ChainEvent(
        event_id=f"stellar:operation:{operation_id}",
        chain=Chain.STELLAR,
        event_type="payment",
        occurred_at=occurred_at,
        source_ref=f"{EXPLORER_TX}/{transaction_hash}",
        attributes=facts,
    )


def parse_trade(record: Mapping[str, Any], pair: MarketPair) -> tuple[ChainEvent, Decimal, Decimal]:
    base_amount = _decimal(record["base_amount"])
    quote_amount = _decimal(record["counter_amount"])
    if base_amount <= 0 or quote_amount <= 0:
        raise ValueError("Trade amounts must be positive")
    occurred_at = _parse_time(record["ledger_close_time"])
    trade_id = str(record.get("id", record["paging_token"]))
    transaction_hash = record.get("transaction_hash")
    links = record.get("_links", {})
    operation_link = links.get("operation", {}) if isinstance(links, Mapping) else {}
    operation_href = operation_link.get("href") if isinstance(operation_link, Mapping) else None
    if isinstance(transaction_hash, str) and transaction_hash:
        source_ref = f"{EXPLORER_TX}/{transaction_hash}"
    elif isinstance(operation_href, str) and operation_href:
        source_ref = operation_href
    else:
        source_ref = f"https://horizon.stellar.org/trades/{trade_id}"
    attributes: dict[str, str | Decimal] = {
        "pair": pair.key,
        "base_amount": base_amount,
        "quote_amount": quote_amount,
        "price": quote_amount / base_amount,
    }
    event = ChainEvent(
        event_id=f"stellar:trade:{trade_id}",
        chain=Chain.STELLAR,
        event_type="trade",
        occurred_at=occurred_at,
        source_ref=source_ref,
        attributes=attributes,
    )
    return event, base_amount, quote_amount


def _parse_order_levels(values: object, field: str) -> list[tuple[Decimal, Decimal]]:
    if not isinstance(values, list):
        raise ValueError(f"Order-book {field} must be a list")
    levels: list[tuple[Decimal, Decimal]] = []
    for level in values:
        if not isinstance(level, Mapping):
            raise ValueError(f"Malformed order-book {field} entry")
        price = _decimal(level.get("price"))
        amount = _decimal(level.get("amount"))
        if price <= 0 or amount < 0:
            raise ValueError(f"Invalid order-book {field} price or amount")
        levels.append((price, amount))
    return levels


def _decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid decimal value {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"Non-finite decimal value {value!r}")
    return result


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Missing or invalid Horizon timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Horizon timestamp must include a timezone")
    return parsed.astimezone(UTC)
