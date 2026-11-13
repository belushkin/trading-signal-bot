from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from stellar_intelligence_bot.app.actionable import process_ideas
from stellar_intelligence_bot.app.config import Settings
from stellar_intelligence_bot.app.dashboard_api import create_app
from stellar_intelligence_bot.core.markets import MarketPair
from stellar_intelligence_bot.storage.database import Database
from stellar_intelligence_bot.storage.models import (
    AssistantSettingsRecord,
    EventRecord,
    MarketSnapshotRecord,
    RuntimeHealthRecord,
    SignalEvaluationRecord,
    SignalRecord,
)

ISSUER = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"
PAIR = f"native/USDC:{ISSUER}"
TX_HASH = "a" * 64


@pytest.mark.asyncio
async def test_assistant_settings_persist_and_screening_health_is_explicit(tmp_path) -> None:
    config = Settings(monitored_pairs=(MarketPair.parse(PAIR),))
    payload = {
        "balances": {"native": "100", f"USDC:{ISSUER}": "50"},
        "reserve": "10", "allocation": "5",
    }
    async with _api(str(tmp_path / "assistant.db"), config) as (client, db):
        response = await client.get("/api/assistant/opportunities")
        assert response.json()["screening"] == "unavailable"
        assert response.json()["configured"] is False
        assert (await client.put("/api/assistant/settings", json=payload)).status_code == 200
        assert (await client.get("/api/assistant/settings")).json()["settings"]["balances"] == (
            payload["balances"]
        )
        valuation = (await client.post("/api/assistant/portfolio-value", json=payload)).json()
        assert valuation["value_xlm"] is None
        assert valuation["missing_assets"] == [f"USDC:{ISSUER}"]
        async with db.sessions.begin() as session:
            row = await session.get(AssistantSettingsRecord, 1)
            row.settings = row.settings | {
                "fee": "0.001", "min_movement_percent": "2", "minimum_severity": "HIGH",
            }
            session.add(MarketSnapshotRecord(
                pair_key=PAIR, observed_at=datetime.now(UTC), best_bid=Decimal("1"),
                best_ask=Decimal("1.25"), bid_depth=Decimal(100), ask_depth=Decimal(100),
                observed_facts={},
            ))
        valuation = (await client.post("/api/assistant/portfolio-value", json=payload)).json()
        assert Decimal(valuation["value_xlm"]) == Decimal("130")
        assert (await client.put("/api/assistant/settings", json=payload)).status_code == 200
        async with db.sessions() as session:
            row = await session.get(AssistantSettingsRecord, 1)
            assert row.settings["fee"] == "0.001"
            assert row.settings["min_movement_percent"] == "2"
            assert row.settings["minimum_severity"] == "HIGH"
        assert "fee" not in (await client.get("/api/assistant/settings")).json()["settings"]
        now = datetime.now(UTC)
        await db.record_health(started_at=now, successful=True, now=now)
        await process_ideas(db, config.monitored_pairs, now)
        response = (await client.get("/api/assistant/opportunities")).json()
        assert response["screening"] == "healthy"
        assert response["configured"] is True
        assert response["items"] == []
        assert (await client.get("/api/assistant/target-results")).json() == {"items": []}
        bad = payload | {"balances": {"native": "0"}}
        assert (await client.put("/api/assistant/settings", json=bad)).status_code == 422


@asynccontextmanager
async def _api(
    database_path: str, settings: Settings
) -> AsyncIterator[tuple[AsyncClient, Database]]:
    database = Database(f"sqlite+aiosqlite:///{database_path}")
    await database.initialize()
    app = create_app(database=database, settings=settings)
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://dashboard.test"
            ) as client:
                yield client, database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_empty_database_and_unconfigured_markets_are_explicit(tmp_path) -> None:
    async with _api(str(tmp_path / "empty.db"), Settings()) as (client, _):
        health = await client.get("/api/health")
        overview = await client.get("/api/overview")
        markets = await client.get("/api/markets")
        signals = await client.get("/api/signals")
        activity = await client.get("/api/activity")
        analytics = await client.get("/api/analytics?days=7")

    assert health.status_code == 200
    assert health.json()["database"]["status"] == "healthy"
    assert health.json()["collector"]["status"] == "not_started"
    assert overview.json()["counts"] == {"events": 0, "signals": 0, "notable_events_24h": 0}
    assert "no_markets_configured" in {item["code"] for item in overview.json()["warnings"]}
    assert markets.json()["items"] == []
    assert "No markets are configured" in markets.json()["message"]
    assert signals.json()["items"] == []
    assert activity.json()["items"] == []
    assert analytics.json()["data_coverage"]["events"] == 0
    assert analytics.json()["data_coverage"]["missing_observation_rate_percent"] is None
    assert len(analytics.json()["signal_frequency"]) == 7
    assert all(item["count"] == 0 for item in analytics.json()["signal_frequency"])


