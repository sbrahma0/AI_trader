"""
database.py — PostgreSQL (Supabase) schema and all DB operations for AI Trader.

Tables:
  user_profile       — investment goals, risk tolerance, targets
  portfolio          — current holdings (updated via action_log or PDF import)
  portfolio_history  — snapshots of portfolio over time
  action_log         — user-recorded trades (manual journal)
  watchlist          — individual stocks to monitor
  sector_watchlist   — whole sectors to track (Space, Quantum, etc.)
  broker_accounts    — cash and account balances per broker
  analysis_runs      — record of every analysis execution
  recommendations    — per-stock recommendations from each run
  rec_feedback       — user feedback on recommendations
  research_results   — cached on-demand research (stock or sector)
"""

import psycopg2
import psycopg2.extras
import os
import re
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

_DATABASE_URL = os.getenv("DATABASE_URL", "")


def _connect():
    """
    Parse the DATABASE_URL and connect via keyword args so that
    passwords containing special chars (@, ?, [, ], +) are handled
    correctly without requiring URL-encoding.

    Key insight: find the last @ FIRST to split userinfo from host,
    then strip query params only from the host portion — never from
    the userinfo where ? may appear inside the password.
    """
    url = _DATABASE_URL

    # Remove scheme prefix
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
            break

    # Split at LAST @ — handles @ inside passwords correctly
    at = url.rfind("@")
    userinfo = url[:at]
    rest     = url[at + 1:]   # host:port/dbname?query

    # Strip query string from the host portion only
    sslmode = "require"
    if "?" in rest:
        rest, query = rest.split("?", 1)
        for part in query.split("&"):
            if part.startswith("sslmode="):
                sslmode = part.split("=", 1)[1]

    # user:password — split at first colon in userinfo
    colon    = userinfo.find(":")
    user     = userinfo[:colon] if colon != -1 else userinfo
    password = userinfo[colon + 1:] if colon != -1 else ""

    # host:port/dbname
    slash     = rest.find("/")
    host_port = rest[:slash] if slash != -1 else rest
    dbname    = rest[slash + 1:] if slash != -1 else "postgres"
    rcolon    = host_port.rfind(":")
    host      = host_port[:rcolon] if rcolon != -1 else host_port
    port      = int(host_port[rcolon + 1:]) if rcolon != -1 else 5432

    return psycopg2.connect(
        host=host, port=port, user=user,
        password=password, dbname=dbname, sslmode=sslmode,
    )


