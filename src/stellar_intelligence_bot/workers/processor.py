"""Persist collected Stellar observations and evaluate signal rules."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from stellar_intelligence_bot.chains.base import CollectedEvent
from stellar_intelligence_bot.core.events import Chain, ChainEvent
from stellar_intelligence_bot.core.market_anomalies import (
    BookObservation,
    TradeObservation,
    large_trade_signal,
    market_anomaly_signals,
    trade_watermark,
    volume_anomaly_signal,
    volume_coverage,
)
from stellar_intelligence_bot.core.signal_engine import (
    SIGNAL_COOLDOWN,
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
        large_trade_min_xlm: Decimal = Decimal("10000"),
        price_anomaly_floor_percent: Decimal = Decimal("1"),
        price_windows_minutes: tuple[int, ...] = (5, 15, 60, 240),
    ) -> None:
        self.database = database
        self.whale_thresholds = whale_thresholds
        self.baseline_samples = baseline_samples
        self.anomaly_multiplier = anomaly_multiplier
        self.large_order_threshold = large_order_threshold
        self.large_trade_min_xlm = large_trade_min_xlm
        self.spread_threshold_percent = spread_threshold_percent
        self.price_anomaly_floor_percent = price_anomaly_floor_percent
        self.price_windows_minutes = price_windows_minutes

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

        rows = await self.database.book_observations(
            pair,
            event.occurred_at - timedelta(
                minutes=max(
                    60, max(self.price_windows_minutes) * 4, (self.baseline_samples + 2) * 5
                )
            ),
            event.occurred_at,
        )
        observations = [
            BookObservation(
                row.observed_at.replace(tzinfo=UTC)
                if row.observed_at.tzinfo is None
                else row.observed_at.astimezone(UTC),
                row.best_bid,
                row.best_ask,
                trade_watermark(row.observed_facts),
            )
            for row in rows
            if row.best_bid is not None and row.best_ask is not None
        ]
        for signal in market_anomaly_signals(
            event,
            pair.key,
            observations,
            now=datetime.now(UTC),
            spread_floor_percent=self.spread_threshold_percent,
            price_floor_percent=self.price_anomaly_floor_percent,
            windows=self.price_windows_minutes,
        ):
            await self.database.save_signal(signal, SIGNAL_COOLDOWN)

        covered = volume_coverage(observations, event.occurred_at, self.baseline_samples)
        if covered is not None:
            volumes = await self.database.volume_windows(pair, *covered)
            volume_candidate = volume_anomaly_signal(
                event, pair.key, observations, volumes, now=datetime.now(UTC),
                samples=self.baseline_samples, multiplier=self.anomaly_multiplier,
            )
            if volume_candidate is not None:
                await self.database.save_signal(volume_candidate, SIGNAL_COOLDOWN)

        trade_coverage = volume_coverage(observations, event.occurred_at, 12)
        markers = [
            book for book in observations
            if book.trade_watermark is not None and book.observed_at < event.occurred_at
        ]
        latest_watermark = trade_watermark(dict(attrs))
        if (
            pair.base.code is None
            and trade_coverage is not None
            and markers
            and latest_watermark is not None
        ):
            previous_watermark = markers[-1].trade_watermark
            if previous_watermark is not None and previous_watermark < latest_watermark:
                trade_rows = await self.database.trades_with_events(
                    pair, trade_coverage[0], latest_watermark
                )
                trades = [
                    TradeObservation(
                        ChainEvent(
                            event_id=trade.trade_id,
                            chain=Chain.STELLAR,
                            event_type="trade",
                            occurred_at=(
                                trade.occurred_at.replace(tzinfo=UTC)
                                if trade.occurred_at.tzinfo is None
                                else trade.occurred_at.astimezone(UTC)
                            ),
                            source_ref=source.source_ref,
                        ),
                        trade.base_amount,
                        trade.quote_amount,
                        str(source.attributes.get("trade_type"))
                        if isinstance(source.attributes.get("trade_type"), str)
                        else None,
                    )
                    for trade, source in trade_rows
                ]
                for trade in trades:
                    if previous_watermark < trade.event.occurred_at <= latest_watermark:
                        candidate = large_trade_signal(
                            trade, trades, pair.key, now=datetime.now(UTC),
                            minimum_xlm=self.large_trade_min_xlm, coverage_start=trade_coverage[0],
                        )
                        if candidate is not None:
                            await self.database.save_signal(candidate, SIGNAL_COOLDOWN)


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise ValueError(f"Invalid market snapshot measurement: {value!r}") from exc
