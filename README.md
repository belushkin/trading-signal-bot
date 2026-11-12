# Stellar Intelligence Bot

A read-only, human-in-the-loop Stellar activity and market monitor. It does not manage wallets, sign or submit transactions, or place trades.

## Current implementation

The service currently collects new ledger summaries and payment operations from public Stellar Horizon, plus trade history and order-book snapshots for configured pairs. It persists normalized events, trades, five-minute volume windows, market snapshots, signal history, and per-stream cursors. Market signals compare spread, sampled mid-price movements and completed trade volume with each pair's own history. Configured large-transfer signals remain separate.

Signal outcome evaluation, PostgreSQL deployment, health checks, retention, and a read-only web dashboard are implemented. The dashboard reads persisted collector data through a FastAPI API and does not connect directly to Stellar. Discord notifications and control commands are intentionally omitted for now.

Large transfers are not interpreted as buys or sells. The prior fixed-spread, visible-offer and volume rules no longer run. The new volume and large-trade rules require recorded cursor-based scans and Horizon ledger watermarks; pre-existing observations cannot establish that coverage. Depth-change alerts are not implemented because top-20 order books are not fixed price ranges. This MVP does not reconstruct historical order books, collect every transaction body, monitor AMM pools, or deliver Discord notifications.

The spread rule needs a fresh two-sided book and at least 20 observations over a continuous hour, with gaps of at most two minutes; it triggers at the greater of the configured floor and three times the pair's median spread. Price movement uses 5, 15, 60 and 240 minute windows by default. Each needs continuous samples across the current window and three preceding equivalent windows, with at least three prices near each endpoint; the threshold is the greater of the configured floor and three times the median preceding absolute return. These are exploratory thresholds, not demonstrated predictive value. Price changes use sampled bid/ask midpoints, not executed-trade prices.

The volume rule compares the latest completed five-minute window against the mean of the configured number of preceding completed windows. A saved Horizon history-ledger watermark and successful cursor-based scan must cover the full interval, with no gaps over two minutes and a fresh watermark; the first truncated bootstrap scan does not establish coverage. Only then can a window without persisted trades count as zero. Volumes combine order-book and liquidity-pool trades and cannot be attributed to either venue. Provider history and watermark correctness remain assumptions.

Ledger and operation streams process at most five Horizon pages per cycle so their backlog cannot indefinitely starve market collection. Their persisted cursors resume on the next cycle, so activity can lag even while the collector heartbeat is healthy; monitor the latest event timestamps as well as health.

Horizon can report a tiny trade with a positive base amount and zero quote amount. The collector persists the actual amounts and the provider's rational price separately; the dashboard does not present that rational price as an executed price, and the large-trade rule excludes zero-notional trades.

Large-trade alerts compare the trade's quote-asset notional with the median of up to 500 prior trades in the same pair and venue over the preceding hour. They require at least 100 prior trades, continuous watermarked scans, a fresh trade, ten times the median, and at least 10,000 XLM by default. The XLM floor prevents a tiny relative baseline alone from creating an alert; it is an exploratory minimum, not a valuation in USD. Unknown venue trades and non-XLM-base pairs are excluded. The signal preserves the original trade and does not infer intent.

Five subsequent midpoint observations are evaluated at 5, 15, 60, 240 and 1440 minutes when data is available. Analytics excludes legacy signals, including those created before the bid-depth correction; their records remain visible in Signals and are not recalculated. Returns are descriptive, not profitability or directional accuracy. Averages and percentages in the dashboard require at least ten measured outcomes.

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

Replace `G...` with a real Stellar issuer public key. Configure monitored pairs as comma-separated `BASE/QUOTE` asset identifiers; each asset is `native` or `CODE:ISSUER`. Configure whale thresholds as comma-separated `ASSET=AMOUNT` entries in that asset's units. By default the verified Tier 1 watchlist is enabled. Set `BOT_WATCHLIST_ENABLED=''` to disable all default markets; ledger and payment collection continues.

Settings:

