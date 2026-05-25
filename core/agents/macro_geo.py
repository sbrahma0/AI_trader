"""
Agent 2: Macro / Geo Watcher
Analyzes macroeconomic and geopolitical data to determine market regime and sector biases.
Uses Haiku — structured analysis of numeric data, no stock-level reasoning needed.
"""

from .base import call_agent

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1000

SECTORS = [
    "AI Infrastructure", "Quantum Computing", "Space",
    "Clean Energy", "Nuclear Energy", "Semiconductors",
    "Software", "Crypto", "Financials", "Healthcare",
]


def run_macro_agent(macro: dict, geo: dict, market: dict) -> dict:
    """
    Returns {market_regime, sector_biases, cash_recommendation_pct, key_events, key_risks, rate_note}
    """
    spy  = market.get("SPY", {})
    qqq  = market.get("QQQ", {})
    vix  = market.get("VIX", {}).get("current") or market.get("^VIX", {}).get("current") or 20

    prompt = f"""You are a macroeconomic analyst for a stock trading system.

MACRO DATA:
- Fed Funds Rate: {macro.get('fed_funds_rate', 'N/A')}%  (trend: {macro.get('rate_trend', 'stable')}, environment: {macro.get('rate_environment', 'neutral')})
- Inflation YoY: {macro.get('inflation_yoy', 'N/A')}%  ({macro.get('inflation_status', 'unknown')})
- Unemployment: {macro.get('unemployment', 'N/A')}%
- US GDP Growth: {macro.get('gdp_growth_us', 'N/A')}%
- 10Y Treasury: {macro.get('treasury_10y', 'N/A')}%
- 2Y Treasury: {macro.get('treasury_2y', 'N/A')}%
- Yield Curve Spread (10Y-2Y): {macro.get('yield_curve', {}).get('spread', 'N/A')} (inverted: {macro.get('yield_curve', {}).get('inverted', False)})
- Consumer Sentiment: {macro.get('consumer_sentiment', 'N/A')}
- Macro Score: {macro.get('macro_score', 5)}/10

MARKET CONDITIONS:
- SPY: {spy.get('current', 'N/A')}  Day: {spy.get('day_change_pct', 0):+.1f}%  Week: {spy.get('week_change_pct', 0):+.1f}%
- QQQ: {qqq.get('current', 'N/A')}  Day: {qqq.get('day_change_pct', 0):+.1f}%  Week: {qqq.get('week_change_pct', 0):+.1f}%
- VIX: {vix}  {'(ELEVATED FEAR)' if float(vix or 20) > 25 else ''}
- Overall Sentiment: {market.get('_meta', {}).get('overall_sentiment', 'neutral')}

GEOPOLITICAL:
- Risk Index: {geo.get('index', 5)}/10  ({geo.get('level', 'medium').upper()})
- Headlines: {'; '.join((geo.get('top_headlines') or [])[:3])}

TASK: Determine the current investment regime and sector positioning.

Return ONLY this JSON object:
{{
  "market_regime": "risk_on" | "risk_off" | "neutral",
  "sector_biases": {{
    "AI Infrastructure": <-3 to +3>,
    "Quantum Computing": <-3 to +3>,
    "Space": <-3 to +3>,
    "Clean Energy": <-3 to +3>,
    "Nuclear Energy": <-3 to +3>,
    "Semiconductors": <-3 to +3>,
    "Software": <-3 to +3>,
    "Crypto": <-3 to +3>,
    "Financials": <-3 to +3>,
    "Healthcare": <-3 to +3>
  }},
  "cash_recommendation_pct": <0-100>,
  "key_events": ["<event1>", "<event2>"],
  "key_risks": ["<risk1>", "<risk2>"],
  "rate_sensitivity_note": "<one sentence on rate impact for growth/tech stocks>"
}}

bias scale: +3=strong tailwind, 0=neutral, -3=strong headwind
cash_recommendation_pct: % of portfolio to hold as cash (0 = fully invested, 30+ = defensive)"""

    try:
        result = call_agent(MODEL, prompt, MAX_TOKENS, "Macro/Geo")
        result.setdefault("market_regime", "neutral")
        result.setdefault("sector_biases", {s: 0 for s in SECTORS})
        result.setdefault("cash_recommendation_pct", 0)
        result.setdefault("key_events", [])
        result.setdefault("key_risks", [])
        result.setdefault("rate_sensitivity_note", "")
        return result
    except Exception as e:
        print(f"  [Macro/Geo] ERROR: {e}")
        return {
            "market_regime": "neutral",
            "sector_biases": {s: 0 for s in SECTORS},
            "cash_recommendation_pct": 0,
            "key_events": [],
            "key_risks": [],
            "rate_sensitivity_note": "",
        }
