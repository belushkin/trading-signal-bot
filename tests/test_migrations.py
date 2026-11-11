import sqlite3

from stellar_intelligence_bot.app.main import _upgrade_schema


def test_initial_migration_creates_persistent_schema(tmp_path) -> None:
    database_path = tmp_path / "migrated.db"
    _upgrade_schema(f"sqlite+aiosqlite:///{database_path}")

    with sqlite3.connect(database_path) as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert version == ("0005_trade_provenance",)
    assert {
        "events",
        "signals",
        "processing_cursors",
        "trades",
        "volume_windows",
        "market_snapshots",
        "signal_evaluations",
        "runtime_health",
    } <= tables
    with sqlite3.connect(database_path) as connection:
        event_indexes = {row[1] for row in connection.execute("PRAGMA index_list('events')")}
        signal_indexes = {row[1] for row in connection.execute("PRAGMA index_list('signals')")}
    assert "ix_events_type_time" in event_indexes
    assert "ix_signals_time_category_severity" in signal_indexes