| Variable | Default | Description |
|---|---:|---|
| `BOT_HORIZON_URL` | `https://horizon.stellar.org` | Public or self-hosted Horizon URL |
| `BOT_DATABASE_URL` | `sqlite+aiosqlite:///./stellar-intelligence.db` | SQLAlchemy async SQLite or PostgreSQL URL (`postgresql+asyncpg://...`) |
| `BOT_MONITORED_PAIRS` | empty | Non-empty comma-separated `BASE/QUOTE` pairs override the watchlist |
| `BOT_WATCHLIST_ENABLED` | `USDC,SHX,VELO,AQUA` | Comma-separated watchlist codes; add `BTC` or `XRP` to enable Tier 2, empty disables all |
| `BOT_WHALE_THRESHOLDS` | empty | Per-asset transfer thresholds, e.g. `native=100000` |
| `BOT_POLL_INTERVAL_SECONDS` | `15` | Poll interval; minimum 5 seconds |
| `BOT_VOLUME_BASELINE_SAMPLES` | `20` | Prior covered five-minute windows needed for volume baseline |
| `BOT_VOLUME_ANOMALY_MULTIPLIER` | `3` | Completed-window volume threshold relative to mean baseline |
| `BOT_LARGE_ORDER_THRESHOLD` | `10000` | Legacy setting; visible-offer alerts are disabled |
| `BOT_LARGE_TRADE_MIN_XLM` | `10000` | Absolute XLM minimum for large-trade alerts |
| `BOT_SPREAD_THRESHOLD_PERCENT` | `1` | Minimum percentage threshold for baseline-relative spread widening |
| `BOT_PRICE_ANOMALY_FLOOR_PERCENT` | `1` | Minimum absolute sampled mid-price change percentage |
| `BOT_PRICE_WINDOWS_MINUTES` | `5,15,60,240` | Enabled price windows, selected from 5, 15, 60 and 240 minutes |
| `BOT_LOG_LEVEL` | `INFO` | Standard logging level |
| `BOT_RETENTION_DAYS` | `30` | Delete observations older than this many days during periodic cleanup |
| `BOT_ACCOUNT_DIAGNOSTICS` | `false` | Enables the optional `/api/signals/{id}/account-diagnostics` endpoint. Account identities are never part of the default public signal views |

The exact issuer identities and tier defaults are defined once in [watchlist.json](src/stellar_intelligence_bot/app/watchlist.json); see [the market verification record](docs/market-watchlist.md) for sources and limitations. A non-empty `BOT_MONITORED_PAIRS` takes precedence over `BOT_WATCHLIST_ENABLED`; an empty `BOT_MONITORED_PAIRS` alone no longer disables the default watchlist.

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

For common operations, run `./manage.sh help`. For example, `./manage.sh start`
builds and starts the stack, `./manage.sh status` shows service status,
`./manage.sh logs app` follows collector logs, `./manage.sh health` checks the
collector, and `./manage.sh stop` stops services without deleting data.

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
- The volume baseline uses the latest persisted five-minute windows containing trades. Signals remain unavailable until enough baseline windows exist; zero-trade windows are not currently materialized. Horizon trade volume can include order-book and liquidity-pool trades; trades stored before migration `0005` have no venue until the explicit backfill below is run.
- Prior to the watchlist update, order-book bid amounts were treated as base units rather than quote units. Older bid-depth readings are hidden in the dashboard, but historical signals and evaluations are not recomputed. Earlier snapshots also used rounded Horizon prices.
- Confidence is qualitative, not a probability: `HIGH` means the signal's measured facts come directly from complete source fields and satisfy a configured rule. It does not imply predictive accuracy.
- Signal severity is rule-based. A transfer at least ten times its configured threshold is `HIGH`; other configured transfer breaches are `WATCH`. Market anomaly and order-book rules currently emit `WATCH`, except an extreme volume breach (at least three times its configured trigger), which emits `HIGH`.
- Signals expire after 30 minutes and matching deduplication keys have a 15-minute cooldown.

## Market episodes and review state

The dashboard overview groups individual signals into market episodes in the browser (`web/src/episodes.ts`); no signal is changed or hidden. Signals are grouped only when they share the exact same market (pair and issuers) and are close in time: the same signal type within 30 minutes, or different types within 10 minutes of each other (measurement windows are used when stored). An episode spans at most 6 hours. Trade and operation IDs are not stored on signals, so they are not used for grouping.

Recommended human actions are rule-based: *Investigate* needs a High or Critical signal (or two signal families at Watch or above) in the last 6 hours with usable stored measurements; a lone large trade is never escalated on size alone. *Monitor* covers recent lower-severity activity, *Informational* covers old or low-severity episodes, and episodes without stored measurements get "no recommendation". These are prompts for human review, never trading advice.

Marking an episode reviewed or dismissed is stored in the `episode_reviews` table (migration `0006_episode_reviews`, a new table only) through `GET /api/episode-reviews`, `PUT`/`DELETE /api/episode-reviews/{episode_key}`. A reviewed episode returns to the attention list only if later evidence has a higher severity; dismissed episodes stay closed. The write endpoints have no authentication, like the rest of the dashboard API, so keep it on a trusted network.

## Actionable signals

