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

    assert version == ("0003_runtime_health",)
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
