"""Preliminary price-momentum ideas and price-only follow-up."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from stellar_intelligence_bot.core.markets import MarketPair
from stellar_intelligence_bot.storage.database import Database
from stellar_intelligence_bot.storage.models import (
    AssistantHeartbeatRecord,
    AssistantSettingsRecord,
    MarketSnapshotRecord,
    OpportunityEvaluationRecord,
    OpportunityRecord,
    SignalRecord,
)

HORIZONS = (5, 10, 60, 240, 1440, 10080)
STEP = Decimal("0.0000001")
MAX_AMOUNT = Decimal("922337203685.4775807")


class AssistantSettings(BaseModel):
    balances: dict[str, Decimal]
    reserve: Decimal = Field(ge=0, le=MAX_AMOUNT, decimal_places=7)
    fee: Decimal = Field(ge=0, le=MAX_AMOUNT, decimal_places=7)
    allocation: Decimal = Field(gt=0, le=100, decimal_places=4)
    min_movement_percent: Decimal = Field(gt=0, le=100, decimal_places=4)
    minimum_severity: Literal["WATCH", "HIGH"] = "WATCH"

    @model_validator(mode="after")
    def validate_balances(self) -> "AssistantSettings":
        from stellar_intelligence_bot.core.markets import StellarAsset

        for key, amount in self.balances.items():
            if StellarAsset.parse(key).key != key:
                raise ValueError("Asset identities must be canonical")
            if not amount.is_finite() or amount < 0 or amount > MAX_AMOUNT:
                raise ValueError("Invalid asset balance")
            exponent = amount.as_tuple().exponent
            if isinstance(exponent, int) and exponent < -7:
                raise ValueError("Asset balances permit at most seven decimal places")
        if "native" not in self.balances:
            raise ValueError("An XLM balance is required")
        if self.balances["native"] < self.reserve + 2 * self.fee:
            raise ValueError("XLM balance must cover the reserve and two assumed fees")
        return self


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _d(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def build_idea(
    signal: SignalRecord,
    snapshot: MarketSnapshotRecord,
    pair: MarketPair,
    config: AssistantSettings,
) -> tuple[str, dict[str, object]] | None:
    """Construct only from signal-time book; zero direction and unknown input reject."""
    move = _d(signal.observed_facts.get("observed_percent"))
    bid, ask = snapshot.best_bid, snapshot.best_ask
    if (
        signal.category != "price_movement"
        or signal.severity
        not in (
            {"HIGH", "CRITICAL"}
            if config.minimum_severity == "HIGH"
            else {"WATCH", "HIGH", "CRITICAL"}
        )
        or move is None
        or abs(move) < config.min_movement_percent
        or bid is None
        or ask is None
        or bid <= 0
        or ask < bid
        or pair.base.key != "native"
    ):
        return None
    levels = snapshot.observed_facts.get("order_book_levels")
    if (
        not isinstance(levels, dict)
        or levels.get("version") != 1
        or levels.get("price_units") != "quote_per_base"
        or levels.get("bid_amount_units") != "quote"
        or levels.get("ask_amount_units") != "base"
    ):
        return None
    side = "BUY" if move > 0 else "SELL"
    price = ask if side == "BUY" else bid
    entries = levels.get("asks" if side == "BUY" else "bids")
    if not isinstance(entries, list):
        return None
    liquidity = Decimal(0)
    for level in entries:
        if not isinstance(level, dict):
            return None
        level_price, amount = _d(level.get("price")), _d(level.get("amount"))
        if (
            level_price is not None
            and abs(level_price - price) <= Decimal("0.000000000000000001")
            and amount is not None
            and amount > 0
        ):
            liquidity += amount if side == "BUY" else amount / price
    xlm = config.balances.get("native", Decimal(0))
    available = (
        config.balances.get(pair.quote.key, Decimal(0))
        if side == "BUY"
        else xlm - config.reserve - 2 * config.fee
    )
    budget = available * config.allocation / 100
    quantity = (budget / price if side == "BUY" else budget).quantize(STEP, rounding=ROUND_DOWN)
    if quantity <= 0 or quantity > liquidity:
        return None
    quote = quantity * price
    fees_quote = 2 * config.fee * bid
    target = (
        (quote * Decimal("1.01") + fees_quote) / quantity
        if side == "BUY"
        else (quote * Decimal("0.99") - fees_quote) / quantity
    )
    target = target.quantize(STEP, rounding=ROUND_CEILING if side == "BUY" else ROUND_DOWN)
    if target <= 0:
        return None
    return side, {
        "rule_version": "momentum-v1",
        "reason": (
            f"Observed mid-price moved {move}% over "
            f"{signal.observed_facts.get('window_minutes')} minutes; "
            "preliminary momentum rule follows that direction."
        ),
        "source_signal_id": signal.signal_id,
        "source_event_id": signal.event_id,
        "snapshot_id": snapshot.id,
        "snapshot_at": _utc(snapshot.observed_at).isoformat(),
        "bid": str(bid),
        "ask": str(ask),
        "entry_limit": str(price),
        "quantity": str(quantity),
        "quote_amount": str(quote),
        "target_exit": str(target),
        "fee_xlm_per_transaction": str(config.fee),
        "estimated_fees_quote": str(fees_quote),
        "estimated_profit_quote": str(
            quantity * target - quote - fees_quote
            if side == "BUY"
            else quote - quantity * target - fees_quote
        ),
        "estimated_net_percent": str(
            (
                quantity * target - quote - fees_quote
                if side == "BUY"
                else quote - quantity * target - fees_quote
            )
            / quote
            * 100
        ),
        "allocation_percent_of_available_asset": str(config.allocation),
        "reserve_xlm": str(config.reserve),
        "base_balance": str(xlm),
        "quote_balance": str(config.balances.get(pair.quote.key, 0)),
        "limitations": [
            "Preliminary momentum rule is unvalidated and may fail.",
            "Transaction fees are user assumptions converted at the observed bid.",
            "Spread is reflected by using ask for a buy and bid for a sell; "
            "exit target uses the opposite side.",
            "Slippage, future spread, actual network fees and execution are unknown; "
            "the 1% target is estimated, not verified net profit.",
            "Displayed best-level liquidity is a snapshot, not evidence of a fill.",
        ],
    }


async def process_ideas(db: Database, pairs: tuple[MarketPair, ...], now: datetime) -> None:
    """Emit new ideas only after settings and source signal exist; evaluate sampled prices."""
    async with db.sessions.begin() as session:
        settings_row = await session.get(AssistantSettingsRecord, 1)
        if settings_row is not None:
            settings = AssistantSettings.model_validate(settings_row.settings)
            candidates = (
                await session.scalars(
                    select(SignalRecord)
                    .where(
                        SignalRecord.category == "price_movement",
                        SignalRecord.occurred_at
                        >= max(_utc(settings_row.updated_at), now - timedelta(minutes=2)),
                        SignalRecord.occurred_at <= now,
                        SignalRecord.expires_at > now,
                    )
                    .order_by(SignalRecord.occurred_at)
                )
            ).all()
            for signal in candidates:
                if await session.get(OpportunityRecord, signal.signal_id) is not None:
                    continue
                pair = next((p for p in pairs if p.key == signal.observed_facts.get("pair")), None)
                if pair is None:
                    continue
                snapshot = await session.scalar(
                    select(MarketSnapshotRecord)
                    .where(
                        MarketSnapshotRecord.pair_key == pair.key,
                        MarketSnapshotRecord.observed_at == signal.occurred_at,
                    )
                    .order_by(MarketSnapshotRecord.id.desc())
                    .limit(1)
                )
                if snapshot is None:
                    continue
                idea = build_idea(signal, snapshot, pair, settings)
                if idea is None:
                    continue
                side, parameters = idea
                session.add(
                    OpportunityRecord(
                        signal_id=signal.signal_id,
                        created_at=now,
                        expires_at=_utc(signal.expires_at),
                        pair_key=pair.key,
                        side=side,
                        parameters=parameters,
                    )
                )
                await session.flush()
                for horizon in HORIZONS:
                    session.add(
                        OpportunityEvaluationRecord(
                            signal_id=signal.signal_id,
                            horizon_minutes=horizon,
                            target_at=now + timedelta(minutes=horizon),
                            status="pending",
                        )
                    )
        due = (
            await session.scalars(
                select(OpportunityEvaluationRecord)
                .where(
                    OpportunityEvaluationRecord.status == "pending",
                    OpportunityEvaluationRecord.target_at <= now,
                )
                .order_by(OpportunityEvaluationRecord.target_at)
                .limit(500)
            )
        ).all()
        for evaluation in due:
            opportunity = await session.get(OpportunityRecord, evaluation.signal_id)
            if opportunity is None:
                raise ValueError("Opportunity evaluation has no source opportunity")
            snapshots = (
                await session.scalars(
                    select(MarketSnapshotRecord)
                    .where(
                        MarketSnapshotRecord.pair_key == opportunity.pair_key,
                        MarketSnapshotRecord.observed_at >= opportunity.created_at,
                        MarketSnapshotRecord.observed_at <= evaluation.target_at,
                        MarketSnapshotRecord.best_bid > 0,
                        MarketSnapshotRecord.best_ask >= MarketSnapshotRecord.best_bid,
                    )
                    .order_by(MarketSnapshotRecord.observed_at)
                )
            ).all()
            entry = Decimal(str(opportunity.parameters["entry_limit"]))
            target = Decimal(str(opportunity.parameters["target_exit"]))
            entry_seen = False
            target_seen = False
            entry_time = None
            last_mid = None
            for snapshot in snapshots:
                bid, ask = snapshot.best_bid, snapshot.best_ask
                if bid is None or ask is None:
                    continue
                last_mid = (bid + ask) / 2
                if not entry_seen and (ask <= entry if opportunity.side == "BUY" else bid >= entry):
                    entry_seen = True
                    entry_time = snapshot.observed_at
                elif entry_seen and (bid >= target if opportunity.side == "BUY" else ask <= target):
                    target_seen = True
            original_mid = (
                Decimal(str(opportunity.parameters["bid"]))
                + Decimal(str(opportunity.parameters["ask"]))
            ) / 2
            last_at = _utc(snapshots[-1].observed_at) if snapshots else None
            times = [_utc(opportunity.created_at)] + [
                _utc(snapshot.observed_at) for snapshot in snapshots
            ] + [_utc(evaluation.target_at)]
            largest_gap = max(
                (later - earlier).total_seconds()
                for earlier, later in zip(times, times[1:], strict=False)
            )
            near_horizon = last_at is not None and (
                _utc(evaluation.target_at) - last_at <= timedelta(minutes=2)
            )
            evaluation.status = "observed" if near_horizon else "unknown"
            evaluation.result = {
                "entry_price_observed": entry_seen,
                "target_price_observed_after_entry": target_seen,
                "entry_observed_at": _utc(entry_time).isoformat() if entry_time else None,
                "price_change_percent": str((last_mid / original_mid - 1) * 100)
                if last_mid and near_horizon
                else None,
                "last_observed_at": last_at.isoformat() if last_at else None,
                "observation_count": len(snapshots),
                "largest_gap_seconds": largest_gap,
                "execution": "unknown",
                "note": "Sampled bid/ask conditions are not proof of execution; "
                "absence may reflect gaps.",
            }
        heartbeat = await session.get(AssistantHeartbeatRecord, 1)
        if heartbeat is None:
            session.add(AssistantHeartbeatRecord(id=1, last_success_at=now))
        else:
            heartbeat.last_success_at = now
