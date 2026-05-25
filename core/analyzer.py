"""
analyzer.py — Main analysis orchestrator.

Flow (multi-agent pipeline):
  1. Load portfolio + watchlist + user profile from DB
  2. Fetch market overview, macro, geo, per-stock data
  3. Run 5-agent pipeline:
       Agent 1 (Haiku): Momentum signals
       Agent 2 (Haiku): Macro/Geo regime
       Agent 3 (Sonnet): Short-term scanner
       Agent 4 (Sonnet): Long-term analyst
       Agent 5 (Sonnet): Synthesis → final recommendations
  4. Save recommendations to DB + write latest_results.json

Run manually:  python core/analyzer.py [trigger] [--scope full|portfolio_only|watchlist_only|momentum_scan]
Triggered by:  GitHub Actions (cron: no scope arg → defaults to 'full')
"""

import os
import json
import time
import traceback
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent / ".env")

from core.database import (
    get_connection, get_portfolio, get_watchlist, get_user_profile,
    save_recommendations, init_db,
)
from core.collectors.market_data   import get_full_snapshot, get_market_overview
from core.collectors.news_sentiment import get_full_sentiment, get_geopolitical_risk
from core.collectors.macro_data    import get_macro_summary


# ── CONFIGURATION ─────────────────────────────────────────────────────────────

RESULTS_PATH = Path(__file__).parent.parent / "data" / "latest_results.json"

# Max stocks per run (free API rate limits)
MAX_PORTFOLIO_STOCKS = 30
MAX_NEW_PICKS        = 8   # new stock candidates to evaluate beyond portfolio
CLAUDE_BATCH_SIZE    = 10  # stocks per Claude API call (keeps output under max_tokens)


# ── DATA COLLECTION ───────────────────────────────────────────────────────────

def collect_all_data(trigger: str = "manual") -> dict:
    """
    Gather all data needed for one analysis run.
    Returns a structured dict ready to be serialized into the Claude prompt.
    """
    print(f"\n{'='*60}")
    print(f"  AI Trader — Analysis Run  [{trigger}]  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*60}\n")

    profile  = get_user_profile()
    holdings = get_portfolio()
    watchlist = get_watchlist()

    # Tickers to analyze:
    # - Everything in portfolio (always)
    # - Top watchlist items not already in portfolio, up to MAX_NEW_PICKS
    portfolio_tickers = {h["ticker"] for h in holdings}
    watch_tickers     = [w["ticker"] for w in watchlist
                         if w["ticker"] not in portfolio_tickers][:MAX_NEW_PICKS]
    all_tickers       = list(portfolio_tickers) + watch_tickers

    print(f"Portfolio stocks : {sorted(portfolio_tickers)}")
    print(f"New pick candidates: {watch_tickers}\n")

    # ── Market overview ───────────────────────────────────────────────────────
    print("Fetching market overview...")
    market = get_market_overview()

    # ── Macro ──────────────────────────────────────────────────────────────────
    print("Fetching macro indicators (FRED, World Bank, BLS)...")
    macro = get_macro_summary()

    # ── Geopolitical risk ─────────────────────────────────────────────────────
    print("Fetching geopolitical signals (RSS)...")
    geo = get_geopolitical_risk()

    # ── Per-stock data ────────────────────────────────────────────────────────
    print(f"\nFetching data for {len(all_tickers)} stocks...")
    stock_data = {}
    for i, ticker in enumerate(all_tickers):
        print(f"  [{i+1}/{len(all_tickers)}] {ticker}")
        try:
            snapshot   = get_full_snapshot(ticker)
            sentiment  = get_full_sentiment(ticker, snapshot.get("company_name", ""))
            stock_data[ticker] = {**snapshot, "sentiment": sentiment}
        except Exception as e:
            print(f"    Error: {e}")
            stock_data[ticker] = {"ticker": ticker, "error": str(e)}
        time.sleep(0.5)  # gentle rate limiting

    return {
        "profile":          profile,
        "holdings":         holdings,
        "watchlist":        watchlist,
        "portfolio_tickers": list(portfolio_tickers),
        "new_pick_tickers": watch_tickers,
        "market":           market,
        "macro":            macro,
        "geo":              geo,
        "stocks":           stock_data,
        "trigger":          trigger,
        "run_at":           datetime.now().isoformat(),
    }


# ── LEGACY: single-agent prompt builder (kept for reference, no longer called) ──

