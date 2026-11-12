"""Cursor-aware Stellar operations, trades, and order-book observations."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import urlencode

from stellar_intelligence_bot.chains.base import (
    CollectedEvent,
    OrderBookLevel,
    OrderBookLevels,
    TradeProvenance,
)
from stellar_intelligence_bot.chains.stellar.client import MALFORMED_RECORD_KEY, HorizonClient
from stellar_intelligence_bot.core.events import Chain, ChainEvent
from stellar_intelligence_bot.core.markets import MarketPair

logger = logging.getLogger(__name__)
EXPLORER_TX = "https://stellar.expert/explorer/public/tx"
MAX_CHAIN_PAGES_PER_CYCLE = 5


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
        if not self.pairs:
            return
        horizon = await self.client.get_json("")
        watermark = _parse_time(horizon.get("history_latest_ledger_closed_at"))
        for pair in self.pairs:
            has_cursor = await self.cursors.get_cursor(f"stellar:trades:{pair.key}") is not None
            async for item in self._trade_events(pair):
                yield item
            yield await self._market_snapshot(
                pair, trade_watermark=watermark if has_cursor else None
            )

    async def _operation_events(self) -> AsyncIterator[CollectedEvent]:
        stream = "stellar:operations"
        cursor = await self.cursors.get_cursor(stream)
        async for record in self.client.records(
            "operations", cursor=cursor, params={"include_failed": "false"},
            max_pages=MAX_CHAIN_PAGES_PER_CYCLE,
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
        async for record in self.client.records(
            "ledgers", cursor=cursor, max_pages=MAX_CHAIN_PAGES_PER_CYCLE
        ):
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
            token = record.get("paging_token")
            record_cursor = token if isinstance(token, str) and token else None
            if MALFORMED_RECORD_KEY in record:
                reason = "malformed_record: Horizon returned a non-object trade record"
                logger.warning(
                    "Skipping malformed Horizon trade record pair=%s detail=%s",
                    pair.key,
                    record[MALFORMED_RECORD_KEY],
                )
                yield CollectedEvent(None, stream_key=stream, cursor=None, skipped_reason=reason)
                continue
            if _is_zero_base_dust(record):
                # Horizon rounds sub-stroop base amounts to zero; nothing meaningful to store.
                logger.info(
                    "Skipping zero-base dust trade pair=%s trade_id=%s",
                    pair.key,
                    record.get("id"),
                )
                yield CollectedEvent(
                    None,
                    stream_key=stream,
                    cursor=record_cursor,
                    skipped_reason="dust_zero_base_amount",
                )
                continue
            try:
                event, base_amount, quote_amount = parse_trade(record, pair)
                provenance = parse_trade_provenance(record, pair, observed_at=datetime.now(UTC))
            except (KeyError, ValueError, TypeError, AttributeError, InvalidOperation) as exc:
                reason = f"malformed_trade: {exc}"[:300]
                logger.warning(
                    "Skipping malformed Horizon trade pair=%s trade_id=%s paging_token=%s "
                    "reason=%s",
                    pair.key,
                    record.get("id"),
                    token,
                    reason,
                )
                yield CollectedEvent(
                    None, stream_key=stream, cursor=record_cursor, skipped_reason=reason
                )
                continue
            yield CollectedEvent(
                event,
                stream_key=stream,
                cursor=str(record["paging_token"]),
                pair=pair,
                base_amount=base_amount,
                quote_amount=quote_amount,
                provenance=provenance,
            )

    async def _market_snapshot(
        self, pair: MarketPair, *, trade_watermark: datetime | None = None
    ) -> CollectedEvent:
        payload = await self.client.order_book(pair)
        observed_at = datetime.now(UTC)
        raw_bids = _parse_order_levels(payload.get("bids", []), "bids")
        bids = [(price, amount / price) for price, amount in raw_bids]
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
            "bid_depth_units": "base",
        }
        if trade_watermark is not None:
            if trade_watermark > observed_at:
                raise ValueError("Horizon trade watermark is in the future")
            trade_facts["trade_scan_complete_through"] = trade_watermark.isoformat()
        source_ref = (
            f"{self.client.base_url}/order_book?"
            f"{urlencode(pair.order_book_params() | {'limit': '20'})}"
        )
        event = ChainEvent(
            event_id=f"stellar:order_book:{pair.key}:{observed_at.isoformat()}",
            chain=Chain.STELLAR,
            event_type="market_snapshot",
            occurred_at=observed_at,
            source_ref=source_ref,
            attributes=trade_facts,
        )
        return CollectedEvent(
            event,
            pair=pair,
            is_market_snapshot=True,
            order_book_levels=OrderBookLevels(
                bids=_level_evidence(payload.get("bids", []), raw_bids),
                asks=_level_evidence(payload.get("asks", []), asks),
            ),
        )

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


def parse_trade_time(record: Mapping[str, Any]) -> datetime:
    return _parse_time(record["ledger_close_time"])


def _is_zero_base_dust(record: Mapping[str, Any]) -> bool:
    try:
        return _decimal(record["base_amount"]) == 0
    except (KeyError, ValueError, InvalidOperation):
        return False


def _asset_key(record: Mapping[str, Any], prefix: str) -> str | None:
    kind = record.get(f"{prefix}_asset_type")
    if kind == "native":
        return "native"
    code = record.get(f"{prefix}_asset_code")
    issuer = record.get(f"{prefix}_asset_issuer")
    if isinstance(code, str) and isinstance(issuer, str) and code and issuer:
        return f"{code}:{issuer}"
    return None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_trade_provenance(
    record: Mapping[str, Any], pair: MarketPair, *, observed_at: datetime | None
) -> TradeProvenance:
    """Extract source facts without inferring anything the Horizon record does not state."""
    base_asset = _asset_key(record, "base")
    counter_asset = _asset_key(record, "counter")
    if (base_asset is not None and base_asset != pair.base.key) or (
        counter_asset is not None and counter_asset != pair.quote.key
    ):
        raise ValueError(
            f"Trade asset identity {base_asset}/{counter_asset} does not match {pair.key}"
        )
    trade_type = record.get("trade_type")
    venue = {"orderbook": "order_book", "liquidity_pool": "liquidity_pool"}.get(str(trade_type))
    base_is_seller = record.get("base_is_seller")
    links = record.get("_links")
    operation = links.get("operation") if isinstance(links, Mapping) else None
    href = operation.get("href") if isinstance(operation, Mapping) else None
    operation_id = href.rstrip("/").rsplit("/", 1)[-1] if isinstance(href, str) else None
    if operation_id is not None and not operation_id.isdigit():
        operation_id = None
    return TradeProvenance(
        venue=venue,
        base_is_seller=base_is_seller if isinstance(base_is_seller, bool) else None,
        base_asset=base_asset,
        counter_asset=counter_asset,
        operation_id=operation_id,
        transaction_hash=_text(record.get("transaction_hash")),
        base_account=_text(record.get("base_account")),
        counter_account=_text(record.get("counter_account")),
        liquidity_pool_id=_text(record.get("base_liquidity_pool_id"))
        or _text(record.get("counter_liquidity_pool_id")),
        observed_at=observed_at,
    )


def parse_trade(record: Mapping[str, Any], pair: MarketPair) -> tuple[ChainEvent, Decimal, Decimal]:
    base_amount = _decimal(record["base_amount"])
    quote_amount = _decimal(record["counter_amount"])
    if base_amount <= 0 or quote_amount < 0:
        raise ValueError("Trade base amount must be positive and quote amount nonnegative")
    if quote_amount == 0:
        raw_price = record.get("price")
        if not isinstance(raw_price, Mapping):
            raise ValueError("Zero-quote trade must include a rational Horizon price")
        numerator = _decimal(raw_price.get("n"))
        denominator = _decimal(raw_price.get("d"))
        if numerator <= 0 or denominator <= 0:
            raise ValueError("Zero-quote trade must include a positive rational price")
        trade_price = numerator / denominator
    else:
        trade_price = quote_amount / base_amount
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
        "price": trade_price,
        "price_source": "horizon_rational" if quote_amount == 0 else "executed_amounts",
    }
    trade_type = record.get("trade_type")
    if trade_type in ("orderbook", "liquidity_pool"):
        attributes["trade_type"] = str(trade_type)
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
        rational = level.get("price_r")
        if isinstance(rational, Mapping):
            numerator = _decimal(rational.get("n"))
            denominator = _decimal(rational.get("d"))
            if denominator <= 0:
                raise ValueError(f"Invalid order-book {field} price denominator")
            price = numerator / denominator
        else:
            price = _decimal(level.get("price"))
        amount = _decimal(level.get("amount"))
        if price <= 0 or amount < 0:
            raise ValueError(f"Invalid order-book {field} price or amount")
        levels.append((price, amount))
    return levels


def _level_evidence(
    values: object, parsed: list[tuple[Decimal, Decimal]]
) -> tuple[OrderBookLevel, ...]:
    if not isinstance(values, list) or len(values) != len(parsed):
        raise ValueError("Order-book levels changed after validation")
    result = []
    for level, (price, amount) in zip(values, parsed, strict=True):
        if not isinstance(level, Mapping):
            raise ValueError("Malformed order-book level")
        rational = level.get("price_r")
        result.append(
            OrderBookLevel(
                price=str(price),
                amount=str(amount),
                price_numerator=str(rational["n"]) if isinstance(rational, Mapping) else None,
                price_denominator=str(rational["d"]) if isinstance(rational, Mapping) else None,
            )
        )
    return tuple(result)


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