class _Conn:
    """
    Thin sqlite3-compatible wrapper around a psycopg2 connection.

    Translates sqlite3 call patterns so every CRUD function below
    works without modification:
      - positional ?  → %s
      - named :param  → %(param)s
      - fetchone/fetchall return dict-like RealDictRow objects
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        if params is None:
            params = ()
        if isinstance(params, dict):
            sql = re.sub(r":(\w+)", r"%(\1)s", sql)
        else:
            sql = sql.replace("?", "%s")
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return cur

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        self._conn.close()
        return False


def get_connection() -> _Conn:
    conn = _connect()
    return _Conn(conn)


def migrate_add_columns():
    """Idempotent migrations for columns added after initial schema creation."""
    migrations = [
        ("recommendations", "horizon",               "TEXT DEFAULT 'medium'"),
        ("recommendations", "play_type",             "TEXT DEFAULT 'core'"),
        ("user_profile",    "active_strategy_file",  "TEXT DEFAULT NULL"),
    ]
    with get_connection() as conn:
        for table, col, typedef in migrations:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typedef}")
            except Exception:
                pass  # already exists


def init_db():
    """Create all tables if they don't exist, then run column migrations."""
    # Execute each DDL statement individually — psycopg2 doesn't support
    # multi-statement strings, and DDL order matters for FK constraints.
    stmts = [
        # ── USER PROFILE ──────────────────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS user_profile (
            id                   INTEGER PRIMARY KEY DEFAULT 1,
            name                 TEXT    DEFAULT 'Sayan',
            investment_goal      TEXT    DEFAULT 'Maximize profit — beat SPY and QQQ',
            risk_tolerance       TEXT    DEFAULT 'medium-high',
            time_horizon         TEXT    DEFAULT 'flexible — short or long depending on opportunity',
            strategy             TEXT    DEFAULT 'Beat S&P 500 and NASDAQ 100. Long or short term — whatever maximizes return.',
            preferred_sectors    TEXT    DEFAULT '["AI Infrastructure","Energy","Quantum","Space"]',
            avoided_sectors      TEXT    DEFAULT '[]',
            target_return        REAL    DEFAULT 300.0,
            notes                TEXT,
            active_strategy_file TEXT    DEFAULT NULL,
            updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        "INSERT INTO user_profile (id) VALUES (1) ON CONFLICT (id) DO NOTHING",

        # ── PORTFOLIO (current holdings) ──────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS portfolio (
            id              SERIAL PRIMARY KEY,
            ticker          TEXT    NOT NULL UNIQUE,
            company_name    TEXT,
            shares          REAL    NOT NULL DEFAULT 0,
            avg_cost_basis  REAL,
            current_price   REAL,
            sector          TEXT,
            asset_type      TEXT    DEFAULT 'stock',
            added_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes           TEXT
        )""",

        # ── PORTFOLIO HISTORY (snapshots) ─────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS portfolio_history (
            id                SERIAL PRIMARY KEY,
            snapshot_date     DATE    NOT NULL,
            ticker            TEXT    NOT NULL,
            shares            REAL,
            avg_cost_basis    REAL,
            price_at_snapshot REAL,
            total_value       REAL,
            source            TEXT    DEFAULT 'manual'
        )""",

        # ── WATCHLIST (individual stocks) ─────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS watchlist (
            id              SERIAL PRIMARY KEY,
            ticker          TEXT    NOT NULL UNIQUE,
            company_name    TEXT,
            sector          TEXT,
            why_watching    TEXT,
            added_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active       INTEGER DEFAULT 1
        )""",

        # ── SECTOR WATCHLIST ──────────────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS sector_watchlist (
            id              SERIAL PRIMARY KEY,
            sector_name     TEXT    NOT NULL UNIQUE,
            why_watching    TEXT,
            key_stocks      TEXT    DEFAULT '[]',
            key_events      TEXT    DEFAULT '[]',
            last_analyzed   TIMESTAMP,
            is_active       INTEGER DEFAULT 1
        )""",

        # ── BROKER ACCOUNTS ───────────────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS broker_accounts (
            id              SERIAL PRIMARY KEY,
            broker_name     TEXT    NOT NULL,
            account_type    TEXT    DEFAULT 'taxable',
            cash_balance    REAL    DEFAULT 0,
            total_value     REAL    DEFAULT 0,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes           TEXT
        )""",

        # ── ANALYSIS RUNS ─────────────────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS analysis_runs (
            id               SERIAL PRIMARY KEY,
            run_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            trigger          TEXT    DEFAULT 'manual',
            market_summary   TEXT,
            macro_summary    TEXT,
            geo_summary      TEXT,
            sentiment_summary TEXT,
            status           TEXT    DEFAULT 'pending',
            error_message    TEXT,
            duration_secs    REAL
        )""",

        # ── RECOMMENDATIONS ───────────────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS recommendations (
            id                      SERIAL PRIMARY KEY,
            run_id                  INTEGER NOT NULL REFERENCES analysis_runs(id),
            ticker                  TEXT    NOT NULL,
            company_name            TEXT,
            action                  TEXT    NOT NULL CHECK(action IN ('strong_buy','buy','hold','sell','strong_sell','new_pick')),
            confidence              INTEGER CHECK(confidence BETWEEN 1 AND 10),
            current_price           REAL,
            buy_range_low           REAL,
            buy_range_high          REAL,
            sell_target_low         REAL,
            sell_target_high        REAL,
            stop_loss               REAL,
            suggested_shares        REAL,
            suggested_pct_portfolio REAL,
            growth_potential_pct    REAL,
            growth_timeline         TEXT,
            thesis                  TEXT,
            risks                   TEXT,
            catalysts               TEXT,
            technical_score         INTEGER,
            sentiment_score         INTEGER,
            macro_score             INTEGER,
            is_new_pick             INTEGER DEFAULT 0,
            horizon                 TEXT    DEFAULT 'medium',
            play_type               TEXT    DEFAULT 'core',
            created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",

        # ── RECOMMENDATION FEEDBACK ───────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS rec_feedback (
            id              SERIAL PRIMARY KEY,
            rec_id          INTEGER NOT NULL REFERENCES recommendations(id),
            was_accurate    INTEGER,
            user_comment    TEXT,
            judged_at       TIMESTAMP
        )""",

        # ── ACTION LOG (user trade journal) ───────────────────────────────────
        # Created after recommendations so the FK ref resolves.
        # total_value is STORED generated column (PostgreSQL 12+).
        """CREATE TABLE IF NOT EXISTS action_log (
            id               SERIAL PRIMARY KEY,
            action_date      DATE    NOT NULL DEFAULT CURRENT_DATE,
            ticker           TEXT    NOT NULL,
            action           TEXT    NOT NULL CHECK(action IN ('buy','sell','hold_noted','watchlist_add','watchlist_remove')),
            shares           REAL,
            price_per_share  REAL,
            total_value      REAL    GENERATED ALWAYS AS (shares * price_per_share) STORED,
            was_system_suggested INTEGER DEFAULT 0,
            rec_id           INTEGER REFERENCES recommendations(id),
            my_reasoning     TEXT,
            outcome_notes    TEXT,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",

        # ── RESEARCH RESULTS (cached on-demand analysis) ──────────────────────
        """CREATE TABLE IF NOT EXISTS research_results (
            id              SERIAL PRIMARY KEY,
            subject         TEXT    NOT NULL,
            subject_type    TEXT    DEFAULT 'stock',
            research_json   TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",

        # ── DEFAULT WATCHLIST ─────────────────────────────────────────────────
        """INSERT INTO watchlist (ticker, company_name, sector) VALUES
            ('NVDA',  'NVIDIA Corporation',     'AI Infrastructure'),
            ('AMD',   'Advanced Micro Devices', 'AI Infrastructure'),
            ('INTC',  'Intel Corporation',      'AI Infrastructure'),
            ('MU',    'Micron Technology',      'AI Infrastructure'),
            ('MRVL',  'Marvell Technology',     'AI Infrastructure'),
            ('TSM',   'Taiwan Semiconductor',   'AI Infrastructure'),
            ('AVGO',  'Broadcom Inc.',          'AI Infrastructure'),
            ('IONQ',  'IonQ Inc.',              'Quantum Computing'),
            ('RGTI',  'Rigetti Computing',      'Quantum Computing'),
            ('QUBT',  'Quantum Computing Inc.', 'Quantum Computing'),
            ('RKLB',  'Rocket Lab USA',         'Space'),
            ('ASTS',  'AST SpaceMobile',        'Space'),
            ('NEE',   'NextEra Energy',         'Clean Energy'),
            ('FSLR',  'First Solar',            'Clean Energy'),
            ('CEG',   'Constellation Energy',   'Nuclear Energy'),
            ('VST',   'Vistra Corp',            'Energy')
            ON CONFLICT (ticker) DO NOTHING""",

        # ── DEFAULT SECTOR WATCHLIST ──────────────────────────────────────────
        """INSERT INTO sector_watchlist (sector_name, why_watching, key_stocks) VALUES
            ('AI Infrastructure', 'Core holding sector — data center buildout, GPU/chip demand',          '["NVDA","AMD","AVGO","MU","MRVL","TSM"]'),
            ('Quantum Computing',  'High-risk high-reward — early stage, watching for commercialization', '["IONQ","RGTI","QUBT"]'),
            ('Space',              'SpaceX IPO catalyst, satellite internet, defense contracts',           '["RKLB","ASTS"]'),
            ('Clean Energy',       'IRA tailwinds, nuclear renaissance, grid demand from AI data centers','["CEG","VST","NEE","FSLR"]')
            ON CONFLICT (sector_name) DO NOTHING""",
    ]

    conn = _connect()
    try:
        with conn.cursor() as cur:
            for stmt in stmts:
                cur.execute(stmt)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    migrate_add_columns()
    print("✅ Database initialized at Supabase")


# ── PORTFOLIO OPERATIONS ─────────────────────────────────────────────────────

def upsert_holding(ticker, shares, avg_cost, company_name=None, sector=None, notes=None):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO portfolio (ticker, company_name, shares, avg_cost_basis, sector, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(ticker) DO UPDATE SET
                shares         = excluded.shares,
                avg_cost_basis = excluded.avg_cost_basis,
                company_name   = COALESCE(excluded.company_name, company_name),
                sector         = COALESCE(excluded.sector, sector),
                notes          = COALESCE(excluded.notes, notes),
                updated_at     = CURRENT_TIMESTAMP
        """, (ticker.upper(), company_name, shares, avg_cost, sector, notes))