def build_prompt(data: dict, tickers_subset: set | None = None) -> str:
    """
    Build the structured analysis prompt for Claude.
    tickers_subset: if provided, only include those tickers in the stock section.
    """
    profile  = data["profile"]
    market   = data["market"]
    macro    = data["macro"]
    geo      = data["geo"]
    stocks   = data["stocks"]
    holdings = data["holdings"]

    # Build portfolio context (cost basis, current value, P&L)
    holding_map = {h["ticker"]: h for h in holdings}

    def fmt(v, prefix="", suffix="", decimals=2):
        if v is None: return "N/A"
        return f"{prefix}{v:.{decimals}f}{suffix}"

    # ── SYSTEM CONTEXT ────────────────────────────────────────────────────────
    prompt = f"""You are an expert financial analyst and portfolio advisor.
Your client is {profile.get('name', 'Sayan')}, who has the following investment profile:

INVESTMENT PROFILE:
- Goal: {profile.get('investment_goal', 'Exponential growth over 2-3 years')}
- Time horizon: {profile.get('time_horizon', '2-3 years')}
- Risk tolerance: {profile.get('risk_tolerance', 'medium-high')}
- Strategy: {profile.get('strategy', 'No day trading. Long-term positions.')}
- Target return: {profile.get('target_return', 300)}% (i.e., wants to roughly {int(profile.get('target_return', 300)/100 + 1)}x their money)
- Preferred sectors: {profile.get('preferred_sectors', '["AI Infrastructure","Energy","Quantum","Space"]')}
- Notes: {profile.get('notes') or 'None'}

"""

    # ── MARKET CONDITIONS ─────────────────────────────────────────────────────
    spy   = market.get("SPY", {})
    qqq   = market.get("QQQ", {})
    smh   = market.get("SMH", {})
    vix   = market.get("VIX", {})
    meta  = market.get("_meta", {})

    prompt += f"""CURRENT MARKET CONDITIONS:
- Overall sentiment: {meta.get('overall_sentiment', 'neutral').upper()}
- S&P 500 (SPY): {fmt(spy.get('current'), '$')} | Day: {fmt(spy.get('day_change_pct'), suffix='%')} | Week: {fmt(spy.get('week_change_pct'), suffix='%')}
- NASDAQ 100 (QQQ): {fmt(qqq.get('current'), '$')} | Day: {fmt(qqq.get('day_change_pct'), suffix='%')} | Week: {fmt(qqq.get('week_change_pct'), suffix='%')}
- Semiconductors (SMH): {fmt(smh.get('current'), '$')} | Week: {fmt(smh.get('week_change_pct'), suffix='%')}
- VIX (Fear Index): {fmt(vix.get('current'))} — {'ELEVATED FEAR' if (vix.get('current') or 0) > 25 else 'NORMAL'}

"""

    # ── MACRO ENVIRONMENT ─────────────────────────────────────────────────────
    yc = macro.get("yield_curve", {})
    prompt += f"""MACRO-ECONOMIC ENVIRONMENT (Score: {macro.get('macro_score', 5)}/10):
- Fed Funds Rate: {fmt(macro.get('fed_funds_rate'), suffix='%')} ({macro.get('rate_trend', 'stable')}, {macro.get('rate_environment', 'neutral')})
- Inflation (YoY): {fmt(macro.get('inflation_yoy'), suffix='%')} — {macro.get('inflation_status', 'unknown')}
- Unemployment: {fmt(macro.get('unemployment'), suffix='%')}
- US GDP Growth: {fmt(macro.get('gdp_growth_us'), suffix='%')}
- 10Y Treasury: {fmt(macro.get('treasury_10y'), suffix='%')} | 2Y: {fmt(macro.get('treasury_2y'), suffix='%')}
- Yield curve (10Y-2Y): {fmt(yc.get('spread'), suffix='%')} — {'⚠️ INVERTED (recession signal)' if yc.get('inverted') else 'normal'}
- Consumer sentiment: {fmt(macro.get('consumer_sentiment'))}
- Global context: Taiwan GDP {fmt(macro.get('taiwan_gdp'), suffix='%')} (semiconductor supply chain health)

"""

    # ── GEOPOLITICAL RISK ─────────────────────────────────────────────────────
    prompt += f"""GEOPOLITICAL RISK (Index: {geo.get('risk_index', 5)}/10 — {geo.get('risk_level', 'medium').upper()}):
Top headlines:
"""
    for h in geo.get("top_headlines", [])[:4]:
        prompt += f"  - {h}\n"
    prompt += "\n"

    # ── CURRENT PORTFOLIO ─────────────────────────────────────────────────────
    prompt += "CURRENT PORTFOLIO:\n"
    if holdings:
        for h in holdings:
            cost  = h.get("avg_cost_basis") or 0
            price = h.get("current_price") or cost
            pnl   = (price - cost) / cost * 100 if cost else 0
            shares = h.get("shares", 0)
            value  = price * shares
            prompt += (f"  {h['ticker']:6s} | {shares:>8.2f} shares | "
                       f"avg cost ${cost:.2f} | current ${price:.2f} | "
                       f"P&L {pnl:+.1f}% | value ${value:,.0f}\n")
    else:
        prompt += "  (No holdings yet — focus on new picks)\n"
    prompt += "\n"

    # ── PER-STOCK DATA ────────────────────────────────────────────────────────
    prompt += "DETAILED STOCK DATA:\n"
    prompt += "="*60 + "\n"

    for ticker, s in stocks.items():
        if tickers_subset is not None and ticker not in tickers_subset:
            continue
        if s.get("error"):
            prompt += f"\n{ticker}: DATA FETCH ERROR — {s['error']}\n"
            continue

        in_portfolio = ticker in holding_map
        h = holding_map.get(ticker, {})
        sent = s.get("sentiment", {})

        prompt += f"""
{'[IN PORTFOLIO]' if in_portfolio else '[WATCHLIST/NEW PICK]'} {ticker} — {s.get('company_name', '')}
Sector: {s.get('sector', 'N/A')} | Market cap: ${(s.get('market_cap') or 0)/1e9:.1f}B
Price: ${fmt(s.get('current_price'))} | Day: {fmt(s.get('change_pct_today'), suffix='%')}
52W High: ${fmt(s.get('high_52w'))} ({fmt(s.get('pct_from_52w_high'), suffix='%')} from high) | 52W Low: ${fmt(s.get('low_52w'))}
"""
        if in_portfolio and h.get("avg_cost_basis"):
            pnl = ((s.get("current_price") or 0) - h["avg_cost_basis"]) / h["avg_cost_basis"] * 100
            prompt += f"YOUR POSITION: {h.get('shares',0):.2f} shares @ ${h['avg_cost_basis']:.2f} avg cost | P&L: {pnl:+.1f}%\n"

        prompt += f"""Fundamentals: P/E={fmt(s.get('pe_ratio'))} | Fwd P/E={fmt(s.get('forward_pe'))} | PEG={fmt(s.get('peg_ratio'))} | Beta={fmt(s.get('beta'))}
  Revenue growth={fmt(s.get('revenue_growth'), suffix='%')} | Earnings growth={fmt(s.get('earnings_growth'), suffix='%')}
  Analyst target=${fmt(s.get('analyst_target'))} ({s.get('analyst_rec','N/A')}) | Short ratio={fmt(s.get('short_ratio'))}
Technicals (score {s.get('technical_score','?')}/10): RSI={fmt(s.get('rsi'))} | MACD hist={fmt(s.get('macd_histogram'), decimals=3)}
  SMA20=${fmt(s.get('sma20'))} | SMA50=${fmt(s.get('sma50'))} | SMA200=${fmt(s.get('sma200'))}
  BB: ${fmt(s.get('bb_lower'))}–${fmt(s.get('bb_upper'))} | Vol trend: {s.get('volume_trend','N/A')}
Sentiment (score {sent.get('sentiment_score','?')}/10): News={sent.get('news_score','?')} | Reddit={sent.get('reddit_score','?')} | Trends={sent.get('trends_score','?')}
  Trend direction: {sent.get('trend_direction','stable')} | Mentions: {sent.get('mention_count',0)}
  Headlines: {' | '.join(sent.get('top_headlines',[])[:2]) or 'None'}
Next earnings: {s.get('next_earnings_date','N/A')} | Insider sentiment: {fmt(s.get('insider_sentiment'), decimals=4)}
"""

    # ── INSTRUCTIONS ─────────────────────────────────────────────────────────
    tickers_in_batch = sorted(tickers_subset) if tickers_subset else sorted(stocks.keys())
    prompt += f"""
{'='*60}
ANALYSIS INSTRUCTIONS:

IMPORTANT: Generate recommendations ONLY for these {len(tickers_in_batch)} tickers: {tickers_in_batch}
Do NOT generate entries for any other tickers, even if they appear in the portfolio section above.

Tailor recommendations to {profile.get('name','Sayan')}'s profile:
- Focus on 2-3 year exponential growth, NOT short-term trading
- Prefer strong AI infrastructure leaders with secular tailwinds
- For energy/quantum/space: higher risk tolerance accepted, but thesis must be solid
- Weight heavily: analyst targets, revenue/earnings growth trajectory, macro alignment
- Flag any upcoming earnings as a near-term catalyst or risk
- For NEW PICKS: only recommend if conviction is high (confidence >= 7)

RESPOND WITH ONLY a valid JSON array of exactly {len(tickers_in_batch)} objects. No markdown, no explanation outside the JSON.
Each element must follow this exact schema:

{{
  "ticker": "NVDA",
  "company_name": "NVIDIA Corporation",
  "action": "strong_buy",
  "confidence": 9,
  "current_price": 875.50,
  "buy_range_low": 820.0,
  "buy_range_high": 900.0,
  "sell_target_low": 1200.0,
  "sell_target_high": 1500.0,
  "stop_loss": 780.0,
  "suggested_pct_portfolio": 15.0,
  "growth_potential_pct": 65.0,
  "growth_timeline": "12-18 months",
  "thesis": "1-2 sentence thesis focused on 2-3 year horizon",
  "risks": ["Risk 1", "Risk 2"],
  "catalysts": ["Catalyst 1", "Catalyst 2"],
  "technical_score": 8,
  "sentiment_score": 7,
  "macro_score": 6,
  "is_new_pick": 0
}}

For stocks already in portfolio: set is_new_pick=0.
For watchlist stocks being recommended: set action="new_pick" and is_new_pick=1.
For watchlist stocks NOT worth buying now: set action="hold" with low confidence.

Return ONLY the JSON array.
"""
    return prompt


