"""Deterministic, evidence-first signal rules for the Stellar MVP."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from stellar_intelligence_bot.core.events import ChainEvent
from stellar_intelligence_bot.core.signals import Confidence, Severity, Signal

SIGNAL_COOLDOWN = timedelta(minutes=15)
SIGNAL_TTL = timedelta(minutes=30)
EVALUATION_HORIZONS_MINUTES = (5, 15, 60, 240, 1440)


def whale_signal(event: ChainEvent, threshold: Decimal | None) -> Signal | None:
    if threshold is None:
        return None
    amount = _decimal(event.attributes.get("amount"))
    asset = event.attributes.get("asset")
    if amount is None or not isinstance(asset, str) or amount < threshold:
        return None
    account = event.attributes.get("source_account")
    dedupe = f"whale:{asset}:{account or 'unknown'}"
    severity = Severity.HIGH if amount >= threshold * 10 else Severity.WATCH
    return _signal(
        event,
        category="whale_transfer",
        dedupe_key=dedupe,
        severity=severity,
        facts={
            "amount": str(amount),
            "asset": asset,
            "source_account": account,
            "destination_account": event.attributes.get("destination_account"),
            "configured_threshold": str(threshold),
        },
        interpretation=(
            "A payment met the configured transfer-size threshold; "
            "intent and ownership are unknown."
        ),
    )


def volume_signal(
    event: ChainEvent,
    pair_key: str,
    current_volume: Decimal,
    baseline_volume: Decimal,
    baseline_samples: int,
    multiplier: Decimal,
) -> Signal | None:
    trigger = baseline_volume * multiplier
    if baseline_samples <= 0 or baseline_volume <= 0 or current_volume < trigger:
        return None
    return _signal(
        event,
        category="unusual_volume",
        dedupe_key=f"volume:{pair_key}",
        severity=Severity.HIGH if current_volume >= trigger * 3 else Severity.WATCH,
        facts=_with_reference_price(
            event,
            {
                "pair": pair_key,
                "current_five_minute_base_volume": str(current_volume),
                "baseline_mean_base_volume": str(baseline_volume),
                "baseline_windows": baseline_samples,
                "trigger_multiplier": str(multiplier),
            },
        ),
        interpretation=(
            "Observed five-minute base-asset volume exceeds the configured "
            "multiplier of recent baseline windows."
        ),
    )


def order_book_signals(
    event: ChainEvent,
    pair_key: str,
    *,
    large_order_threshold: Decimal,
    spread_threshold_percent: Decimal,
) -> list[Signal]:
    facts = event.attributes
    bid = _decimal(facts.get("best_bid"))
    ask = _decimal(facts.get("best_ask"))
    bid_depth = _decimal(facts.get("bid_depth_base")) or Decimal(0)
    ask_depth = _decimal(facts.get("ask_depth_base")) or Decimal(0)
    signals: list[Signal] = []
    levels: list[tuple[str, Decimal]] = []
    for key in ("largest_bid_amount", "largest_ask_amount"):
        amount = _decimal(facts.get(key))
        if amount is not None:
            levels.append((key, amount))

    large_levels = [(side, amount) for side, amount in levels if amount >= large_order_threshold]
    if large_levels:
        signals.append(
            _signal(
                event,
                category="visible_liquidity",
                dedupe_key=f"visible-liquidity:{pair_key}",
                severity=Severity.WATCH,
                facts=_with_reference_price(
                    event,
                    {
                        "pair": pair_key,
                        "large_visible_levels": [
                            {"side": side, "base_amount": str(amount)}
                            for side, amount in large_levels
                        ],
                        "configured_base_amount_threshold": str(large_order_threshold),
                        "best_bid": str(bid) if bid is not None else None,
                        "best_ask": str(ask) if ask is not None else None,
                        "bid_depth_base": str(bid_depth),
                        "ask_depth_base": str(ask_depth),
                    },
                ),
                interpretation=(
                    "A large order is visible in the current snapshot; it may change or cancel "
                    "and is not evidence of execution."
                ),
            )
        )

    if bid is not None and ask is not None and ask > 0 and ask >= bid:
        spread = (ask - bid) / ask * Decimal(100)
        if spread >= spread_threshold_percent:
            signals.append(
                _signal(
                    event,
                    category="wide_spread",
                    dedupe_key=f"wide-spread:{pair_key}",
                    severity=Severity.WATCH,
                    facts=_with_reference_price(
                        event,
                        {
                            "pair": pair_key,
                            "best_bid": str(bid),
                            "best_ask": str(ask),
                            "spread_percent": str(spread),
                            "configured_threshold_percent": str(spread_threshold_percent),
                        },
                    ),
                    interpretation=(
                        "The current order-book snapshot has a wide best-bid/best-ask spread."
                    ),
                )
            )
    return signals


def _with_reference_price(event: ChainEvent, facts: dict[str, object]) -> dict[str, object]:
    bid = _decimal(event.attributes.get("best_bid"))
    ask = _decimal(event.attributes.get("best_ask"))
    if bid is not None and ask is not None and bid > 0 and ask >= bid:
        facts["reference_price"] = str((bid + ask) / Decimal(2))
        facts["reference_source"] = event.source_ref
    return facts


def _signal(
    event: ChainEvent,
    *,
    category: str,
    dedupe_key: str,
    severity: Severity,
    facts: dict[str, object],
    interpretation: str,
    confidence: Confidence = Confidence.HIGH,
) -> Signal:
    signal_id = hashlib.sha256(f"{event.event_id}:{category}:{dedupe_key}".encode()).hexdigest()
    return Signal(
        signal_id=signal_id,
        deduplication_key=dedupe_key,
        event_id=event.event_id,
        occurred_at=event.occurred_at,
        category=category,
        severity=severity,
        confidence=confidence,
        observed_facts=facts,
        interpretation=interpretation,
        expires_at=event.occurred_at + SIGNAL_TTL,
    )


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid signal measurement: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"Invalid signal measurement: {value!r}")
    return parsed