def get_portfolio():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT p.*,
                   ROUND(CAST((p.current_price - p.avg_cost_basis) / NULLIF(p.avg_cost_basis, 0) * 100 AS numeric), 2) as pnl_pct,
                   ROUND(CAST((p.current_price - p.avg_cost_basis) * p.shares AS numeric), 2) as pnl_dollars
            FROM portfolio p
            ORDER BY p.shares * COALESCE(p.current_price, p.avg_cost_basis) DESC
        """).fetchall()
        return [dict(r) for r in rows]


def refresh_portfolio_prices():
    """Pull latest prices from yfinance for all holdings. Called by UI refresh button."""
    import yfinance as yf
    holdings = get_portfolio()
    if not holdings:
        return 0
    tickers = [h["ticker"] for h in holdings]
    updated = 0
    with get_connection() as conn:
        for ticker in tickers:
            try:
                info = yf.Ticker(ticker).fast_info
                price = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
                if price:
                    conn.execute(
                        "UPDATE portfolio SET current_price=?, updated_at=CURRENT_TIMESTAMP WHERE ticker=?",
                        (float(price), ticker)
                    )
                    updated += 1
            except Exception:
                pass
    return updated


def snapshot_portfolio(source="manual"):
    holdings = get_portfolio()
    today = datetime.now().date().isoformat()
    with get_connection() as conn:
        for h in holdings:
            price = h.get("current_price") or h.get("avg_cost_basis") or 0
            conn.execute("""
                INSERT INTO portfolio_history (snapshot_date, ticker, shares, avg_cost_basis, price_at_snapshot, total_value, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (today, h["ticker"], h["shares"], h["avg_cost_basis"], price, h["shares"] * price, source))