# ── LEGACY: single Claude call (kept for reference, no longer called) ──────────

def call_claude(prompt: str) -> list[dict]:
    """Send the analysis prompt to Claude and parse the JSON response."""
    import anthropic

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    print("\nCalling Claude Sonnet for analysis synthesis...")
    start = time.time()

    response = client.messages.create(
        model   = "claude-sonnet-4-6",
        max_tokens = 8000,
        system  = (
            "You are a rigorous financial analyst. You always respond with valid JSON only. "
            "Never include markdown code blocks or explanation text outside the JSON array."
        ),
        messages = [{"role": "user", "content": prompt}],
    )

    elapsed = time.time() - start
    print(f"  Claude responded in {elapsed:.1f}s (stop_reason={response.stop_reason})")

    if response.stop_reason == "max_tokens":
        print("  WARNING: response was truncated — increase max_tokens or reduce stock count")

    raw = response.content[0].text.strip()

    # Strip any accidental markdown fencing
    raw = raw.strip("`").strip()
    if raw.startswith("json"):
        raw = raw[4:].strip()

    try:
        recs = json.loads(raw)
        if not isinstance(recs, list):
            raise ValueError("Expected a JSON array")
        print(f"  Parsed {len(recs)} recommendations")
        return recs
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}")
        print(f"  Raw response (first 500 chars): {raw[:500]}")
        raise


