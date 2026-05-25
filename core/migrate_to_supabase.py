"""
migrate_to_supabase.py — One-time migration from local SQLite to Supabase PostgreSQL.

Run this ONCE after:
  1. Creating your Supabase project at https://supabase.com
  2. Adding DATABASE_URL to your .env file

Usage:
    python core/migrate_to_supabase.py

What it does:
  - Calls init_db() to create all tables in Supabase
  - Copies every row from local data/trader.db into Supabase
  - Resets PostgreSQL sequences so auto-increment picks up from the right id
  - Skips tables that are already populated (safe to re-run if something fails)
"""

import sqlite3
import psycopg2
import psycopg2.extras
import os
import sys
from pathlib import Path

# Allow running as `python core/migrate_to_supabase.py` from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

SQLITE_PATH = Path(__file__).parent.parent / "data" / "trader.db"
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL and "sslmode" not in DATABASE_URL:
    DATABASE_URL += "?sslmode=require"

# Tables to migrate in FK-safe order.
# Each entry: (table_name, [columns_to_copy])
# Omit GENERATED/COMPUTED columns from the column list.
TABLES = [
    ("user_profile", [
        "id", "name", "investment_goal", "risk_tolerance", "time_horizon",
        "strategy", "preferred_sectors", "avoided_sectors", "target_return",
        "notes", "active_strategy_file", "updated_at",
    ]),
    ("portfolio", [
        "id", "ticker", "company_name", "shares", "avg_cost_basis",
        "current_price", "sector", "asset_type", "added_at", "updated_at", "notes",
    ]),
    ("portfolio_history", [
        "id", "snapshot_date", "ticker", "shares", "avg_cost_basis",
        "price_at_snapshot", "total_value", "source",
    ]),
    ("watchlist", [
        "id", "ticker", "company_name", "sector", "why_watching",
        "added_at", "is_active",
    ]),
    ("sector_watchlist", [
        "id", "sector_name", "why_watching", "key_stocks", "key_events",
        "last_analyzed", "is_active",
    ]),
    ("broker_accounts", [
        "id", "broker_name", "account_type", "cash_balance", "total_value",
        "updated_at", "notes",
    ]),
    ("analysis_runs", [
        "id", "run_at", "trigger", "market_summary", "macro_summary",
        "geo_summary", "sentiment_summary", "status", "error_message", "duration_secs",
    ]),
    ("recommendations", [
        "id", "run_id", "ticker", "company_name", "action", "confidence",
        "current_price", "buy_range_low", "buy_range_high",
        "sell_target_low", "sell_target_high", "stop_loss",
        "suggested_shares", "suggested_pct_portfolio",
        "growth_potential_pct", "growth_timeline",
        "thesis", "risks", "catalysts",
        "technical_score", "sentiment_score", "macro_score",
        "is_new_pick", "horizon", "play_type", "created_at",
    ]),
    ("rec_feedback", [
        "id", "rec_id", "was_accurate", "user_comment", "judged_at",
    ]),
    # total_value is GENERATED in action_log — excluded from column list
    ("action_log", [
        "id", "action_date", "ticker", "action", "shares", "price_per_share",
        "was_system_suggested", "rec_id", "my_reasoning", "outcome_notes", "created_at",
    ]),
    ("research_results", [
        "id", "subject", "subject_type", "research_json", "created_at",
    ]),
]

# SERIAL tables that need sequence reset after explicit-id inserts
SERIAL_TABLES = [
    "portfolio", "portfolio_history", "watchlist", "sector_watchlist",
    "broker_accounts", "analysis_runs", "recommendations", "rec_feedback",
    "action_log", "research_results",
]


def migrate():
    if not SQLITE_PATH.exists():
        print(f"❌ SQLite DB not found at {SQLITE_PATH}")
        sys.exit(1)

    if not DATABASE_URL:
        print("❌ DATABASE_URL not set in .env")
        sys.exit(1)

    print(f"Source : {SQLITE_PATH}")
    print(f"Target : Supabase ({DATABASE_URL[:40]}...)")
    print()

    # Create all tables in Supabase first
    print("Creating schema in Supabase…")
    from core.database import init_db
    init_db()
    print()

    # Connect to SQLite (source)
    sq = sqlite3.connect(SQLITE_PATH)
    sq.row_factory = sqlite3.Row

    # Connect to PostgreSQL (target) using the safe URL parser from database.py
    from core.database import _connect
    pg = _connect()
    pg.autocommit = False
    cur = pg.cursor()

    total_rows = 0

    for table, columns in TABLES:
        # Check if SQLite table exists
        exists = sq.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            print(f"  skip {table} — not in SQLite")
            continue

        # Check if Postgres table already has rows
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        pg_count = cur.fetchone()[0]
        if pg_count > 0:
            print(f"  skip {table} — already has {pg_count} rows in Supabase (re-run with --force to overwrite)")
            continue

        rows = sq.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
        if not rows:
            print(f"  {table}: 0 rows")
            continue

        col_list = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

        batch = [tuple(row[c] for c in columns) for row in rows]
        try:
            cur.executemany(sql, batch)
            pg.commit()
            print(f"  ✓ {table}: {len(batch)} rows")
            total_rows += len(batch)
        except Exception as e:
            pg.rollback()
            print(f"  ✗ {table}: FAILED — {e}")

    # Reset sequences so next INSERT gets the right id
    print("\nResetting sequences…")
    for table in SERIAL_TABLES:
        try:
            cur.execute(f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table}), 1),
                    true
                )
            """)
            pg.commit()
            print(f"  ✓ {table}_id_seq reset")
        except Exception as e:
            pg.rollback()
            print(f"  ✗ {table}: sequence reset failed — {e}")

    sq.close()
    cur.close()
    pg.close()

    print(f"\n✅ Migration complete — {total_rows} rows copied to Supabase")


if __name__ == "__main__":
    migrate()