@pytest.mark.asyncio
async def test_analytics_excludes_legacy_signals_and_reports_pending_outcomes(tmp_path) -> None:
    async with _api(str(tmp_path / "engine.db"), Settings()) as (client, database):
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            for name in ("legacy", "current"):
                session.add(
                    EventRecord(
                        event_id=name, chain="stellar", event_type="market_snapshot",
                        occurred_at=now, source_ref="https://horizon.stellar.org/order_book",
                        attributes={},
                    )
                )
                session.add(
                    SignalRecord(
                        signal_id=name, deduplication_key=name, event_id=name, occurred_at=now,
                        category="spread_anomaly", severity="WATCH", confidence="HIGH",
                        observed_facts={"pair": PAIR} | (
                            {"engine_version": "market-anomaly-v1"} if name == "current" else {}
                        ),
                        interpretation="Observed spread", expires_at=now + timedelta(minutes=30),
                    )
                )
                session.add(
                    SignalEvaluationRecord(
                        signal_id=name, pair_key=PAIR, horizon_minutes=5,
                        target_at=now + timedelta(minutes=5), reference_at=now,
                        reference_price=Decimal(1), status="pending", data_quality="pending",
                        observation_count=0,
                    )
                )
        response = await client.get("/api/analytics?days=1")
    assert response.status_code == 200
    data = response.json()
    assert data["excluded_legacy_signals"] == 1
    assert data["signals_by_category"] == [{"category": "spread_anomaly", "count": 1}]
    assert data["data_coverage"]["pending_evaluations"] == 1
    assert data["outcomes_by_pair"][0]["signals"] == 1
    assert data["outcomes_by_pair"][0]["mean_observed_change_percent"] is None


@pytest.mark.asyncio
async def test_started_collector_without_completed_cycle_is_starting(tmp_path) -> None:
    async with _api(str(tmp_path / "starting.db"), Settings()) as (client, database):
        await database.start_health(datetime.now(UTC))

        health = (await client.get("/api/health")).json()
        overview = (await client.get("/api/overview")).json()

    assert health["collector"]["status"] == "starting"
    assert overview["collector"]["status"] == "starting"
    assert "collector_starting" in {item["code"] for item in overview["warnings"]}


@pytest.mark.asyncio
async def test_watchlist_exposes_tiers_disabled_pairs_and_missing_data(tmp_path) -> None:
    settings = Settings.from_env()
    async with _api(str(tmp_path / "watchlist.db"), settings) as (client, _):
        markets = (await client.get("/api/markets")).json()["items"]
        details = await client.get(f"/api/markets/{settings.watchlist[4].pair.key}")

    assert len(markets) == 9
    assert [market["tier"] for market in markets] == [1, 1, 1, 1, 1, 1, 1, 2, 2]
    assert [market["enabled"] for market in markets] == [
        True, True, True, True, True, True, True, False, False
    ]
    assert all(market["status"] == "no_data" for market in markets)
    assert all(market["trade_count_24h"] is None for market in markets)
    assert details.status_code == 200
    assert details.json()["summary"]["enabled"] is False
    assert details.json()["recent_trades"] == []


