"""
Agent 4: Long-term Analyst
Evaluates tickers for medium-to-long term conviction (months to years).
Uses fundamentals + macro alignment. Skips technical noise.
"""

import json
from .base import call_agent

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 5000
BATCH_SIZE = 15


def run_long_term_agent(
    tickers: list,
    macro_result: dict,
    stocks: dict,
    holdings: list,
    profile: dict,
) -> list:
    """
    Returns list of long-term conviction dicts:
    {ticker, conviction, horizon, thesis, target, key_risks, key_catalysts, sector_alignment}
    """
    if not tickers:
        return []

    holding_map = {h["ticker"]: h for h in holdings}
    regime  = macro_result.get("market_regime", "neutral")
    biases  = macro_result.get("sector_biases", {})
    sectors = profile.get("preferred_sectors", ["AI Infrastructure", "Space", "Quantum", "Energy"])
    if isinstance(sectors, str):
        import json as _j
        try:
            sectors = _j.loads(sectors)
        except Exception:
            sectors = []

    all_results = []
    batches = [tickers[i:i+BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]

    for batch_idx, batch in enumerate(batches, 1):
        print(f"  [Long-term] batch {batch_idx}/{len(batches)}: {batch}")

        ticker_blocks = []
        for ticker in batch:
            s = stocks.get(ticker, {})
            if not s or s.get("error"):
                continue
            h = holding_map.get(ticker)
            sector = s.get("sector", "Unknown")
            position_line = ""
            _cp  = s.get('current_price') or 0
            _at  = s.get('analyst_target') or 0
            _lo  = s.get('low_52w') or 0
            _hi  = s.get('high_52w') or 0
            if h:
                _cost = h.get("avg_cost_basis") or 0
                pnl_pct = (_cp - _cost) / (_cost or 1) * 100
                _sh   = h.get('shares') or 0
                position_line = f"YOUR POSITION: {_sh:.1f} shares @ ${_cost:.2f} avg | P&L: {pnl_pct:+.1f}%\n"

            ticker_blocks.append(
                f"### {ticker} — {s.get('company_name', ticker)} ({sector})\n"
                f"{position_line}"
                f"Price: ${_cp:.2f}  Market Cap: ${(s.get('market_cap') or 0)/1e9:.1f}B\n"
                f"P/E: {s.get('pe_ratio','N/A')}  Fwd P/E: {s.get('forward_pe','N/A')}  PEG: {s.get('peg_ratio','N/A')}\n"
                f"Beta: {s.get('beta','N/A')}  Debt/Equity: {s.get('debt_to_equity','N/A')}\n"
                f"Rev growth: {(s.get('revenue_growth') or 0)*100:.0f}%  Earnings growth: {(s.get('earnings_growth') or 0)*100:.0f}%\n"
                f"Gross margin: {(s.get('gross_margins') or 0)*100:.0f}%  FCF: ${(s.get('free_cashflow') or 0)/1e9:.1f}B\n"
                f"Analyst target: ${_at:.0f}  Rec: {s.get('analyst_rec','N/A')}\n"
                f"52w range: ${_lo:.2f} – ${_hi:.2f}\n"
                f"Sector macro bias: {biases.get(sector, 0):+d}  |  Next earnings: {s.get('next_earnings_date','unknown')}\n"
                f"Recent news: {'; '.join((s.get('recent_news') or [])[:2])[:120]}"
            )

        if not ticker_blocks:
            continue

        blocks_str   = "\n\n".join(ticker_blocks)
        tickers_list = ", ".join(batch)

        prompt = f"""You are a long-term stock analyst. Goal: maximize profit vs SPY/QQQ benchmark.

INVESTMENT PROFILE:
- Preferred sectors: {', '.join(sectors)}
- Strategy: {profile.get('strategy', 'Beat S&P 500 and NASDAQ 100')}
- Risk tolerance: {profile.get('risk_tolerance', 'medium-high')}

MARKET REGIME: {regime.upper()}
Key risks: {'; '.join(macro_result.get('key_risks', [])[:3])}
Rate note: {macro_result.get('rate_sensitivity_note', '')}

ANALYZE THESE TICKERS: {tickers_list}

{blocks_str}

For EACH ticker, provide a long-term conviction assessment (months to years).
Focus on: can this stock outperform SPY/QQQ over the investment horizon?

Return a JSON array with one object per ticker:
{{
  "ticker": "XXX",
  "conviction": <1-10>,
  "horizon": "<e.g. 6-12 months, 12-24 months>",
  "thesis": "<2-3 sentences — core investment case>",
  "target": <float — 12-month price target>,
  "key_risks": ["risk1", "risk2"],
  "key_catalysts": ["catalyst1", "catalyst2"],
  "sector_alignment": "strong" | "moderate" | "weak"
}}

Return ONLY the JSON array. No other text."""

        try:
            results = call_agent(MODEL, prompt, MAX_TOKENS, f"Long-term[{batch_idx}]")
            for r in results:
                if r.get("ticker"):
                    r["ticker"] = r["ticker"].upper()
                    all_results.append(r)
        except Exception as e:
            print(f"  [Long-term] batch {batch_idx} ERROR: {e}")

    return all_results