# ── SAVE RESULTS ──────────────────────────────────────────────────────────────

def save_run(data: dict, recommendations: list[dict]) -> int:
    """Save analysis run metadata and all recommendations to the database."""
    market_summary = json.dumps({
        "overall_sentiment": data["market"].get("_meta", {}).get("overall_sentiment"),
        "spy_week":          data["market"].get("SPY", {}).get("week_change_pct"),
        "vix":               data["market"].get("VIX", {}).get("current"),
    })
    macro_summary   = json.dumps({k: data["macro"].get(k) for k in
                                   ["macro_score", "fed_funds_rate", "inflation_yoy",
                                    "rate_environment", "unemployment"]})
    sentiment_summary = json.dumps({"geo_risk_index": data["geo"].get("risk_index")})

    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO analysis_runs (trigger, status, market_summary, macro_summary, sentiment_summary)
            VALUES (?, 'completed', ?, ?, ?)
        """, (data["trigger"], market_summary, macro_summary, sentiment_summary))
        run_id = cursor.lastrowid

    VALID_ACTIONS = {"strong_buy", "buy", "hold", "sell", "strong_sell", "new_pick"}

    def normalize_action(raw_action: str) -> str:
        a = str(raw_action or "hold").lower().strip().replace(" ", "_").replace("-", "_")
        if a in VALID_ACTIONS:
            return a
        if "strong" in a and "buy" in a:
            return "strong_buy"
        if "strong" in a and "sell" in a:
            return "strong_sell"
        if "buy" in a or "accumulate" in a or "pick" in a:
            return "buy"
        if "sell" in a or "reduce" in a:
            return "sell"
        return "hold"

    def _serialize(v):
        """Ensure risks/catalysts are stored as JSON strings."""
        if isinstance(v, list):
            return json.dumps(v)
        if isinstance(v, str):
            return v  # already serialized by pipeline agents
        return "[]"

    # Normalize recs — ensure required fields have defaults
    normalized = []
    portfolio_set = set(data["portfolio_tickers"])
    for r in recommendations:
        ticker = str(r.get("ticker", "")).upper().strip()
        if not ticker:
            continue
        normalized.append({
            "ticker":                  ticker,
            "company_name":            r.get("company_name", ""),
            "action":                  normalize_action(r.get("action", "hold")),
            "confidence":              int(r.get("confidence", 5)),
            "current_price":           r.get("current_price"),
            "buy_range_low":           r.get("buy_range_low"),
            "buy_range_high":          r.get("buy_range_high"),
            "sell_target_low":         r.get("sell_target_low"),
            "sell_target_high":        r.get("sell_target_high"),
            "stop_loss":               r.get("stop_loss"),
            "suggested_shares":        r.get("suggested_shares"),
            "suggested_pct_portfolio": r.get("suggested_pct_portfolio"),
            "growth_potential_pct":    r.get("growth_potential_pct"),
            "growth_timeline":         r.get("growth_timeline", ""),
            "thesis":                  r.get("thesis", ""),
            "risks":                   _serialize(r.get("risks", [])),
            "catalysts":               _serialize(r.get("catalysts", [])),
            "technical_score":         r.get("technical_score"),
            "sentiment_score":         r.get("sentiment_score"),
            "macro_score":             r.get("macro_score"),
            "is_new_pick":             int(r.get("is_new_pick", 0) or ticker not in portfolio_set),
            "horizon":                 r.get("horizon", "medium"),
            "play_type":               r.get("play_type", "core"),
        })

    save_recommendations(run_id, normalized)

    # Also update current_price in portfolio table for P&L display
    with get_connection() as conn:
        for r in recommendations:
            ticker = str(r.get("ticker", "")).upper()
            price  = r.get("current_price")
            if ticker and price:
                conn.execute("""
                    UPDATE portfolio SET current_price=?, updated_at=CURRENT_TIMESTAMP
                    WHERE ticker=?
                """, (price, ticker))

    return run_id


def write_results_json(recommendations: list[dict], data: dict):
    """Write latest_results.json so Streamlit can read it even without DB access."""
    RESULTS_PATH.parent.mkdir(exist_ok=True)
    output = {
        "run_at":      data["run_at"],
        "trigger":     data["trigger"],
        "market":      data["market"],
        "macro":       {k: data["macro"].get(k) for k in
                        ["macro_score", "fed_funds_rate", "inflation_yoy",
                         "rate_environment", "inflation_status"]},
        "geo_risk":    {"index": data["geo"].get("risk_index"), "level": data["geo"].get("risk_level")},
        "recommendations": recommendations,
    }
    with open(RESULTS_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results written to {RESULTS_PATH}")


# ── MAIN ENTRY POINT ──────────────────────────────────────────────────────────

def run_analysis(
    trigger: str = "manual",
    scope: str = "full",
    tickers: list | None = None,
) -> int:
    """
    Multi-agent pipeline: collect → 5 agents → save. Returns run_id.

    scope:   'full' | 'portfolio_only' | 'watchlist_only' | 'momentum_scan'
    tickers: explicit list overrides scope (used by Research tab)
    """
    init_db()

    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO analysis_runs (trigger, status) VALUES (?, 'running')",
            (trigger,)
        )
        run_id = cursor.lastrowid

    start_time = time.time()

    try:
        data = collect_all_data(trigger=trigger)

        from core.agents.pipeline import run_pipeline
        recommendations = run_pipeline(data, scope=scope, tickers_override=tickers)

        run_id = save_run(data, recommendations)
        write_results_json(recommendations, data)

        duration = time.time() - start_time
        with get_connection() as conn:
            conn.execute("""
                UPDATE analysis_runs SET status='completed', duration_secs=? WHERE id=?
            """, (round(duration, 1), run_id))

        print(f"\n✅ Analysis complete in {duration:.0f}s — {len(recommendations)} recommendations (run #{run_id})")
        return run_id

    except Exception as e:
        duration = time.time() - start_time
        err = traceback.format_exc()
        print(f"\n❌ Analysis FAILED: {e}\n{err}")
        with get_connection() as conn:
            conn.execute("""
                UPDATE analysis_runs SET status='failed', error_message=?, duration_secs=? WHERE id=?
            """, (str(e)[:500], round(duration, 1), run_id))
        raise


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("trigger", nargs="?", default="manual")
    parser.add_argument("--scope",   default="full",
                        choices=["full", "portfolio_only", "watchlist_only", "momentum_scan"])
    parser.add_argument("--tickers", nargs="*", help="specific tickers to analyze")
    args = parser.parse_args()

    run_analysis(trigger=args.trigger, scope=args.scope, tickers=args.tickers)
