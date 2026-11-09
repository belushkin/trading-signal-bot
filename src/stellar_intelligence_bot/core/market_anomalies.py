"""Conservative anomaly rules using only timestamped order-book observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median

from stellar_intelligence_bot.core.events import ChainEvent
from stellar_intelligence_bot.core.signal_engine import _signal
from stellar_intelligence_bot.core.signals import Confidence, Severity, Signal

MAX_GAP = timedelta(minutes=2)
SPREAD_HISTORY = timedelta(hours=1)
PRICE_WINDOWS = (5, 15, 60, 240)
MINIMUM_SPREAD_SAMPLES = 20
BASELINE_WINDOWS = 3
LARGE_TRADE_HISTORY = timedelta(hours=1)
LARGE_TRADE_MIN_SAMPLES = 100
LARGE_TRADE_BASELINE_LIMIT = 500
LARGE_TRADE_MULTIPLIER = Decimal(10)


@dataclass(frozen=True, slots=True)
class TradeObservation:
    event: ChainEvent
    base_amount: Decimal
    quote_amount: Decimal
    venue: str | None


@dataclass(frozen=True, slots=True)
class BookObservation:
    observed_at: datetime
    bid: Decimal
    ask: Decimal
    trade_watermark: datetime | None = None

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> Decimal:
        return (self.ask - self.bid) / self.ask * 100


def _covered(
    observations: list[BookObservation], start: datetime, end: datetime
) -> bool:
    if len(observations) < 3:
        return False
    times = [start, *(item.observed_at for item in observations), end]
    return all(
        timedelta(0) <= right - left <= MAX_GAP
        for left, right in zip(times, times[1:], strict=False)
    )


def _window_return(
    observations: list[BookObservation], start: datetime, end: datetime
) -> Decimal | None:
    selected = [item for item in observations if start <= item.observed_at <= end]
    if not _covered(selected, start, end):
        return None
    first = [item.mid for item in selected if item.observed_at <= start + MAX_GAP]
    last = [item.mid for item in selected if item.observed_at >= end - MAX_GAP]
    if len(first) < 3 or len(last) < 3:
        return None
    reference = median(first)
    return (median(last) / reference - 1) * 100


def market_anomaly_signals(
    event: ChainEvent,
    pair: str,
    observations: list[BookObservation],
    *,
    now: datetime,
    spread_floor_percent: Decimal,
    price_floor_percent: Decimal = Decimal("1"),
    multiplier: Decimal = Decimal("3"),
    windows: tuple[int, ...] = PRICE_WINDOWS,
) -> list[Signal]:
    """Evaluate the current book against strictly preceding, continuous observations."""
    at = event.occurred_at
    if not timedelta(0) <= now - at <= MAX_GAP:
        return []
    usable = sorted(
        (
            item
            for item in observations
            if item.observed_at <= at and item.bid > 0 and item.ask >= item.bid
        ),
        key=lambda item: item.observed_at,
    )
    if not usable or usable[-1].observed_at != at:
        return []
    current = usable[-1]
    result: list[Signal] = []
    spread_start = at - SPREAD_HISTORY
    prior = [item for item in usable if spread_start <= item.observed_at < at]
    if len(prior) >= MINIMUM_SPREAD_SAMPLES and _covered(prior, spread_start, at):
        baseline = median(item.spread for item in prior)
        threshold = max(spread_floor_percent, baseline * multiplier)
        if current.spread >= threshold and current.spread > baseline:
            result.append(
                _market_signal(
                    event, pair, "spread_anomaly", f"spread:{pair}",
                    current.spread, baseline, threshold,
                    len(prior), "one hour of continuous two-sided order-book snapshots",
                    "The best-bid/ask spread widened above its pair-specific median baseline.",
                    current.mid,
                )
            )
    for minutes in windows:
        duration = timedelta(minutes=minutes)
        change = _window_return(usable, at - duration, at)
        history = [
            _window_return(usable, at - duration * (index + 2), at - duration * (index + 1))
            for index in range(BASELINE_WINDOWS)
        ]
        if change is None or any(value is None for value in history):
            continue
        values = [abs(value) for value in history if value is not None]
        baseline = median(values)
        threshold = max(price_floor_percent, baseline * multiplier)
        if abs(change) >= threshold:
            result.append(
                _market_signal(
                    event, pair, "price_movement", f"price:{pair}:{minutes}",
                    change, baseline, threshold,
                    sum(item.observed_at >= at - duration * (BASELINE_WINDOWS + 1)
                        for item in usable),
                    f"{BASELINE_WINDOWS + 1} continuous {minutes}-minute windows",
                    "The sampled mid-price moved unusually relative to preceding windows.",
                    current.mid,
                    window_minutes=minutes,
                )
            )
    return result


def market_readiness(
    observations: list[BookObservation], at: datetime, windows: tuple[int, ...]
) -> dict[str, bool]:
    usable = sorted(
        (item for item in observations if item.observed_at <= at and item.bid > 0
         and item.ask >= item.bid),
        key=lambda item: item.observed_at,
    )
    if not usable or usable[-1].observed_at != at:
        return {"spread_anomaly": False, **{f"price_{minutes}m": False for minutes in windows}}
    start = at - SPREAD_HISTORY
    prior = [item for item in usable if start <= item.observed_at < at]
    ready = {
        "spread_anomaly": len(prior) >= MINIMUM_SPREAD_SAMPLES and _covered(prior, start, at)
    }
    for minutes in windows:
        duration = timedelta(minutes=minutes)
        ready[f"price_{minutes}m"] = all(
            _window_return(usable, at - duration * (index + 1), at - duration * index)
            is not None
            for index in range(BASELINE_WINDOWS + 1)
        )
    return ready


def trade_watermark(facts: dict[str, object]) -> datetime | None:
    value = facts.get("trade_scan_complete_through")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid persisted trade watermark")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Trade watermark must include a timezone")
    return result.astimezone(UTC)


def volume_coverage(
    observations: list[BookObservation], at: datetime, samples: int
) -> tuple[datetime, datetime] | None:
    markers = sorted(
        (item for item in observations if item.trade_watermark is not None
         and item.observed_at <= at),
        key=lambda item: item.observed_at,
    )
    if not markers or markers[-1].observed_at != at:
        return None
    watermark = markers[-1].trade_watermark
    if watermark is None or not timedelta(0) <= at - watermark <= MAX_GAP:
        return None
    end = watermark.replace(minute=(watermark.minute // 5) * 5, second=0, microsecond=0)
    start = end - timedelta(minutes=5 * (samples + 1))
    before = [item for item in markers if item.observed_at <= start]
    if not before:
        return None
    selected = [item for item in markers if item.observed_at >= before[-1].observed_at]
    if any(
        right.observed_at - left.observed_at > MAX_GAP
        or left.trade_watermark is None
        or right.trade_watermark is None
        or right.trade_watermark < left.trade_watermark
        for left, right in zip(selected, selected[1:], strict=False)
    ):
        return None
    return start, end


def volume_anomaly_signal(
    event: ChainEvent,
    pair: str,
    observations: list[BookObservation],
    volumes: dict[datetime, Decimal],
    *,
    now: datetime,
    samples: int,
    multiplier: Decimal,
) -> Signal | None:
    if not timedelta(0) <= now - event.occurred_at <= MAX_GAP:
        return None
    covered = volume_coverage(observations, event.occurred_at, samples)
    if covered is None:
        return None
    start, end = covered
    windows = [volumes.get(start + timedelta(minutes=5 * index), Decimal(0))
               for index in range(samples + 1)]
    baseline = sum(windows[:-1], Decimal(0)) / samples
    trigger = baseline * multiplier
    current = windows[-1]
    if baseline <= 0 or current < trigger:
        return None
    current_book = next(item for item in observations if item.observed_at == event.occurred_at)
    return _signal(
        event,
        category="volume_anomaly",
        dedupe_key=f"volume:{pair}",
        severity=Severity.HIGH if current >= trigger * 2 else Severity.WATCH,
        facts={
            "engine_version": "market-anomaly-v1",
            "pair": pair,
            "observed_base_volume": str(current),
            "baseline_mean_base_volume": str(baseline),
            "threshold_base_volume": str(trigger),
            "baseline_windows": samples,
            "coverage": "continuous completed trade scans; five-minute closed windows",
            "window_start": (end - timedelta(minutes=5)).isoformat(),
            "window_end": end.isoformat(),
            "source": "Horizon trades; order-book and liquidity-pool venues combined",
            "reference_price": str(current_book.mid),
            "reference_source": event.source_ref,
        },
        interpretation="Completed five-minute trade volume exceeded the historical mean threshold.",
        confidence=Confidence.MEDIUM,
    )


def large_trade_signal(
    trade: TradeObservation,
    previous_trades: list[TradeObservation],
    pair: str,
    *,
    now: datetime,
    minimum_xlm: Decimal,
    coverage_start: datetime,
) -> Signal | None:
    at = trade.event.occurred_at
    if (
        trade.venue not in ("orderbook", "liquidity_pool")
        or not timedelta(0) <= now - at <= MAX_GAP
        or coverage_start > at - LARGE_TRADE_HISTORY
        or trade.base_amount < minimum_xlm
        or trade.quote_amount <= 0
    ):
        return None
    prior = sorted(
        (
            candidate for candidate in previous_trades
            if candidate.venue == trade.venue
            and at - LARGE_TRADE_HISTORY <= candidate.event.occurred_at < at
            and candidate.quote_amount > 0
        ),
        key=lambda candidate: candidate.event.occurred_at,
    )[-LARGE_TRADE_BASELINE_LIMIT:]
    if len(prior) < LARGE_TRADE_MIN_SAMPLES:
        return None
    baseline = median(item.quote_amount for item in prior)
    if baseline <= 0:
        return None
    price = trade.quote_amount / trade.base_amount
    absolute_floor = minimum_xlm * price
    threshold = max(baseline * LARGE_TRADE_MULTIPLIER, absolute_floor)
    if trade.quote_amount < threshold:
        return None
    return _signal(
        trade.event,
        category="large_trade",
        dedupe_key=f"large-trade:{trade.event.event_id}",
        severity=Severity.HIGH if trade.quote_amount >= threshold * 2 else Severity.WATCH,
        facts={
            "engine_version": "market-anomaly-v1",
            "pair": pair,
            "observed_quote_notional": str(trade.quote_amount),
            "base_amount_xlm": str(trade.base_amount),
            "quote_per_xlm": str(price),
            "baseline_median_quote_notional": str(baseline),
            "threshold_quote_notional": str(threshold),
            "minimum_xlm": str(minimum_xlm),
            "baseline_multiplier": str(LARGE_TRADE_MULTIPLIER),
            "baseline_trade_count": len(prior),
            "coverage": "continuous watermarked trade scans for the preceding hour",
            "trade_type": trade.venue,
            "source": "Horizon trade; source event identifies the original trade",
            "reference_price": str(price),
            "reference_source": trade.event.source_ref,
        },
        interpretation=(
            "The trade exceeded both the absolute XLM floor and the same-market, "
            "same-venue historical trade-size threshold; intent is unknown."
        ),
        confidence=Confidence.MEDIUM,
    )


def _market_signal(
    event: ChainEvent,
    pair: str,
    category: str,
    deduplication_key: str,
    observed: Decimal,
    baseline: Decimal,
    threshold: Decimal,
    samples: int,
    coverage: str,
    interpretation: str,
    reference_price: Decimal,
    *,
    window_minutes: int | None = None,
) -> Signal:
    return _signal(
        event,
        category=category,
        dedupe_key=deduplication_key,
        severity=Severity.HIGH if abs(observed) >= threshold * 2 else Severity.WATCH,
        facts={
            "engine_version": "market-anomaly-v1",
            "pair": pair,
            "observed_percent": str(observed),
            "baseline_percent": str(baseline),
            "threshold_percent": str(threshold),
            "coverage": coverage,
            "sample_count": samples,
            "window_minutes": window_minutes,
            "source": "Stellar Horizon order-book snapshot",
            "reference_price": str(reference_price),
            "reference_source": event.source_ref,
        },
        interpretation=interpretation,
        confidence=Confidence.MEDIUM,
    )
