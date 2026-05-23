"""
database.py — SQLite schema and all DB operations for AI Trader.

Tables:
  user_profile       — investment goals, risk tolerance, targets
  portfolio          — current holdings (updated via action_log or PDF import)
  portfolio_history  — snapshots of portfolio over time
  action_log         — user-recorded trades (manual journal)
  watchlist          — stocks to monitor beyond current holdings
  analysis_runs      — record of every analysis execution
  recommendations    — per-stock recommendations from each run
  rec_feedback       — user feedback on recommendations (was it right?)
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "trader.db"


def get_connection():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    with get_connection() as conn:
        conn.executescript("""
        -- ── USER PROFILE ─────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS user_profile (
            id               INTEGER PRIMARY KEY DEFAULT 1,
            name             TEXT    DEFAULT 'Sayan',
            investment_goal  TEXT    DEFAULT 'Exponential growth over 2-3 years',
            risk_tolerance   TEXT    DEFAULT 'medium-high',
            time_horizon     TEXT    DEFAULT '2-3 years',
            strategy         TEXT    DEFAULT 'No day trading. Long-term positions.',
            preferred_sectors TEXT   DEFAULT '["AI infrastructure","Energy","Quantum","Space"]',
            avoided_sectors  TEXT    DEFAULT '[]',
            target_return    REAL    DEFAULT 300.0,   -- % target (300 = 3x)
            notes            TEXT,
            updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        INSERT OR IGNORE INTO user_profile (id) VALUES (1);

        -- ── PORTFOLIO (current holdings) ──────────────────────────────────────
        CREATE TABLE IF NOT EXISTS portfolio (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT    NOT NULL UNIQUE,
            company_name    TEXT,
            shares          REAL    NOT NULL DEFAULT 0,
            avg_cost_basis  REAL,           -- average cost per share
            current_price   REAL,           -- updated on each analysis run
            sector          TEXT,
            asset_type      TEXT    DEFAULT 'stock',  -- stock, etf, crypto
            added_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes           TEXT
        );

        -- ── PORTFOLIO HISTORY (snapshots) ─────────────────────────────────────
        CREATE TABLE IF NOT EXISTS portfolio_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date   DATE    NOT NULL,
            ticker          TEXT    NOT NULL,
            shares          REAL,
            avg_cost_basis  REAL,
            price_at_snapshot REAL,
            total_value     REAL,
            source          TEXT    DEFAULT 'manual'  -- 'pdf_import', 'manual', 'action_log'
        );

        -- ── ACTION LOG (user trade journal) ───────────────────────────────────
        CREATE TABLE IF NOT EXISTS action_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            action_date     DATE    NOT NULL DEFAULT (date('now')),
            ticker          TEXT    NOT NULL,
            action          TEXT    NOT NULL CHECK(action IN ('buy','sell','hold_noted','watchlist_add','watchlist_remove')),
            shares          REAL,
            price_per_share REAL,
            total_value     REAL    GENERATED ALWAYS AS (shares * price_per_share) VIRTUAL,
            was_system_suggested INTEGER DEFAULT 0,  -- 1 if following a recommendation
            rec_id          INTEGER REFERENCES recommendations(id),
            my_reasoning    TEXT,   -- user's own note about why
            outcome_notes   TEXT,   -- user can add notes later about how it went
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- ── WATCHLIST ─────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS watchlist (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT    NOT NULL UNIQUE,
            company_name    TEXT,
            sector          TEXT,
            why_watching    TEXT,
            added_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active       INTEGER DEFAULT 1
        );

        -- ── ANALYSIS RUNS ─────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS analysis_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            trigger         TEXT    DEFAULT 'manual',  -- 'market_open', 'market_close', 'manual'
            market_summary  TEXT,   -- JSON: overall market sentiment
            macro_summary   TEXT,   -- JSON: macro indicators snapshot
            geo_summary     TEXT,   -- JSON: geopolitical risk signals
            sentiment_summary TEXT, -- JSON: news/reddit sentiment scores
            status          TEXT    DEFAULT 'pending',  -- pending, running, completed, failed
            error_message   TEXT,
            duration_secs   REAL
        );

        -- ── RECOMMENDATIONS (per-stock, per-run) ──────────────────────────────
        CREATE TABLE IF NOT EXISTS recommendations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          INTEGER NOT NULL REFERENCES analysis_runs(id),
            ticker          TEXT    NOT NULL,
            company_name    TEXT,
            action          TEXT    NOT NULL CHECK(action IN ('strong_buy','buy','hold','sell','strong_sell','new_pick')),
            confidence      INTEGER CHECK(confidence BETWEEN 1 AND 10),
            current_price   REAL,
            buy_range_low   REAL,
            buy_range_high  REAL,
            sell_target_low REAL,
            sell_target_high REAL,
            stop_loss       REAL,
            suggested_shares REAL,  -- suggested position size
            suggested_pct_portfolio REAL,  -- % of portfolio
            growth_potential_pct REAL,     -- % upside
            growth_timeline TEXT,           -- e.g. "12-18 months"
            thesis          TEXT,           -- Claude's investment thesis
            risks           TEXT,           -- JSON array of risk factors
            catalysts       TEXT,           -- JSON array of upcoming catalysts
            technical_score INTEGER,        -- 1-10
            sentiment_score INTEGER,        -- 1-10
            macro_score     INTEGER,        -- 1-10
            is_new_pick     INTEGER DEFAULT 0,  -- 1 if not in current portfolio
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- ── RECOMMENDATION FEEDBACK ───────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS rec_feedback (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            rec_id          INTEGER NOT NULL REFERENCES recommendations(id),
            was_accurate    INTEGER,  -- 1 yes, 0 no, NULL = not yet judged
            user_comment    TEXT,
            judged_at       TIMESTAMP
        );

        -- ── DEFAULT WATCHLIST (your core stocks) ──────────────────────────────
        INSERT OR IGNORE INTO watchlist (ticker, company_name, sector) VALUES
            ('NVDA',  'NVIDIA Corporation',          'AI Infrastructure'),
            ('AMD',   'Advanced Micro Devices',      'AI Infrastructure'),
            ('INTC',  'Intel Corporation',           'AI Infrastructure'),
            ('MU',    'Micron Technology',           'AI Infrastructure'),
            ('MRVL',  'Marvell Technology',          'AI Infrastructure'),
            ('TSMC',  'Taiwan Semiconductor',        'AI Infrastructure'),
            ('AVGO',  'Broadcom Inc.',               'AI Infrastructure'),
            ('IONQ',  'IonQ Inc.',                   'Quantum Computing'),
            ('RGTI',  'Rigetti Computing',           'Quantum Computing'),
            ('QUBT',  'Quantum Computing Inc.',      'Quantum Computing'),
            ('RKLB',  'Rocket Lab USA',              'Space'),
            ('SPCE',  'Virgin Galactic',             'Space'),
            ('ASTS',  'AST SpaceMobile',             'Space'),
            ('NEE',   'NextEra Energy',              'Clean Energy'),
            ('FSLR',  'First Solar',                 'Clean Energy'),
            ('CEG',   'Constellation Energy',        'Nuclear Energy'),
            ('VST',   'Vistra Corp',                 'Energy');
        """)
    print(f"✅ Database initialized at {DB_PATH}")


# ── PORTFOLIO OPERATIONS ────────────────────────────────────────────────────

def upsert_holding(ticker, shares, avg_cost, company_name=None, sector=None, notes=None):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO portfolio (ticker, company_name, shares, avg_cost_basis, sector, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(ticker) DO UPDATE SET
                shares        = excluded.shares,
                avg_cost_basis = excluded.avg_cost_basis,
                company_name  = COALESCE(excluded.company_name, company_name),
                sector        = COALESCE(excluded.sector, sector),
                notes         = COALESCE(excluded.notes, notes),
                updated_at    = CURRENT_TIMESTAMP
        """, (ticker.upper(), company_name, shares, avg_cost, sector, notes))


