# Architecture

## Phase 2 components

- `app/` loads validated environment settings, configures logging, and runs the collector with bounded retry backoff.
- `chains/stellar/` uses `stellar-sdk` to validate and serialize assets and `aiohttp` to access the public Horizon REST API asynchronously. It polls ledgers, payment operations, configured trade pairs, and current order-book snapshots.
- `chains/base.py` defines a read-only collector boundary and a collected-event envelope. Source cursors remain collection metadata, not domain-event attributes.
- `core/` holds chain-neutral event/signal types, Stellar market identifiers, and deterministic evidence-first rules.
- `workers/` commits observations and evaluates signals independently of Horizon response parsing.
- `storage/` persists events, trades, five-minute volume windows, market snapshots, signal history, and recovery cursors using SQLAlchemy 2 async. Alembic migrations are applied at startup. SQLite is the local default; PostgreSQL URLs use `asyncpg`.

Cursor advancement is committed atomically with the corresponding event and, for trades, their volume aggregates. Non-payment operations are skipped without being retained, but their cursor is advanced. Duplicate event IDs do not duplicate observations or signal evaluation. Signal records use deterministic IDs and 15-minute category-specific deduplication cooldowns.

## Signal definitions

- **Whale transfer:** emitted only when an operator configures a threshold for the payment asset. Severity is `WATCH`, or `HIGH` at ten times the threshold. No account ownership or trading intent is inferred.
- **Unusual volume:** compares the current five-minute base-asset volume to the mean of the configured number of latest prior windows that contain trades. No signal is emitted until enough positive-baseline windows exist.
- **Visible liquidity:** flags a current order-book level at or above the configured base-asset amount. The signal explicitly says this is not evidence of execution.
- **Wide spread:** flags the current `(ask - bid) / ask` percentage at or above the configured threshold. It is an observation, not a price prediction.

Confidence is qualitative. `HIGH` means a signal is supported by complete direct source measurements and a configured rule; it does not mean a high probability of future price movement.

## Signal evaluation

Eligible pair-based market signals capture the two-sided order-book midpoint at signal time as their reference price. The main collection loop evaluates the first valid two-sided midpoint at or after each 5-minute, 15-minute, 1-hour, 4-hour, and 24-hour target. The observation must arrive within five minutes after the target. Missing outcomes are persisted as missing; the evaluator never substitutes the latest stale observation.

Each evaluation stores the reference and measured price/time, signed observed percent change, maximum sampled increase and decrease from the reference up to the target, number of samples, largest sample gap, and data-quality status. These values describe observed price movement only. Signals currently have no direction field, so the system does not label outcomes favorable/adverse, calculate directional accuracy, or claim realized P&L or profitability. Sampled extrema may miss changes between observations. Signals without a pair or valid reference price are excluded from evaluation.

`python -m stellar_intelligence_bot report` prints JSON summary statistics grouped by category, pair, severity, confidence, and horizon, including measured/missing/pending counts and data-quality counts. `--since-days` and `--horizon-minutes` limit the report.

## Data collection limits

Horizon is queried via its supported REST resources. The collector bootstraps each stream from the latest returned page and then follows ascending cursor pages. It does not backfill full history or fetch every transaction body. Payments retain amount, asset, source/destination accounts, operation type, event time, and transaction explorer reference. Ledger events retain sequence, hash, close time, and transaction counts.

The current volume baseline does not materialize zero-trade windows. An empty market interval therefore does not lower the baseline. Historical order-book reconstruction, AMM pool monitoring, explicit application-health persistence, and Discord remain future work.

## Future integration

Do not begin XRPL implementation until the Stellar collector and signal quality are reliable. An XRPL collector should map only genuinely shared event concepts into `ChainEvent`; keep network-specific concepts in their native forms rather than forcing them into a misleading common abstraction.

## Safety and operations

The application has no wallet, signing, order-submission, or transaction-execution interfaces. The default database is a local SQLite file. PostgreSQL async URLs, Alembic migrations, configurable retention, and a collector/database health check are supported. Docker Compose deployment and operational backup instructions are documented; Discord delivery and remote production deployment are not implemented or verified.

## Dependencies

Runtime ranges in `pyproject.toml` require Python 3.12+, `stellar-sdk` 12.x/13.x, `aiohttp` 3.x, SQLAlchemy 2.x with its asyncio extra, `aiosqlite` for SQLite, and `asyncpg` for PostgreSQL. Development ranges include pytest, pytest-asyncio, Ruff, mypy, and Alembic. Ranges are chosen to constrain compatible major versions; the lockfile/reproducible deployment image remains future deployment work.
