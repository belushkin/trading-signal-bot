"""Explain a volume window from persisted trades without inferring missing facts."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from statistics import mean, median
from typing import Any

VENUES = ("order_book", "liquidity_pool")
DIRECTION_NOTE = (
    "Orientation of the recorded trade as reported by Horizon (whether the base-asset holder "
    "was the seller). The aggressor or taker side is not provided and is not inferred."
)


def _text(value: Decimal) -> str:
    return format(value, "f")


def _share(part: Decimal, whole: Decimal) -> str | None:
    return _text(part * 100 / whole) if whole > 0 else None


def _bucket() -> dict[str, Any]:
    return {"count": 0, "base_volume": Decimal(0), "quote_volume": Decimal(0)}


def _public(bucket: dict[str, Any]) -> dict[str, object]:
    return {
        "count": bucket["count"],
        "base_volume": _text(bucket["base_volume"]),
        "quote_volume": _text(bucket["quote_volume"]),
    }


def _iso(value: datetime) -> str:
    moment = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return moment.isoformat().replace("+00:00", "Z")


def _trade_summary(trade: Any) -> dict[str, object]:
    return {
        "trade_id": trade.trade_id,
        "occurred_at": _iso(trade.occurred_at),
        "base_amount": _text(trade.base_amount),
        "quote_amount": _text(trade.quote_amount),
        "price": _text(trade.price),
        "venue": trade.venue,
        "source_ref": trade.source_ref,
    }


def trade_window_evidence(trades: Sequence[Any]) -> dict[str, object]:
    """Summarise trades of one window; None fields stay unknown and are counted as such."""
    total_base = sum((trade.base_amount for trade in trades), Decimal(0))
    total_quote = sum((trade.quote_amount for trade in trades), Decimal(0))
    venues: dict[str, dict[str, Any]] = {name: _bucket() for name in (*VENUES, "unknown")}
    direction: dict[str, dict[str, Any]] = {
        "base_is_seller_true": _bucket(),
        "base_is_seller_false": _bucket(),
        "unknown": _bucket(),
    }
    operations: dict[str, dict[str, Any]] = defaultdict(_bucket)
    for trade in trades:
        venue = trade.venue if trade.venue in VENUES else "unknown"
        orientation = {True: "base_is_seller_true", False: "base_is_seller_false"}.get(
            trade.base_is_seller, "unknown"
        )
        targets = [venues[venue], direction[orientation]]
        if trade.operation_id:
            targets.append(operations[trade.operation_id])
        for bucket in targets:
            bucket["count"] += 1
            bucket["base_volume"] += trade.base_amount
            bucket["quote_volume"] += trade.quote_amount

    by_base = sorted(trades, key=lambda item: (-item.base_amount, item.trade_id))
    by_quote = sorted(trades, key=lambda item: (-item.quote_amount, item.trade_id))
    ranked_operations = sorted(
        operations.items(), key=lambda item: (-item[1]["base_volume"], item[0])
    )
    sizes = [trade.base_amount for trade in trades]
    with_venue = sum(trade.venue in VENUES for trade in trades)
    with_orientation = sum(trade.base_is_seller is not None for trade in trades)
    with_operation = sum(bool(trade.operation_id) for trade in trades)
    accounts = {
        account
        for trade in trades
        for account in (trade.base_account, trade.counter_account)
        if account
    }
    top_operation_volume = sum(
        (bucket["base_volume"] for _, bucket in ranked_operations[:5]), Decimal(0)
    )
    return {
        "trade_count": len(trades),
        "base_volume": _text(total_base),
        "quote_volume": _text(total_quote),
        "venues": {name: _public(bucket) for name, bucket in venues.items()},
        "venue_sum_matches_total": sum(
            (bucket["base_volume"] for bucket in venues.values()), Decimal(0)
        )
        == total_base,
        "coverage": {
            "venue_trades": with_venue,
            "orientation_trades": with_orientation,
            "operation_trades": with_operation,
            "total_trades": len(trades),
        },
        "largest_trade_share_percent": _share(by_base[0].base_amount, total_base)
        if by_base
        else None,
        "largest_trades_by_base": [_trade_summary(trade) for trade in by_base[:5]],
        "largest_trades_by_quote": [_trade_summary(trade) for trade in by_quote[:5]],
        "mean_trade_base_size": _text(mean(sizes)) if sizes else None,
        "median_trade_base_size": _text(median(sizes)) if sizes else None,
        "distinct_operations": len(operations) if with_operation else None,
        "top_operations": [
            {
                "operation_id": key,
                "trade_count": bucket["count"],
                "base_volume": _text(bucket["base_volume"]),
                "share_percent": _share(bucket["base_volume"], total_base),
            }
            for key, bucket in ranked_operations[:5]
        ],
        "top_operations_share_percent": _share(top_operation_volume, total_base)
        if with_operation == len(trades) and trades
        else None,
        "direction": {
            "note": DIRECTION_NOTE,
            **{name: _public(bucket) for name, bucket in direction.items()},
        },
        "distinct_accounts": len(accounts) if accounts else None,
    }


def account_diagnostics(trades: Sequence[Any], limit: int = 10) -> dict[str, object]:
    """Optional diagnostic view: per-account participation, only where accounts are stored."""
    volume: dict[str, dict[str, Any]] = defaultdict(_bucket)
    for trade in trades:
        for account in (trade.base_account, trade.counter_account):
            if account:
                volume[account]["count"] += 1
                volume[account]["base_volume"] += trade.base_amount
                volume[account]["quote_volume"] += trade.quote_amount
    ranked = sorted(volume.items(), key=lambda item: (-item[1]["base_volume"], item[0]))
    return {
        "accounts_observed": len(volume),
        "trades_with_accounts": sum(
            bool(trade.base_account or trade.counter_account) for trade in trades
        ),
        "total_trades": len(trades),
        "top_accounts": [{"account": key, **_public(bucket)} for key, bucket in ranked[:limit]],
        "note": "Participation counts both sides of each trade. No intent is inferred.",
    }
