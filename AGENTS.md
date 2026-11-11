# AGENTS.md

## 1. Project Context
This repository contains a Stellar Market Intelligence platform.

Its purpose is to:

- Monitor selected Stellar DEX markets.
- Detect unusual market activity.
- Preserve evidence and data provenance.
- Evaluate subsequent market price movements.
- Present understandable market insights through a web dashboard.

This is a market intelligence and monitoring product, NOT an automated trading bot.

### Explicitly out of scope
Do not introduce these without explicit user approval:

- Automated trading or transaction execution.
- Wallet integrations, private keys or transaction signing.
- Binance, Kraken, CCXT or unrelated exchanges.
- XRP Ledger support.
- New blockchain integrations.
- BigQuery or Hubble.
- LLM-based signal generation.
- Discord or Telegram integrations.
- New monitored pairs.
- New infrastructure services without demonstrated need.

## 2. Architecture
Use the existing architecture and conventions.

Current architecture:

- Python backend.
- FastAPI API.
- PostgreSQL persistence.
- SQLAlchemy 2 and Alembic migrations.
- Stellar Horizon data collection.
- React and TypeScript frontend.
- Docker Compose deployment.

PostgreSQL is the source of truth.

The dashboard must consume persisted data through the API. Do not introduce a second database or frontend-specific persistence layer.

Do not introduce a vector database. Prefer SQL queries and structured analytics for historical signal retrieval.

## 3. Critical Engineering Rules

### Data integrity

- Never invent missing market data.
- Missing values must remain NULL when unknown.
- Never infer trade aggressor direction from base_is_seller.
- Distinguish trade price from order-book mid-price.
- Never mix different price bases within one evaluation.
- Never describe observed price changes as profit or trading performance.
- Never claim causality between a signal and subsequent price movement.
- Preserve source references and provenance.
- Historical backfills must be idempotent.
- Never insert reconstructed trades unless explicitly requested.

### Signal engine

- Do not change anomaly thresholds or signal-generation logic unless explicitly requested.
- Do not modify existing signal semantics during unrelated tasks.
- Preserve deduplication and expiration behaviour.
- Do not recalculate historical signals unless explicitly requested.

### Database

- Every schema change requires an Alembic migration.
- Preserve existing database volumes and records.
- Do not drop tables or columns without explicit approval.
- Inspect migration impact before applying migrations to a live database.
- Never reset the production or development database as a shortcut.

### Collector

- Preserve cursor and watermark correctness.
- Malformed individual trades may be skipped only according to existing collector rules.
- Page-level failures must remain retryable.
- Preserve duplicate protection.
- Add regression tests for collector resilience changes.

## 4. Token and Context Efficiency
These instructions are mandatory.

### Repository exploration

- Read this file first.
- Do not inspect the entire repository at the beginning of every task.
- Start with git status and a targeted search for relevant symbols.
- Read only files directly related to the task.
- Follow existing implementation patterns instead of rediscovering architecture.
- Do not reread files already inspected unless they have changed or additional context is necessary.
- Do not repeat investigations documented in README, tests or existing reports.

### Tool usage

- Prefer targeted commands such as:
  - `rg`
  - `git diff`
  - `git status --short`
  - targeted pytest invocations
  - focused SQL queries
- Avoid dumping entire files, database tables or logs.
- Limit log output to relevant time ranges and error context.
- Use summaries and aggregates instead of raw records where possible.
- Do not run expensive broad diagnostics unless the task requires them.

### Implementation

- Make the smallest change that solves the task.
- Avoid unrelated refactoring.
- Do not add dependencies without a clear requirement.
- Do not introduce new services or infrastructure without justification.
- Reuse existing API endpoints and components.
- Avoid speculative improvements and unnecessary abstractions.

### Testing

- Run focused tests first.
- Run the full relevant test suite before declaring completion.
- Do not rerun unchanged tests without a reason.
- Report exactly which tests were run and their results.
- Never claim tests passed if they were not executed.

### Reporting
Keep final reports concise.

Always include:

1. What changed.
2. Important implementation decisions.
3. Tests and verification results.
4. Database or migration impact.
5. Remaining limitations.
6. Whether changes were committed or pushed.

Do not repeat the entire task description in the report.

## 5. Dashboard Product Principles
The dashboard is intended for a user who is not necessarily familiar with trading terminology.

Prioritize:

- Plain-language explanations.
- Clear market-event summaries.
- Historical comparisons.
- Transparent evidence.
- Explicit uncertainty and data limitations.

Keep technical diagnostics available in a separate expandable section.

Do not expose internal database terminology as the primary user-facing explanation.

All explanations must be grounded in persisted evidence and deterministic rules. Do not invent market narratives.

## 6. Development Workflow
For each task:

1. Read this file.
2. Inspect git status.
3. Identify the smallest relevant set of files.
4. Briefly explain the proposed approach if the task is substantial.
5. Implement only the requested scope.
6. Add or update focused regression tests.
7. Run relevant verification.
8. Inspect the final diff.
9. Provide a concise report.

Do not commit or push unless explicitly requested.

Do not start another task or expand scope after completing the requested work.

## 7. Current Project Priorities
Current priorities:

1. Preserve collector reliability.
2. Maintain data provenance and evaluation correctness.
3. Improve dashboard readability and human explanations.
4. Collect real market observations.
5. Evaluate signal usefulness based on sufficient historical evidence.

Do not prioritize new features, new pairs or new infrastructure over these goals.

When task requirements conflict with this document, follow the user's latest explicit instruction, while preserving data integrity and safety.