@pytest.mark.asyncio
async def test_missing_market_observations_and_stale_persisted_data(tmp_path) -> None:
    pair = MarketPair.parse(PAIR)
    settings = Settings(monitored_pairs=(pair,))
    async with _api(str(tmp_path / "stale.db"), settings) as (client, database):
        missing = (await client.get("/api/markets")).json()["items"][0]
        now = datetime.now(UTC)
        old_time = now - timedelta(minutes=10)
        async with database.sessions.begin() as session:
            session.add(
                MarketSnapshotRecord(
                    pair_key=PAIR,
                    observed_at=old_time,
                    best_bid=Decimal("9"),
                    best_ask=Decimal("11"),
                    bid_depth=Decimal("100"),
                    ask_depth=Decimal("120"),
                    spread_percent=Decimal("18.18"),
                    observed_facts={},
                )
            )
            session.add(
                RuntimeHealthRecord(
                    health_key="collector",
                    started_at=old_time,
                    last_success_at=old_time,
                    last_error_at=None,
                    last_error=None,
                )
            )
        stale = (await client.get("/api/markets")).json()["items"][0]
        detail = await client.get(f"/api/markets/{PAIR}")
        overview = (await client.get("/api/overview")).json()

    assert missing["status"] == "no_data"
    assert missing["latest_price"] is None
    assert missing["volume_24h_base"] is None
    assert missing["trade_ingestion_status"] == "unverified"
    assert missing["last_trade_scan_through"] is None
    assert stale["status"] == "stale"
    assert stale["trade_ingestion_status"] == "unverified"
    assert Decimal(stale["latest_price"]) == Decimal("10")
    assert detail.status_code == 200
    assert Decimal(detail.json()["history"][0]["price"]) == Decimal("10")
    assert detail.json()["latest_order_book_levels"] is None
    assert overview["collector"]["status"] == "stale"
    assert "collector_stale" in {item["code"] for item in overview["warnings"]}


