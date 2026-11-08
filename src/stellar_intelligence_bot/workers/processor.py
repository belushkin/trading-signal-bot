"""Persist collected Stellar observations and evaluate signal rules."""

from datetime import UTC, datetime
from decimal import Decimal

from stellar_intelligence_bot.chains.base import CollectedEvent
from stellar_intelligence_bot.core.signal_engine import (
    SIGNAL_COOLDOWN,
    order_book_signals,
    volume_signal,
    whale_signal,
)
from stellar_intelligence_bot.storage.database import Database


class EventProcessor:
    def __init__(
        self,
        database: Database,
        *,
        whale_thresholds: dict[str, Decimal],
        baseline_samples: int,
        anomaly_multiplier: Decimal,
        large_order_threshold: Decimal,
        spread_threshold_percent: Decimal,
    ) -> None:
        self.database = database
        self.whale_thresholds = whale_thresholds
        self.baseline_samples = baseline_samples
        self.anomaly_multiplier = anomaly_multiplier
        self.large_order_threshold = large_order_threshold
        self.spread_threshold_percent = spread_threshold_percent

    async def process(self, item: CollectedEvent) -> None:
        event = item.event
        if event is None:
            if item.stream_key is not None and item.cursor is not None:
                await self.database.advance_cursor(item.stream_key, item.cursor)
            return

        if item.is_market_snapshot:
            if item.pair is None:
                raise ValueError("A market snapshot must include its trading pair")
            await self._process_snapshot(item)
            return

        if item.stream_key is None or item.cursor is None:
            raise ValueError("A persisted chain event must include its source cursor")

        if event.event_type == "trade":
            if item.pair is None or item.base_amount is None or item.quote_amount is None:
                raise ValueError("Trade events must include pair and amounts")
            await self.database.save_trade_event(
                event,
                item.stream_key,
                item.cursor,
                item.pair,
                item.base_amount,
                item.quote_amount,
            )
            return

        inserted = await self.database.save_event_with_cursor(
            event, item.stream_key, item.cursor
        )
        if inserted and event.event_type == "payment":
            asset = event.attributes.get("asset")
            if isinstance(asset, str):
                signal = whale_signal(event, self.whale_thresholds.get(asset))
                if signal is not None and signal.expires_at > datetime.now(UTC):
                    await self.database.save_signal(signal, SIGNAL_COOLDOWN)

    async def _process_snapshot(self, item: CollectedEvent) -> None:
        event = item.event
        pair = item.pair
        if event is None or pair is None:
            raise ValueError("A market snapshot must include its event and pair")
        attrs = event.attributes
        best_bid = _optional_decimal(attrs.get("best_bid"))
        best_ask = _optional_decimal(attrs.get("best_ask"))
        bid_depth = _optional_decimal(attrs.get("bid_depth_base")) or Decimal(0)
        ask_depth = _optional_decimal(attrs.get("ask_depth_base")) or Decimal(0)
        inserted = await self.database.save_market_snapshot(
            event,
            pair,
            event.occurred_at,
            best_bid,
            best_ask,
            bid_depth,
            ask_depth,
            dict(attrs),
        )
        if not inserted:
            return

        current_window = event.occurred_at.replace(
            minute=(event.occurred_at.minute // 5) * 5, second=0, microsecond=0
        )
        baseline = await self.database.volume_baseline(
            pair, self.baseline_samples, current_window
        )
        if baseline is not None:
            baseline_volume, sample_count = baseline
            current_volume = await self.database.current_window_volume(pair, current_window)
            signal = volume_signal(
                event,
                pair.key,
                current_volume,
                baseline_volume,
                sample_count,
                self.anomaly_multiplier,
            )
            if signal is not None:
                await self.database.save_signal(signal, SIGNAL_COOLDOWN)

        for signal in order_book_signals(
            event,
            pair.key,
            large_order_threshold=self.large_order_threshold,
            spread_threshold_percent=self.spread_threshold_percent,
        ):
            await self.database.save_signal(signal, SIGNAL_COOLDOWN)


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise ValueError(f"Invalid market snapshot measurement: {value!r}") from exc