The assistant applies the configurable, **unvalidated** `momentum-v1` rule to fresh price-movement anomalies: an upward movement proposes BUY, and a downward movement proposes SELL. It only uses signals produced after settings were saved, within two minutes of their source snapshot. It requires a two-sided book and enough recorded best-level liquidity for the proposed quantity. Only pairs with XLM as the base are currently supported. The rule is exploratory and does not establish a trading edge.

Portfolio settings persist in PostgreSQL under exact asset identities. The allocation percentage limits the available asset balance, not total portfolio value. XLM reserve and two user-assumed network fees are held back when sizing a SELL. The target models the entry bid/ask and converts the two assumed XLM fees using the observed bid; it aims for at least 1% on the quote amount after these assumptions. Actual fees, future spread and slippage remain unknown, so the target and profit are estimates, not verified net returns. No AMM route or fill is simulated.

Each emitted idea retains its original parameters and references. Follow-up samples bid/ask conditions at 5 and 10 minutes, 1, 4 and 24 hours, and 7 days. A horizon without a valid observation in its final two minutes is unknown; gaps can hide price movements even when an endpoint exists. An observed entry or target is not proof of executable liquidity, an executed transaction, or realized profit. Screening runs in the collector; the overview warns when its heartbeat is stale. Historical observations depend on collector uptime and retention.

New order-book snapshots retain the individual levels returned by the existing Horizon request (up to 20 per side) in snapshot facts, with price units, original amount units and rational prices when provided. The market detail API exposes the latest snapshot's levels when recorded, and the market detail dialog displays them under **Latest recorded order-book levels**. Earlier snapshots do not contain these levels. A snapshot is not a guarantee of liquidity at execution time and does not provide queue priority, network fee estimates or AMM reserves.

The API has no authentication. Anyone with access to it can read and modify persisted portfolio balances and settings; run it only on a trusted network. No wallet access or keys are used.

## Signal evaluation

Market signals with a pair and a valid two-sided reference snapshot record their midpoint at signal time. The collector evaluates the first valid two-sided order-book midpoint at or after 5 minutes, 15 minutes, 1 hour, 4 hours, and 24 hours. An observation must arrive within five minutes after the target; otherwise the result is recorded as missing rather than substituted with stale data. Evaluation runs continuously with collection, so the service must remain running for later horizons to complete.

Evaluations persist the reference and measured price/time, signed observed percent change, sampled maximum increase/decrease before the target, observation count, largest sample gap, and data-quality status. These are descriptive forward-price observations only—not realized P&L, profitability, or directional accuracy. Sampled extrema are not guaranteed to capture movement between observations. Signals without a suitable market pair/reference price are not evaluated.

### Trade-price fallback

Snapshot mid-price is always preferred. If no valid two-sided snapshot exists within the five-minute grace window after a target, the evaluation may use persisted trade prices instead, labelled `price_basis=trade_price` (snapshot results are `snapshot_mid`). Requirements: a trade inside `[target, target+5 min]`, a reference trade at or before signal time within 15 minutes, and at least three trades spanning reference to measurement. The two bases are never mixed within one evaluation (the reference price is also a trade price), nothing is interpolated, and `missing_reason` records why an evaluation stayed missing (`no_snapshot_and_no_trade_in_grace`, `no_reference_trade_in_lookback`, `insufficient_trade_observations`). Analytics report the two bases in separate rows.

### Trade provenance and explicit backfills

Migration `0005_trade_provenance` adds nullable columns (venue, `base_is_seller`, asset identities, operation id, transaction hash when present, account identities, pool id, observation time). `NULL` means "not recorded"; missing direction or accounts are never inferred. Horizon trade records do not include a transaction hash, and `base_is_seller` is orientation only, not the aggressor side. Two explicit, idempotent commands handle historical rows:

```sh
python -m stellar_intelligence_bot backfill-trades --since-hours 26   # fills only empty provenance columns of stored trades; never inserts; observed_at stays NULL
python -m stellar_intelligence_bot reevaluate-missing --dry-run       # preview; omit --dry-run to apply to missing evaluations only
```

`reevaluate-missing` never changes measured evaluations. Evaluations with `price_basis` empty are legacy snapshot results.

Malformed, zero-base (dust) or otherwise invalid Horizon trades are skipped with a logged reason, counted in collector health (`skipped_trade_count`) and their cursor still advances; consecutive failed cycles are counted as interruptions.

Print grouped summary statistics for the last 30 days:

```sh
python -m stellar_intelligence_bot report
python -m stellar_intelligence_bot report --since-days 90 --horizon-minutes 60
```

The JSON report groups by category, pair, severity, confidence, and horizon, and includes measured, missing, and pending counts, data-quality counts, sample-gap information, and observed price-change summaries. No trading-performance or profitability metric is calculated.

See [docs/architecture.md](docs/architecture.md) for module boundaries, evaluation semantics, and known limitations.