@pytest.mark.asyncio
async def test_market_detail_returns_latest_levels_without_stale_fallback(tmp_path) -> None:
    settings = Settings(monitored_pairs=(MarketPair.parse(PAIR),))
    levels = {
        "version": 1, "bid_amount_units": "quote", "ask_amount_units": "base",
        "bids": [{"price": "0.5", "amount": "10"}], "asks": [],
    }
    async with _api(str(tmp_path / "levels.db"), settings) as (client, database):
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            session.add(MarketSnapshotRecord(
                pair_key=PAIR, observed_at=now - timedelta(minutes=2),
                best_bid=Decimal("0.5"), best_ask=Decimal("0.6"),
                bid_depth=Decimal(20), ask_depth=Decimal(0),
                observed_facts={"order_book_levels": levels},
            ))
        detail = (await client.get(f"/api/markets/{PAIR}")).json()
        assert detail["latest_order_book_levels"] == {
            "observed_at": (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
            "levels": levels,
        }
        async with database.sessions.begin() as session:
            session.add(MarketSnapshotRecord(
                pair_key=PAIR, observed_at=now, best_bid=None, best_ask=None,
                bid_depth=Decimal(0), ask_depth=Decimal(0), observed_facts={},
            ))
        detail = (await client.get(f"/api/markets/{PAIR}")).json()
        assert detail["latest_order_book_levels"] is None


@pytest.mark.asyncio
async def test_signal_filters_pagination_details_and_activity_links(tmp_path) -> None:
    settings = Settings(monitored_pairs=(MarketPair.parse(PAIR),))
    async with _api(str(tmp_path / "signals.db"), settings) as (client, database):
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            for index in range(3):
                event_id = f"payment:{index}"
                signal_id = f"signal:{index}"
                session.add(
                    EventRecord(
                        event_id=event_id,
                        chain="stellar",
                        event_type="payment",
                        occurred_at=now - timedelta(minutes=index),
                        source_ref=f"https://stellar.expert/explorer/public/tx/{TX_HASH}",
                        attributes={"amount": "500", "asset": "native"},
                    )
                )
                session.add(
                    SignalRecord(
                        signal_id=signal_id,
                        deduplication_key=signal_id,
                        event_id=event_id,
                        occurred_at=now - timedelta(minutes=index),
                        category="whale_transfer" if index == 2 else "wide_spread",
                        severity="HIGH" if index == 2 else "WATCH",
                        confidence="HIGH",
                        observed_facts={"pair": PAIR, "asset": "native", "amount": str(index)},
                        interpretation=f"Observed facts for test {index}",
                        expires_at=now + timedelta(minutes=30),
                    )
                )
                session.add(
                    SignalEvaluationRecord(
                        signal_id=signal_id,
                        pair_key=PAIR,
                        horizon_minutes=5,
                        target_at=now + timedelta(minutes=5),
                        reference_at=now - timedelta(minutes=index),
                        reference_price=Decimal("10"),
                        measured_price=None,
                        measured_at=None,
                        return_percent=None,
                        max_increase_percent=None,
                        max_decrease_percent=None,
                        observation_count=0,
                        largest_gap_seconds=None,
                        status="missing_observation",
                        data_quality="missing_observation",
                        evaluated_at=now,
                    )
                )

        page = await client.get(
            "/api/signals",
            params={"pair": PAIR, "category": "wide_spread", "severity": "WATCH", "limit": 1},
        )
        next_page = await client.get(
            "/api/signals",
            params={
                "pair": PAIR,
                "category": "wide_spread",
                "severity": "WATCH",
                "limit": 1,
                "offset": 1,
            },
        )
        by_asset = await client.get("/api/signals", params={"pair": "native", "limit": 10})
        detail = await client.get("/api/signals/signal:0")
        activity = await client.get("/api/activity", params={"event_type": "payment", "limit": 1})

    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert len(page.json()["items"]) == 1
    assert page.json()["items"][0]["signal_id"] != next_page.json()["items"][0]["signal_id"]
    assert by_asset.json()["total"] == 3
    assert detail.json()["observed_facts"]["amount"] == "0"
    assert detail.json()["evaluations"][0]["status"] == "missing_observation"
    assert (
        detail.json()["source"]["explorer_url"]
        == f"https://stellar.expert/explorer/public/tx/{TX_HASH}"
    )
    assert activity.json()["items"][0]["event_type"] == "payment"
    assert activity.json()["items"][0]["explorer_url"].endswith(TX_HASH)


@pytest.mark.asyncio
async def test_validation_and_not_found_errors_use_consistent_envelopes(tmp_path) -> None:
    async with _api(str(tmp_path / "errors.db"), Settings()) as (client, _):
        invalid = await client.get("/api/signals?limit=101")
        missing = await client.get("/api/signals/does-not-exist")

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_signal_details_do_not_expose_internal_source_urls_or_query_credentials(
    tmp_path,
) -> None:
    settings = Settings(monitored_pairs=(MarketPair.parse(PAIR),))
    async with _api(str(tmp_path / "source-url.db"), settings) as (client, database):
        now = datetime.now(UTC)
        internal_url = "https://horizon.invalid/order_book?api_key=fixture"
        async with database.sessions.begin() as session:
            session.add(
                EventRecord(
                    event_id="snapshot:private-source",
                    chain="stellar",
                    event_type="market_snapshot",
                    occurred_at=now,
                    source_ref=internal_url,
                    attributes={},
                )
            )
            session.add(
                SignalRecord(
                    signal_id="signal:private-source",
                    deduplication_key="test",
                    event_id="snapshot:private-source",
                    occurred_at=now,
                    category="wide_spread",
                    severity="WATCH",
                    confidence="HIGH",
                    observed_facts={"pair": PAIR, "reference_source": internal_url},
                    interpretation="A test signal.",
                    expires_at=now + timedelta(minutes=30),
                )
            )
        response = await client.get("/api/signals/signal:private-source")
        serialized = response.text

    assert response.status_code == 200
    assert "fixture" not in serialized
    assert response.json()["source"]["source_ref"] is None
    assert response.json()["source"]["explorer_url"] is None
    assert "reference_source" not in response.json()["observed_facts"]


async def _seed_volume_signal(database: Database) -> str:
    from stellar_intelligence_bot.storage.models import TradeRecord

    start = datetime(2026, 1, 1, tzinfo=UTC)
    now = datetime.now(UTC)
    async with database.sessions.begin() as session:
        session.add(
            EventRecord(
                event_id="vol:1", chain="stellar", event_type="volume", occurred_at=now,
                source_ref="https://horizon.stellar.org/trades", attributes={},
            )
        )
        session.add(
            SignalRecord(
                signal_id="vol-signal", deduplication_key="vol", event_id="vol:1",
                occurred_at=now, category="volume_anomaly", severity="HIGH", confidence="HIGH",
                observed_facts={
                    "pair": PAIR, "window_start": start.isoformat(),
                    "window_end": (start + timedelta(minutes=5)).isoformat(),
                    "observed_base_volume": "30",
                },
                interpretation="Observed volume", expires_at=now + timedelta(minutes=30),
            )
        )
        for index, (venue, seller, base) in enumerate(
            (("order_book", True, "20"), ("liquidity_pool", None, "10"))
        ):
            session.add(
                TradeRecord(
                    trade_id=f"t{index}", pair_key=PAIR,
                    occurred_at=start + timedelta(minutes=index + 1),
                    base_amount=Decimal(base), quote_amount=Decimal(base), price=Decimal(1),
                    source_ref="https://horizon.stellar.org/trades/x",
                    venue=venue, base_is_seller=seller, operation_id=f"op{index}",
                    base_account="GACCOUNTSECRET", counter_account=None,
                )
            )
    return "vol-signal"


@pytest.mark.asyncio
async def test_volume_signal_exposes_venue_evidence_without_accounts(tmp_path) -> None:
    settings = Settings(monitored_pairs=(MarketPair.parse(PAIR),))
    async with _api(str(tmp_path / "ev.db"), settings) as (client, database):
        signal_id = await _seed_volume_signal(database)
        detail = await client.get(f"/api/signals/{signal_id}")
        listing = await client.get("/api/signals")
        denied = await client.get(f"/api/signals/{signal_id}/account-diagnostics")
    body = detail.json()
    evidence = body["volume_evidence"]
    assert evidence["matches_signal_volume"] is True
    totals = listing.json()["items"][0]["trade_totals"]
    assert totals["trade_count"] == 2
    assert Decimal(totals["base_amount"]) == 30
    assert Decimal(totals["quote_amount"]) == 30
    assert Decimal(evidence["venues"]["order_book"]["base_volume"]) == 20
    assert "GACCOUNTSECRET" not in detail.text
    assert body["account_diagnostics_available"] is False
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_volume_summary_does_not_claim_incomplete_trade_totals(tmp_path) -> None:
    async with _api(str(tmp_path / "incomplete.db"), Settings()) as (client, database):
        await _seed_volume_signal(database)
        async with database.sessions.begin() as session:
            row = await session.get(SignalRecord, "vol-signal")
            row.observed_facts = row.observed_facts | {"observed_base_volume": "31"}
        listing = await client.get("/api/signals")
        assert listing.json()["items"][0]["trade_totals"] is None


@pytest.mark.asyncio
async def test_account_diagnostics_are_opt_in(tmp_path) -> None:
    settings = Settings(
        monitored_pairs=(MarketPair.parse(PAIR),), account_diagnostics_enabled=True
    )
    async with _api(str(tmp_path / "diag.db"), settings) as (client, database):
        signal_id = await _seed_volume_signal(database)
        response = await client.get(f"/api/signals/{signal_id}/account-diagnostics")
    assert response.status_code == 200
    assert "GACCOUNTSECRET" in response.text


@pytest.mark.asyncio
async def test_episode_review_roundtrip_leaves_signals_untouched(tmp_path) -> None:
    async with _api(str(tmp_path / "reviews.db"), Settings()) as (client, _):
        assert (await client.get("/api/episode-reviews")).json() == {"items": []}
        body = {
            "status": "reviewed",
            "signal_count": 3,
            "max_severity": "HIGH",
            "last_signal_at": "2026-01-01T10:00:00Z",
        }
        saved = await client.put("/api/episode-reviews/sig-1", json=body)
        assert saved.status_code == 200
        assert saved.json()["status"] == "reviewed"
        body["status"] = "dismissed"
        await client.put("/api/episode-reviews/sig-1", json=body)
        items = (await client.get("/api/episode-reviews")).json()["items"]
        assert [(i["episode_key"], i["status"], i["signal_count"]) for i in items] == [
            ("sig-1", "dismissed", 3)
        ]
        bad = await client.put("/api/episode-reviews/sig-1", json={**body, "status": "deleted"})
        assert bad.status_code == 422
        assert (await client.delete("/api/episode-reviews/sig-1")).json()["status"] == "open"
        assert (await client.get("/api/episode-reviews")).json() == {"items": []}


@pytest.mark.asyncio
async def test_removed_calculator_endpoint_is_unavailable(tmp_path) -> None:
    async with _api(str(tmp_path / "removed.db"), Settings()) as (client, _):
        assert (await client.post("/api/paper-scenarios", json={})).status_code == 404
