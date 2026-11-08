# Stellar Intelligence Bot

A read-only, human-in-the-loop Stellar activity and market monitor. It does not manage wallets, sign or submit transactions, or place trades.

## Current implementation

The service currently collects new ledger summaries and payment operations from public Stellar Horizon, plus trade history and order-book snapshots for configured pairs. It persists normalized events, trades, five-minute volume windows, market snapshots, signal history, and per-stream cursors. Signals cover configured large transfers, unusually high pair volume after a baseline is available, large visible orders, and wide spreads.

Signal outcome evaluation, PostgreSQL deployment, health checks, retention, and a read-only web dashboard are implemented. The dashboard reads persisted collector data through a FastAPI API and does not connect directly to Stellar. Discord notifications and control commands are intentionally omitted for now.

Large transfers are not interpreted as buys or sells. Visible offers can change or cancel and are not evidence of execution. This MVP does not reconstruct historical order books, collect every transaction body, monitor AMM pools, or deliver Discord notifications.

## Run locally

Requires Python 3.12 or newer.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'

export BOT_MONITORED_PAIRS='native/USDC:G...'
export BOT_WHALE_THRESHOLDS='native=100000,USDC:G...=50000'
python -m stellar_intelligence_bot
```

Replace `G...` with a real Stellar issuer public key. Configure monitored pairs as comma-separated `BASE/QUOTE` asset identifiers; each asset is `native` or `CODE:ISSUER`. Configure whale thresholds as comma-separated `ASSET=AMOUNT` entries in that asset's units. With no pairs configured, the process still collects ledgers and payment operations but does not collect market data.

Settings:

| Variable | Default | Description |
|---|---:|---|
| `BOT_HORIZON_URL` | `https://horizon.stellar.org` | Public or self-hosted Horizon URL |
| `BOT_DATABASE_URL` | `sqlite+aiosqlite:///./stellar-intelligence.db` | SQLAlchemy async SQLite or PostgreSQL URL (`postgresql+asyncpg://...`) |
| `BOT_MONITORED_PAIRS` | empty | Comma-separated `BASE/QUOTE` pairs |
| `BOT_WHALE_THRESHOLDS` | empty | Per-asset transfer thresholds, e.g. `native=100000` |
| `BOT_POLL_INTERVAL_SECONDS` | `15` | Poll interval; minimum 5 seconds |
| `BOT_VOLUME_BASELINE_SAMPLES` | `20` | Number of preceding five-minute trade windows required |
| `BOT_VOLUME_ANOMALY_MULTIPLIER` | `3` | Current volume threshold relative to mean baseline |
| `BOT_LARGE_ORDER_THRESHOLD` | `10000` | Minimum visible order size in base-asset units |
| `BOT_SPREAD_THRESHOLD_PERCENT` | `1` | Wide-spread signal threshold |
| `BOT_LOG_LEVEL` | `INFO` | Standard logging level |

The process applies Alembic migrations on startup. To run or inspect migrations manually, use `alembic upgrade head` or `alembic current`. PostgreSQL is supported for persistence. Configure retention and follow the backup and health-check procedures in the deployment guide; do not put credentials in the repository.

## Docker Compose

The PostgreSQL Compose deployment includes the collector, read-only dashboard API, and React dashboard. See [docs/deployment.md](docs/deployment.md) for dashboard access, health checks, retention, backup/recovery steps, and operational limits.

```sh
cp .env.example .env
chmod 600 .env
# Set POSTGRES_PASSWORD and desired monitoring settings in .env.
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8080/api/health
docker compose exec app python -m stellar_intelligence_bot health
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). The dashboard binds to localhost by default and polls persisted data; configure `DASHBOARD_PORT` and `DASHBOARD_REFRESH_SECONDS` in `.env` as needed.

Do not run `docker compose down -v` on a deployment with data you need; it removes the PostgreSQL volume.

## Validation

```sh
python -m pytest
ruff check .
mypy src
```

## Data and signal limitations

- Data comes from the configured Horizon endpoint. Availability, rate limits, and historical retention depend on that operator. The client handles pagination, transient 429/5xx/network failures, and retry backoff.
- When a stream has no saved cursor, collection bootstraps from Horizon's latest page; older history is not backfilled. Cursor advancement and event persistence share a database transaction, and duplicate event IDs are ignored.
- The volume baseline uses the latest persisted five-minute windows containing trades. Signals remain unavailable until enough baseline windows exist; zero-trade windows are not currently materialized.
- Confidence is qualitative, not a probability: `HIGH` means the signal's measured facts come directly from complete source fields and satisfy a configured rule. It does not imply predictive accuracy.
- Signal severity is rule-based. A transfer at least ten times its configured threshold is `HIGH`; other configured transfer breaches are `WATCH`. Market anomaly and order-book rules currently emit `WATCH`, except an extreme volume breach (at least three times its configured trigger), which emits `HIGH`.
- Signals expire after 30 minutes and matching deduplication keys have a 15-minute cooldown.

## Signal evaluation

Market signals with a pair and a valid two-sided reference snapshot record their midpoint at signal time. The collector evaluates the first valid two-sided order-book midpoint at or after 5 minutes, 15 minutes, 1 hour, 4 hours, and 24 hours. An observation must arrive within five minutes after the target; otherwise the result is recorded as missing rather than substituted with stale data. Evaluation runs continuously with collection, so the service must remain running for later horizons to complete.

Evaluations persist the reference and measured price/time, signed observed percent change, sampled maximum increase/decrease before the target, observation count, largest sample gap, and data-quality status. These are descriptive forward-price observations only—not realized P&L, profitability, or directional accuracy. Sampled extrema are not guaranteed to capture movement between observations. Signals without a suitable market pair/reference price are not evaluated.

Print grouped summary statistics for the last 30 days:

```sh
python -m stellar_intelligence_bot report
python -m stellar_intelligence_bot report --since-days 90 --horizon-minutes 60
```

The JSON report groups by category, pair, severity, confidence, and horizon, and includes measured, missing, and pending counts, data-quality counts, sample-gap information, and observed price-change summaries. No trading-performance or profitability metric is calculated.

See [docs/architecture.md](docs/architecture.md) for module boundaries, evaluation semantics, and known limitations.
