"""
Agent 1: Momentum Agent
Scans all tickers for short-term momentum signals using technical + sentiment data only.
Uses Haiku (fast, cheap) — purely numeric pattern classification.
"""

from datetime import datetime, timedelta
from .base import call_agent

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 4000


def run_momentum_agent(stocks: dict) -> dict:
    """
    Returns {ticker: {momentum_signal, momentum_score, short_term_window, reason, has_near_catalyst}}
    """
    if not stocks:
        return {}

    # Build compact JSON rows — technical + sentiment only, no narrative
    rows = []
    now = datetime.now()
    for ticker, s in stocks.items():
        if s.get("error"):
            continue
        sent = s.get("sentiment", {})
        # Detect near-term catalyst in Python (no LLM needed)
        has_catalyst = False
        ned = s.get("next_earnings_date")
        if ned:
            try:
                ed = datetime.strptime(str(ned)[:10], "%Y-%m-%d")
                has_catalyst = (ed - now).days <= 30
            except Exception:
                pass

        rows.append({
            "t":   ticker,
            "rsi": round(s.get("rsi") or 50, 1),
            "macd_h": round(s.get("macd_histogram") or 0, 4),
            "pct_52h": round(s.get("pct_from_52w_high") or 0, 1),
            "pct_52l": round(s.get("pct_from_52w_low") or 0, 1),
            "vol":  s.get("volume_trend", "normal"),
            "ts":   s.get("technical_score") or 5,
            "ss":   sent.get("sentiment_score") or 5,
            "tr":   sent.get("trends_score") or 5,
            "td":   sent.get("trend_direction", "stable"),
            "news": (sent.get("top_headlines") or [""])[0][:80],
            "cat":  has_catalyst,
        })

    if not rows:
        return {}

    import json
    data_json = json.dumps(rows, separators=(",", ":"))

    prompt = f"""You are a momentum classifier for a stock trading system.

Analyze each stock's technical and sentiment data and classify its short-term momentum.

INPUT (compact JSON array, fields: t=ticker, rsi, macd_h=MACD histogram, pct_52h=% below 52w high,
pct_52l=% above 52w low, vol=volume trend, ts=technical score 1-10, ss=sentiment score 1-10,
tr=trends score 1-10, td=trend direction, news=top headline, cat=has near-term catalyst):

{data_json}

SCORING GUIDANCE:
- RSI < 35: oversold (bullish signal), RSI > 70: overbought (bearish signal)
- MACD histogram > 0 and rising: bullish momentum
- Price near 52w low (pct_52l < 15%) with rising sentiment: potential reversal
- volume=above_avg + positive sentiment: confirms momentum
- Technical score >= 7 + sentiment score >= 7 = strong bullish
- Technical score <= 3 + sentiment score <= 3 = strong bearish

RETURN a JSON array (one object per ticker, same order as input):
[
  {{
    "ticker": "XXX",
    "momentum_signal": "bullish" | "bearish" | "neutral",
    "momentum_score": <1-10>,
    "short_term_window": "<e.g. 3-7 days, 1-2 weeks>",
    "reason": "<one sentence max>"
  }},
  ...
]

IMPORTANT: Return ONLY the JSON array. No other text."""

    try:
        results = call_agent(MODEL, prompt, MAX_TOKENS, "Momentum")
        output = {}
        for item in results:
            ticker = item.get("ticker", "").upper()
            if not ticker:
                continue
            # Merge in the has_near_catalyst flag we computed in Python
            orig = next((r for r in rows if r["t"] == ticker), {})
            output[ticker] = {
                "momentum_signal":    item.get("momentum_signal", "neutral"),
                "momentum_score":     int(item.get("momentum_score", 5)),
                "short_term_window":  item.get("short_term_window", "1-2 weeks"),
                "reason":             item.get("reason", ""),
                "has_near_catalyst":  orig.get("cat", False),
            }
        return output
    except Exception as e:
        print(f"  [Momentum] ERROR: {e}")
        # Return neutral fallback for all tickers
        return {
            r["t"]: {
                "momentum_signal": "neutral",
                "momentum_score": 5,
                "short_term_window": "unknown",
                "reason": "agent error",
                "has_near_catalyst": r["cat"],
            }
            for r in rows
        }
