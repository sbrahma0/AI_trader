"""
Agent 3: Short-term Scanner
Identifies actionable short-term plays (days to weeks) from momentum-qualified tickers.
Only fires for tickers with momentum_score >= 7 OR has_near_catalyst=True.
"""

import json
from .base import call_agent

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 3000


def run_short_term_agent(
    eligible_tickers: list,
    momentum_outputs: dict,
    macro_result: dict,
    stocks: dict,
) -> list:
    """
    Returns list of short-term play dicts:
    {ticker, play_type, entry, target, stop_loss, risk_reward_ratio, timeline, thesis, confidence}
    """
    if not eligible_tickers:
        return []

    regime   = macro_result.get("market_regime", "neutral")
    biases   = macro_result.get("sector_biases", {})
    cash_pct = macro_result.get("cash_recommendation_pct", 0)

    ticker_blocks = []
    for ticker in eligible_tickers:
        s = stocks.get(ticker, {})
        if not s or s.get("error"):
            continue
        mom = momentum_outputs.get(ticker, {})
        sector = s.get("sector", "Unknown")
        _cp  = s.get('current_price') or 0
        _chg = s.get('change_pct_today') or 0
        _at  = s.get('analyst_target') or 0
        _lo  = s.get('low_52w') or 0
        _hi  = s.get('high_52w') or 0
        _rsi = s.get('rsi') or 50
        ticker_blocks.append(
            f"### {ticker} — {s.get('company_name', ticker)} ({sector})\n"
            f"Price: ${_cp:.2f}  Day: {_chg:+.1f}%\n"
            f"Momentum: {mom.get('momentum_signal','neutral').upper()} score={mom.get('momentum_score',5)} | {mom.get('reason','')}\n"
            f"Catalyst within 30d: {'YES — earnings ' + str(s.get('next_earnings_date','')) if mom.get('has_near_catalyst') else 'No'}\n"
            f"P/E: {s.get('pe_ratio','N/A')}  Fwd P/E: {s.get('forward_pe','N/A')}  Beta: {s.get('beta','N/A')}\n"
            f"Rev growth: {(s.get('revenue_growth') or 0)*100:.0f}%  Analyst target: ${_at:.0f}  Analyst rec: {s.get('analyst_rec','N/A')}\n"
            f"52w range: ${_lo:.2f} – ${_hi:.2f}  RSI: {_rsi:.0f}\n"
            f"Sector macro bias: {biases.get(sector, 0):+d}\n"
            f"Top news: {(s.get('recent_news') or [''])[0][:100]}"
        )

    if not ticker_blocks:
        return []

    blocks_str = "\n\n".join(ticker_blocks)
    tickers_list = ", ".join(eligible_tickers)

    prompt = f"""You are a short-term trading specialist. Goal: beat SPY and QQQ returns in the near term.

MARKET REGIME: {regime.upper()}  |  Cash recommendation: {cash_pct}%

ANALYZE THESE {len(eligible_tickers)} TICKERS FOR SHORT-TERM PLAYS:
{tickers_list}

{blocks_str}

TASK: For each ticker, decide if there is a credible short-term trade (days to a few weeks).
Only recommend a ticker if there is a clear setup — earnings catalyst, technical breakout,
momentum confirmation, or sector event. Do NOT force a recommendation if the setup is weak.

Return a JSON array. Include ONLY tickers with a genuine short-term play (can be empty []).
Each object:
{{
  "ticker": "XXX",
  "play_type": "catalyst" | "momentum" | "seasonal",
  "entry": <float — suggested entry price>,
  "target": <float — price target>,
  "stop_loss": <float — mandatory stop-loss>,
  "risk_reward_ratio": <float — target gain / stop loss distance>,
  "timeline": "<e.g. 5-10 days, 2-3 weeks>",
  "thesis": "<2-3 sentences max — WHY this works now>",
  "confidence": <1-10>
}}

Return ONLY the JSON array. No other text."""

    try:
        results = call_agent(MODEL, prompt, MAX_TOKENS, "Short-term")
        # Validate structure
        valid = []
        for r in results:
            if not r.get("ticker"):
                continue
            r["ticker"] = r["ticker"].upper()
            valid.append(r)
        return valid
    except Exception as e:
        print(f"  [Short-term] ERROR: {e}")
        return []
