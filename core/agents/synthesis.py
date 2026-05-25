"""
Agent 5: Synthesis Agent
Combines all prior agent outputs into final actionable recommendations.
Has access to portfolio, profile, and recent trade history for context-aware output.
"""

import json
from .base import call_agent

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8000
BATCH_SIZE = 12

VALID_ACTIONS = {"strong_buy", "buy", "hold", "sell", "strong_sell", "new_pick"}


def _normalize_action(raw: str) -> str:
    a = str(raw or "hold").lower().strip().replace(" ", "_").replace("-", "_")
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


def run_synthesis_agent(
    tickers: list,
    momentum_outputs: dict,
    macro_result: dict,
    short_term_plays: list,
    long_term_convictions: list,
    stocks: dict,
    holdings: list,
    profile: dict,
    action_log: list,
    portfolio_tickers: set,
) -> list:
    """
    Returns list of FinalRecommendation dicts matching the recommendations DB schema.
    """
    if not tickers:
        return []

    # Build lookup maps
    st_map = {p["ticker"].upper(): p for p in short_term_plays if p.get("ticker")}
    lt_map = {c["ticker"].upper(): c for c in long_term_convictions if c.get("ticker")}
    holding_map = {h["ticker"]: h for h in holdings}

    # Portfolio context
    total_cost = sum((h.get("avg_cost_basis") or 0) * (h.get("shares") or 0) for h in holdings)
    total_val  = sum((h.get("current_price") or h.get("avg_cost_basis") or 0) * (h.get("shares") or 0) for h in holdings)

    # Recent trade history (last 10)
    recent_trades = []
    for t in action_log[:10]:
        recent_trades.append(f"{t.get('action_date','?')} {t.get('action','?')} {t.get('ticker','?')} @ ${t.get('price_per_share',0):.2f}")
    trades_str = "\n".join(recent_trades) if recent_trades else "No recent trades"

    # Strategy doc (future: load from file)
    strategy = profile.get("strategy", "Beat S&P 500 and NASDAQ 100. Maximize profit — long or short term.")

    sectors = profile.get("preferred_sectors", [])
    if isinstance(sectors, str):
        try:
            sectors = json.loads(sectors)
        except Exception:
            sectors = []

    all_results = []
    batches = [tickers[i:i+BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]

    for batch_idx, batch in enumerate(batches, 1):
        print(f"  [Synthesis] batch {batch_idx}/{len(batches)}: {batch}")

        # Build agent summary table
        summary_rows = []
        for ticker in batch:
            s     = stocks.get(ticker, {})
            mom   = momentum_outputs.get(ticker, {})
            st    = st_map.get(ticker)
            lt    = lt_map.get(ticker)
            h     = holding_map.get(ticker)
            pnl_str = ""
            if h and s.get("current_price") and h.get("avg_cost_basis"):
                pnl_pct = (s["current_price"] - h["avg_cost_basis"]) / h["avg_cost_basis"] * 100
                pnl_str = f" P&L:{pnl_pct:+.0f}%"

            _scp = s.get('current_price') or 0
            st_str = f"ST:{st['play_type']} tgt=${st.get('target') or 0:.0f} sl=${st.get('stop_loss') or 0:.0f}" if st else "ST:none"
            lt_str = f"LT:conviction={lt.get('conviction',0)} tgt=${lt.get('target') or 0:.0f}" if lt else "LT:none"
            summary_rows.append(
                f"| {ticker:6s} | ${_scp:8.2f} | {mom.get('momentum_signal','neutral'):8s} score={mom.get('momentum_score',5)} "
                f"| {st_str} | {lt_str} | {'HOLDING'+pnl_str if ticker in portfolio_tickers else 'watchlist'} |"
            )

        summary_table = "\n".join(summary_rows)

        # Detailed per-ticker blocks
        detail_blocks = []
        for ticker in batch:
            s   = stocks.get(ticker, {})
            st  = st_map.get(ticker)
            lt  = lt_map.get(ticker)
            h   = holding_map.get(ticker)
            mom = momentum_outputs.get(ticker, {})
            sent = s.get("sentiment", {})

            _dcp = s.get('current_price') or 0
            _dlo = s.get('low_52w') or 0
            _dhi = s.get('high_52w') or 0
            lines = [f"### {ticker} — {s.get('company_name', ticker)}  (sector: {s.get('sector','?')})"]
            lines.append(f"Price: ${_dcp:.2f}  52w: ${_dlo:.0f}–${_dhi:.0f}")
            if h:
                lines.append(f"Holding: {h.get('shares') or 0:.1f}sh @ ${h.get('avg_cost_basis') or 0:.2f}")
            if st:
                lines.append(f"Short-term play ({st.get('play_type')}): entry=${st.get('entry') or 0:.2f} tgt=${st.get('target') or 0:.2f} sl=${st.get('stop_loss') or 0:.2f} RR={st.get('risk_reward_ratio') or 0:.1f} | {st.get('thesis','')[:100]}")
            if lt:
                lines.append(f"Long-term conviction={lt.get('conviction',5)} horizon={lt.get('horizon','?')}: tgt=${lt.get('target') or 0:.0f} | {lt.get('thesis','')[:100]}")
                lines.append(f"LT risks: {', '.join((lt.get('key_risks') or [])[:2])} | catalysts: {', '.join((lt.get('key_catalysts') or [])[:2])}")
            lines.append(f"Momentum: {mom.get('momentum_signal','neutral')} ({mom.get('reason','')})")
            lines.append(f"Sentiment score: {sent.get('sentiment_score',5)} | Trends: {sent.get('trend_direction','stable')}")
            detail_blocks.append("\n".join(lines))

        details_str = "\n\n".join(detail_blocks)
        tickers_list = ", ".join(batch)

        prompt = f"""You are an elite stock analyst and portfolio manager.

PRIMARY STRATEGY: {strategy}
Preferred sectors: {', '.join(sectors)}
Risk tolerance: {profile.get('risk_tolerance', 'medium-high')}

MARKET REGIME: {macro_result.get('market_regime','neutral').upper()}
Key macro risks: {'; '.join(macro_result.get('key_risks',[])[:3])}
Cash recommendation: {macro_result.get('cash_recommendation_pct',0)}%

PORTFOLIO VALUE: Cost ${total_cost:,.0f} → Current ~${total_val:,.0f}

RECENT TRADES:
{trades_str}

AGENT SIGNAL SUMMARY:
| Ticker | Price | Momentum | Short-term | Long-term | Status |
{summary_table}

DETAILED ANALYSIS PER TICKER:
{details_str}

TASK: Produce final investment recommendations for: {tickers_list}

Rules:
1. PRIMARY goal is to beat SPY and QQQ — every rec must justify vs benchmark
2. For short-term plays: MUST include stop_loss (mandatory risk management)
3. For long-term holds: thesis should explain multi-month catalyst
4. If both short-term AND long-term signals exist: set horizon to the shorter one if confidence is high
5. Don't recommend buying something just sold (check recent trades)
6. Thesis: MAX 3 sentences. Be specific, not generic.

Return JSON array of exactly {len(batch)} objects (one per ticker, same order):
{{
  "ticker": "XXX",
  "company_name": "...",
  "action": "strong_buy|buy|hold|sell|strong_sell|new_pick",
  "confidence": <1-10>,
  "current_price": <float>,
  "buy_range_low": <float>,
  "buy_range_high": <float>,
  "sell_target_low": <float>,
  "sell_target_high": <float>,
  "stop_loss": <float>,
  "suggested_pct_portfolio": <float>,
  "growth_potential_pct": <float>,
  "growth_timeline": "<e.g. 2-4 weeks, 6-12 months>",
  "thesis": "<3 sentences max>",
  "risks": ["risk1", "risk2"],
  "catalysts": ["cat1", "cat2"],
  "technical_score": <1-10>,
  "sentiment_score": <1-10>,
  "macro_score": <1-10>,
  "is_new_pick": <0 or 1>,
  "horizon": "short|medium|long",
  "play_type": "core|momentum|seasonal"
}}

Return ONLY the JSON array. No other text."""

        try:
            results = call_agent(MODEL, prompt, MAX_TOKENS, f"Synthesis[{batch_idx}]")
            for r in results:
                if not r.get("ticker"):
                    continue
                r["ticker"]    = r["ticker"].upper()
                r["action"]    = _normalize_action(r.get("action", "hold"))
                r["is_new_pick"] = 0 if r["ticker"] in portfolio_tickers else 1
                r["risks"]     = json.dumps(r.get("risks") or [])
                r["catalysts"] = json.dumps(r.get("catalysts") or [])
                r.setdefault("horizon", "medium")
                r.setdefault("play_type", "core")
                r.setdefault("suggested_shares", None)
                all_results.append(r)
        except Exception as e:
            print(f"  [Synthesis] batch {batch_idx} ERROR: {e}")

    return all_results
