# Docker Compose deployment

The Compose stack runs the read-only collector, dashboard API, React web UI, and PostgreSQL. The UI and API are read-only and use the collector's existing database. It does not configure Discord, a wallet, transaction signing, or trade execution. A successful local Compose check does not imply deployment to a remote host.

## Requirements

- Docker Engine with Docker Compose v2.
- Outbound HTTPS access to the configured Horizon endpoint.
- Persistent disk space and a regular PostgreSQL backup destination.

## Start

```sh
cp .env.example .env
chmod 600 .env
```

Set `POSTGRES_PASSWORD` to a unique long URL-safe value. To generate a hex password:

```sh
openssl rand -hex 32
```

Set `BOT_MONITORED_PAIRS` and, if needed, `BOT_WHALE_THRESHOLDS`. Leave either blank to disable those features. Then build and start:

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 app
```

Alternatively, `./manage.sh start` builds and starts the same stack. Run
`./manage.sh help` for all commands. `status` lists services, `logs [SERVICE]`
follows the latest logs (interrupt with Ctrl+C), `health` checks the collector,
`report` accepts the collector report options, and `config` validates the Compose
configuration without printing its resolved secrets. `stop` preserves containers
and database data. `restart` restarts existing containers without applying
configuration changes; run `start` after editing `.env` to recreate services as
needed. The script runs from the repository directory even when called elsewhere.

The dashboard is available at `http://127.0.0.1:8080` by default. `DASHBOARD_PORT` configures the host port, `DASHBOARD_BIND_ADDRESS` defaults to `127.0.0.1`, and `DASHBOARD_REFRESH_SECONDS` defaults to 20 (allowed range 5–300 seconds). Only the dashboard frontend publishes a host port; it proxies `/api/` requests to the private API service. Keep the default localhost binding unless access is restricted to a trusted private network by address and firewall. Authentication is intentionally not included.

The `app` waits for PostgreSQL's health check, applies Alembic migrations, then starts collecting. The API waits for PostgreSQL and the web UI waits for the API process. The dashboard may briefly show an API/database error while collector migrations are starting; it retries on the configured polling interval. The database is stored in the existing named `postgres_data` volume. The collector and API run as unprivileged users; the API and web UI also drop Linux capabilities, use read-only root filesystems, and have health checks. The collector has a 30-second graceful-shutdown window. Container logs rotate at 10 MB per file with three files retained.

Check dashboard health and collection status:

```sh
curl http://127.0.0.1:8080/api/health
docker compose ps
docker compose logs --tail=100 api dashboard
```

The API health response reports database availability, collector heartbeat freshness, and the configured refresh interval. Dashboard metrics use actual persisted events, signals, trades, order-book snapshots, and signal evaluations. Markets with no observations show N/A; stale snapshots are marked stale. Historical market-price charts show discrete midprice observation dots instead of connecting observations across gaps. The activity page links only to transaction hashes in the persisted StellarExpert source references.

Check runtime status and signal evaluation reports:

```sh
docker compose exec app python -m stellar_intelligence_bot health
docker compose exec app python -m stellar_intelligence_bot report --since-days 30
```

The app's Docker health check tests the database connection and a successful collector heartbeat within the allowed stale interval. A failed collection cycle is logged and included in health status; normal retry/backoff continues. A container marked unhealthy is not automatically restarted solely for that status, but the restart policy handles process exit.

## Configuration changes

Edit `.env` and recreate the relevant services after changing settings:

```sh
docker compose up -d --force-recreate app
docker compose up -d --force-recreate api dashboard
```

Changing the PostgreSQL credentials for an already-initialized database requires a coordinated database credential update; changing `.env` alone does not rotate the role password stored in PostgreSQL.

## Backup

Create a compressed, consistent logical backup:

```sh
mkdir -p backups
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "backups/stellar-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

Move backups off-host and restrict their access; they contain collected account activity and signal history. Verify backups periodically by restoring into a separate test database. Do not remove the production volume to test recovery.

## Recovery

Test a backup without changing the production database. Choose a new, unused test database name:

```sh
docker compose exec db sh -c 'createdb -U "$POSTGRES_USER" "${POSTGRES_DB}_restore_test"'
docker compose exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "${POSTGRES_DB}_restore_test"' \
  < backups/stellar-YYYYMMDDTHHMMSSZ.dump
docker compose exec db sh -c \
  'psql -U "$POSTGRES_USER" -d "${POSTGRES_DB}_restore_test" -c "SELECT version_num FROM alembic_version"'
```

Inspect the restored data and drop only that explicitly named test database when finished:

```sh
docker compose exec db sh -c 'dropdb -U "$POSTGRES_USER" "${POSTGRES_DB}_restore_test"'
```

For production recovery, use a maintenance window and an operator-approved, verified backup. Stop only this Compose project's app, take a fresh backup of the current production database if it remains readable, restore according to the PostgreSQL `pg_restore` procedures, and verify migrations, collector health, and report output before resuming collection. Never use `docker compose down -v` on production data.

## Retention and storage

`BOT_RETENTION_DAYS` defaults to 90 and must be at least one. Once per collector run day, the app deletes old events, trades, market snapshots, volume windows, signals, and their evaluation rows in one transaction. Processing cursors and runtime health are preserved. Retention does not replace backups or database capacity monitoring. PostgreSQL WAL, indexes, backups, and filesystem space also consume storage.

## Stop and inspect

```sh
docker compose logs --tail=200 app
docker compose stop
```

`docker compose stop` preserves the database volume. Avoid `docker compose down -v` unless permanent deletion of this stack's PostgreSQL data is intentionally approved.

## Deployment verification record

No VPS or external production host is configured by this repository. Before describing a remote deployment as complete, provision the host, secure the `.env` file and backup destination, start this Compose project there, and verify healthy app/database status and a successful recent Horizon collection heartbeat.