# ── BROKER ACCOUNT OPERATIONS ─────────────────────────────────────────────────

def get_broker_accounts():
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM broker_accounts ORDER BY broker_name").fetchall()
        return [dict(r) for r in rows]


def upsert_broker_account(broker_name, account_type="taxable", cash_balance=0, total_value=0, notes=None, account_id=None):
    with get_connection() as conn:
        if account_id:
            conn.execute("""
                UPDATE broker_accounts SET broker_name=?, account_type=?, cash_balance=?, total_value=?,
                notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?
            """, (broker_name, account_type, cash_balance, total_value, notes, account_id))
        else:
            conn.execute("""
                INSERT INTO broker_accounts (broker_name, account_type, cash_balance, total_value, notes)
                VALUES (?, ?, ?, ?, ?)
            """, (broker_name, account_type, cash_balance, total_value, notes))


def delete_broker_account(account_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM broker_accounts WHERE id=?", (account_id,))


# ── SECTOR WATCHLIST OPERATIONS ──────────────────────────────────────────────

def get_sector_watchlist():
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM sector_watchlist WHERE is_active=1 ORDER BY sector_name").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["key_stocks"] = json.loads(d["key_stocks"] or "[]")
            d["key_events"] = json.loads(d["key_events"] or "[]")
            result.append(d)
        return result


def upsert_sector(sector_name, why_watching=None, key_stocks=None, key_events=None):
    stocks_json = json.dumps(key_stocks or [])
    events_json = json.dumps(key_events or [])
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO sector_watchlist (sector_name, why_watching, key_stocks, key_events)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(sector_name) DO UPDATE SET
                why_watching = COALESCE(excluded.why_watching, why_watching),
                key_stocks   = excluded.key_stocks,
                key_events   = excluded.key_events
        """, (sector_name, why_watching, stocks_json, events_json))


# ── ACTION LOG OPERATIONS ────────────────────────────────────────────────────

def log_action(ticker, action, shares, price, was_suggested=False, rec_id=None, reasoning=""):
    ticker = ticker.upper()
    with get_connection() as conn:
        # total_value is a GENERATED column — omit from INSERT
        conn.execute("""
            INSERT INTO action_log (ticker, action, shares, price_per_share, was_system_suggested, rec_id, my_reasoning)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (ticker, action, shares, price, int(was_suggested), rec_id, reasoning))

        if action == "buy":
            row = conn.execute("SELECT shares, avg_cost_basis FROM portfolio WHERE ticker=?", (ticker,)).fetchone()
            if row:
                old_shares = row["shares"]
                old_cost   = row["avg_cost_basis"] or price
                new_shares = old_shares + shares
                new_cost   = ((old_shares * old_cost) + (shares * price)) / new_shares
                conn.execute("""
                    UPDATE portfolio SET shares=?, avg_cost_basis=?, updated_at=CURRENT_TIMESTAMP WHERE ticker=?
                """, (new_shares, new_cost, ticker))
            else:
                conn.execute("""
                    INSERT INTO portfolio (ticker, shares, avg_cost_basis) VALUES (?, ?, ?)
                """, (ticker, shares, price))

        elif action == "sell":
            row = conn.execute("SELECT shares FROM portfolio WHERE ticker=?", (ticker,)).fetchone()
            if row:
                new_shares = max(0, row["shares"] - shares)
                if new_shares == 0:
                    conn.execute("DELETE FROM portfolio WHERE ticker=?", (ticker,))
                else:
                    conn.execute("""
                        UPDATE portfolio SET shares=?, updated_at=CURRENT_TIMESTAMP WHERE ticker=?
                    """, (new_shares, ticker))