def get_portfolio():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT p.*,
                   ROUND((p.current_price - p.avg_cost_basis) / p.avg_cost_basis * 100, 2) as pnl_pct,
                   ROUND((p.current_price - p.avg_cost_basis) * p.shares, 2) as pnl_dollars
            FROM portfolio p
            ORDER BY p.shares * COALESCE(p.current_price, p.avg_cost_basis) DESC
        """).fetchall()
        return [dict(r) for r in rows]


def snapshot_portfolio(source="manual"):
    """Save a point-in-time snapshot of current holdings."""
    holdings = get_portfolio()
    today = datetime.now().date().isoformat()
    with get_connection() as conn:
        for h in holdings:
            price = h.get("current_price") or h.get("avg_cost_basis") or 0
            conn.execute("""
                INSERT INTO portfolio_history (snapshot_date, ticker, shares, avg_cost_basis, price_at_snapshot, total_value, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (today, h["ticker"], h["shares"], h["avg_cost_basis"], price, h["shares"] * price, source))


# ── ACTION LOG OPERATIONS ───────────────────────────────────────────────────

def log_action(ticker, action, shares, price, was_suggested=False, rec_id=None, reasoning=""):
    ticker = ticker.upper()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO action_log (ticker, action, shares, price_per_share, was_system_suggested, rec_id, my_reasoning)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (ticker, action, shares, price, int(was_suggested), rec_id, reasoning))

        # Keep portfolio in sync
        if action == "buy":
            row = conn.execute("SELECT shares, avg_cost_basis FROM portfolio WHERE ticker=?", (ticker,)).fetchone()
            if row:
                old_shares = row["shares"]
                old_cost   = row["avg_cost_basis"] or price
                new_shares = old_shares + shares
                new_cost   = ((old_shares * old_cost) + (shares * price)) / new_shares
                conn.execute("""
                    UPDATE portfolio SET shares=?, avg_cost_basis=?, updated_at=CURRENT_TIMESTAMP
                    WHERE ticker=?
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


# ── RECOMMENDATIONS OPERATIONS ──────────────────────────────────────────────

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
                    technical_score, sentiment_score, macro_score, is_new_pick
                ) VALUES (
                    :run_id, :ticker, :company_name, :action, :confidence,
                    :current_price, :buy_range_low, :buy_range_high,
                    :sell_target_low, :sell_target_high, :stop_loss,
                    :suggested_shares, :suggested_pct_portfolio,
                    :growth_potential_pct, :growth_timeline,
                    :thesis, :risks, :catalysts,
                    :technical_score, :sentiment_score, :macro_score, :is_new_pick
                )
            """, {**r, "run_id": run_id})


def get_latest_recommendations():
    with get_connection() as conn:
        run = conn.execute("""
            SELECT id FROM analysis_runs WHERE status='completed' ORDER BY run_at DESC LIMIT 1
        """).fetchone()
        if not run:
            return []
        rows = conn.execute("""
            SELECT r.*, ar.run_at, ar.market_summary
            FROM recommendations r
            JOIN analysis_runs ar ON r.run_id = ar.id
            WHERE r.run_id = ?
            ORDER BY r.confidence DESC, r.action
        """, (run["id"],)).fetchall()
        return [dict(r) for r in rows]


# ── USER PROFILE ─────────────────────────────────────────────────────────────

def get_user_profile():
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM user_profile WHERE id=1").fetchone()
        return dict(row) if row else {}


def update_user_profile(**kwargs):
    if not kwargs:
        return
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values())
    with get_connection() as conn:
        conn.execute(f"UPDATE user_profile SET {fields}, updated_at=CURRENT_TIMESTAMP WHERE id=1", values)


def get_watchlist():
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM watchlist WHERE is_active=1 ORDER BY sector, ticker").fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