def get_action_log(limit=50):
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT a.*, r.action as rec_action, r.thesis as rec_thesis
            FROM action_log a
            LEFT JOIN recommendations r ON a.rec_id = r.id
            ORDER BY a.created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


# ── RECOMMENDATIONS OPERATIONS ───────────────────────────────────────────────

def save_recommendations(run_id, recs: list[dict]):
    with get_connection() as conn:
        for r in recs:
            conn.execute("""
                INSERT INTO recommendations (
                    run_id, ticker, company_name, action, confidence,
                    current_price, buy_range_low, buy_range_high,
                    sell_target_low, sell_target_high, stop_loss,
                    suggested_shares, suggested_pct_portfolio,
                    growth_potential_pct, growth_timeline,
                    thesis, risks, catalysts,
                    technical_score, sentiment_score, macro_score,
                    is_new_pick, horizon, play_type
                ) VALUES (
                    :run_id, :ticker, :company_name, :action, :confidence,
                    :current_price, :buy_range_low, :buy_range_high,
                    :sell_target_low, :sell_target_high, :stop_loss,
                    :suggested_shares, :suggested_pct_portfolio,
                    :growth_potential_pct, :growth_timeline,
                    :thesis, :risks, :catalysts,
                    :technical_score, :sentiment_score, :macro_score,
                    :is_new_pick, :horizon, :play_type
                )
            """, {
                **r,
                "run_id":    run_id,
                "horizon":   r.get("horizon", "medium"),
                "play_type": r.get("play_type", "core"),
            })


def get_latest_recommendations():
    """Return the most recent recommendation per ticker across all completed runs."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT r.*, ar.run_at, ar.market_summary
            FROM recommendations r
            JOIN analysis_runs ar ON r.run_id = ar.id
            WHERE ar.status = 'completed'
              AND r.id IN (
                  SELECT MAX(r2.id)
                  FROM recommendations r2
                  JOIN analysis_runs ar2 ON r2.run_id = ar2.id
                  WHERE ar2.status = 'completed'
                  GROUP BY r2.ticker
              )
            ORDER BY r.confidence DESC, r.action
        """).fetchall()
        return [dict(r) for r in rows]


def get_sector_top_picks(n=3):
    """Return top-N picks per sector using the most recent recommendation per ticker."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT r.ticker, r.company_name, r.action, r.confidence,
                   r.current_price, r.growth_potential_pct, r.horizon, r.play_type,
                   COALESCE(p.sector, w.sector, 'Other') as sector
            FROM recommendations r
            LEFT JOIN portfolio p ON r.ticker = p.ticker
            LEFT JOIN watchlist w ON r.ticker = w.ticker
            WHERE r.action IN ('strong_buy', 'buy', 'new_pick')
              AND r.id IN (
                  SELECT MAX(r2.id)
                  FROM recommendations r2
                  JOIN analysis_runs ar2 ON r2.run_id = ar2.id
                  WHERE ar2.status = 'completed'
                  GROUP BY r2.ticker
              )
            ORDER BY r.confidence DESC, r.growth_potential_pct DESC
        """).fetchall()

        by_sector = {}
        for row in rows:
            d = dict(row)
            sector = d["sector"] or "Other"
            if sector not in by_sector:
                by_sector[sector] = []
            if len(by_sector[sector]) < n:
                by_sector[sector].append(d)
        return by_sector


# ── RESEARCH RESULTS ──────────────────────────────────────────────────────────

def get_research_result(subject: str, max_age_hours: int = 6):
    """Return cached research if it exists and is fresh enough."""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT * FROM research_results
            WHERE LOWER(subject) = LOWER(?)
            ORDER BY created_at DESC LIMIT 1
        """, (subject,)).fetchone()
        if not row:
            return None
        created = row["created_at"]
        if not isinstance(created, datetime):
            created = datetime.fromisoformat(str(created))
        age_hours = (datetime.now() - created).total_seconds() / 3600
        if age_hours > max_age_hours:
            return None
        d = dict(row)
        d["research"] = json.loads(d["research_json"] or "{}")
        return d


def save_research_result(subject: str, subject_type: str, research: dict):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO research_results (subject, subject_type, research_json)
            VALUES (?, ?, ?)
        """, (subject.upper(), subject_type, json.dumps(research)))


# ── USER PROFILE ──────────────────────────────────────────────────────────────

def get_user_profile():
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM user_profile WHERE id=1").fetchone()
        return dict(row) if row else {}


def update_user_profile(**kwargs):
    if not kwargs:
        return
    fields = ", ".join(f"{k}=%s" for k in kwargs)
    values = list(kwargs.values())
    with get_connection() as conn:
        conn.execute(
            f"UPDATE user_profile SET {fields}, updated_at=CURRENT_TIMESTAMP WHERE id=1",
            values
        )


# ── WATCHLIST ─────────────────────────────────────────────────────────────────

def get_watchlist():
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM watchlist WHERE is_active=1 ORDER BY sector, ticker").fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
