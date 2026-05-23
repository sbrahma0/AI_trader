"""
market_data.py — Fetch price data, technicals, and fundamentals.

Sources:
  - yfinance    : OHLCV history, info, earnings calendar (no key needed)
  - Finnhub     : Real-time quote, news, insider sentiment (free tier)
  - Alpha Vantage: RSI, MACD, SMA via API (free tier, 25 req/day)

All functions return plain dicts/lists — no external types leak out.
"""

import os
import time
import requests
import json
from datetime import datetime, timedelta
from typing import Optional

# ── HELPERS ───────────────────────────────────────────────────────────────────

def _get(url, params=None, timeout=10):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  GET {url} failed: {e}")
        return {}


# ── YFINANCE ─────────────────────────────────────────────────────────────────

def get_price_history(ticker: str, period: str = "1y") -> dict:
    """
    Fetch OHLCV history for technical analysis.
    period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y
    Returns {ticker, dates[], opens[], highs[], lows[], closes[], volumes[]}
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period=period)
        if hist.empty:
            return {}
        return {
            "ticker":  ticker,
            "dates":   [str(d.date()) for d in hist.index],
            "opens":   hist["Open"].round(4).tolist(),
            "highs":   hist["High"].round(4).tolist(),
            "lows":    hist["Low"].round(4).tolist(),
            "closes":  hist["Close"].round(4).tolist(),
            "volumes": hist["Volume"].tolist(),
        }
    except Exception as e:
        print(f"  yfinance price history {ticker}: {e}")
        return {}


def get_stock_info(ticker: str) -> dict:
    """
    Fetch key fundamentals: P/E, market cap, sector, beta, revenue growth, etc.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        return {
            "ticker":             ticker,
            "company_name":       info.get("longName") or info.get("shortName", ""),
            "sector":             info.get("sector", ""),
            "industry":           info.get("industry", ""),
            "market_cap":         info.get("marketCap"),
            "current_price":      info.get("currentPrice") or info.get("regularMarketPrice"),
            "52w_high":           info.get("fiftyTwoWeekHigh"),
            "52w_low":            info.get("fiftyTwoWeekLow"),
            "pe_ratio":           info.get("trailingPE"),
            "forward_pe":         info.get("forwardPE"),
            "peg_ratio":          info.get("pegRatio"),
            "beta":               info.get("beta"),
            "revenue_growth":     info.get("revenueGrowth"),
            "earnings_growth":    info.get("earningsGrowth"),
            "gross_margins":      info.get("grossMargins"),
            "debt_to_equity":     info.get("debtToEquity"),
            "free_cashflow":      info.get("freeCashflow"),
            "analyst_target":     info.get("targetMeanPrice"),
            "analyst_low":        info.get("targetLowPrice"),
            "analyst_high":       info.get("targetHighPrice"),
            "recommendation":     info.get("recommendationKey", ""),  # buy/hold/sell
            "short_ratio":        info.get("shortRatio"),
            "dividend_yield":     info.get("dividendYield"),
        }
    except Exception as e:
        print(f"  yfinance info {ticker}: {e}")
        return {"ticker": ticker}


def get_earnings_calendar(ticker: str) -> dict:
    """Next earnings date and EPS estimate."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        cal = t.calendar
        if cal is None or cal.empty:
            return {}
        # calendar is a DataFrame with dates as columns
        dates = cal.columns.tolist() if hasattr(cal, 'columns') else []
        next_date = str(dates[0]) if dates else None
        return {
            "next_earnings_date": next_date,
            "eps_estimate":       float(cal.loc["EPS Estimate"].iloc[0]) if "EPS Estimate" in cal.index and not cal.empty else None,
        }
    except Exception as e:
        print(f"  yfinance earnings {ticker}: {e}")
        return {}


# ── TECHNICAL INDICATORS ─────────────────────────────────────────────────────

def compute_technicals(price_data: dict) -> dict:
    """
    Compute RSI, MACD, Bollinger Bands, SMA 20/50/200 from price history.
    Uses only numpy/pandas — no TA-lib needed.
    Returns a dict of indicator values + a composite technical_score (1-10).
    """
    if not price_data or not price_data.get("closes"):
        return {}

    try:
        import numpy as np
        closes = np.array(price_data["closes"], dtype=float)
        volumes = np.array(price_data["volumes"], dtype=float)
        n = len(closes)
        if n < 20:
            return {}

        current = closes[-1]

        # ── SMAs ──────────────────────────────────────────────────────────────
        sma20  = float(np.mean(closes[-20:]))  if n >= 20  else None
        sma50  = float(np.mean(closes[-50:]))  if n >= 50  else None
        sma200 = float(np.mean(closes[-200:])) if n >= 200 else None

        # ── RSI (14-period) ───────────────────────────────────────────────────
        delta = np.diff(closes[-15:])
        gains = np.where(delta > 0, delta, 0)
        losses = np.where(delta < 0, -delta, 0)
        avg_gain = np.mean(gains[-14:]) if len(gains) >= 14 else 0
        avg_loss = np.mean(losses[-14:]) if len(losses) >= 14 else 0.001
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = float(100 - 100 / (1 + rs))

        # ── MACD (12, 26, 9) ──────────────────────────────────────────────────
        def ema(arr, span):
            k = 2 / (span + 1)
            result = [arr[0]]
            for v in arr[1:]:
                result.append(v * k + result[-1] * (1 - k))
            return np.array(result)

        if n >= 26:
            ema12 = ema(closes, 12)
            ema26 = ema(closes, 26)
            macd_line = ema12 - ema26
            signal_line = ema(macd_line, 9)
            macd_val     = float(macd_line[-1])
            macd_signal  = float(signal_line[-1])
            macd_hist    = float(macd_val - macd_signal)
        else:
            macd_val = macd_signal = macd_hist = None

        # ── Bollinger Bands (20, 2σ) ──────────────────────────────────────────
        bb_mid = sma20
        bb_std = float(np.std(closes[-20:]))
        bb_upper = bb_mid + 2 * bb_std if bb_mid else None
        bb_lower = bb_mid - 2 * bb_std if bb_mid else None

        # ── Volume trend (20-day avg vs 5-day avg) ─────────────────────────────
        vol_avg20 = float(np.mean(volumes[-20:])) if n >= 20 else None
        vol_avg5  = float(np.mean(volumes[-5:]))  if n >= 5  else None
        vol_trend = "above_avg" if (vol_avg5 and vol_avg20 and vol_avg5 > vol_avg20 * 1.2) else "normal"

        # ── 52-week position ──────────────────────────────────────────────────
        high52 = float(np.max(closes[-252:])) if n >= 252 else float(np.max(closes))
        low52  = float(np.min(closes[-252:])) if n >= 252 else float(np.min(closes))
        pct_from_52h = (current - high52) / high52 * 100
        pct_from_52l = (current - low52)  / low52  * 100

        # ── Composite technical score (1-10) ──────────────────────────────────
        score = 5  # neutral baseline

        # RSI: oversold = bullish, overbought = bearish
        if rsi < 30:   score += 2   # oversold — buy signal
        elif rsi < 45: score += 1
        elif rsi > 70: score -= 2   # overbought — caution
        elif rsi > 60: score -= 1

        # Price vs SMAs
        if sma50  and current > sma50:   score += 1
        if sma200 and current > sma200:  score += 1
        if sma20  and current > sma20:   score += 0.5
        # Golden cross (SMA50 > SMA200)
        if sma50 and sma200 and sma50 > sma200: score += 0.5

        # MACD: positive histogram = bullish momentum
        if macd_hist is not None:
            if macd_hist > 0: score += 0.5
            else:             score -= 0.5

        # Distance from 52-week high: near high = momentum
        if pct_from_52h > -5:  score += 0.5  # within 5% of 52w high
        if pct_from_52h < -30: score -= 1    # deep correction

        score = max(1, min(10, round(score)))

        return {
            "current_price":    round(current, 2),
            "sma20":            round(sma20, 2)  if sma20  else None,
            "sma50":            round(sma50, 2)  if sma50  else None,
            "sma200":           round(sma200, 2) if sma200 else None,
            "rsi":              round(rsi, 1),
            "macd":             round(macd_val, 3)    if macd_val    is not None else None,
            "macd_signal":      round(macd_signal, 3) if macd_signal is not None else None,
            "macd_histogram":   round(macd_hist, 3)   if macd_hist   is not None else None,
            "bb_upper":         round(bb_upper, 2) if bb_upper else None,
            "bb_lower":         round(bb_lower, 2) if bb_lower else None,
            "high_52w":         round(high52, 2),
            "low_52w":          round(low52, 2),
            "pct_from_52w_high": round(pct_from_52h, 1),
            "pct_from_52w_low":  round(pct_from_52l, 1),
            "volume_trend":     vol_trend,
            "technical_score":  score,
        }
    except Exception as e:
        print(f"  technicals error: {e}")
        return {}


# ── FINNHUB ───────────────────────────────────────────────────────────────────

FINNHUB_BASE = "https://finnhub.io/api/v1"

def get_finnhub_quote(ticker: str) -> dict:
    """Real-time quote from Finnhub (current, open, high, low, prev close, change%)."""
    key = os.getenv("FINNHUB_API_KEY", "")
    if not key:
        return {}
    data = _get(f"{FINNHUB_BASE}/quote", {"symbol": ticker, "token": key})
    if not data or data.get("c", 0) == 0:
        return {}
    return {
        "current":    data.get("c"),
        "open":       data.get("o"),
        "high":       data.get("h"),
        "low":        data.get("l"),
        "prev_close": data.get("pc"),
        "change_pct": round((data["c"] - data["pc"]) / data["pc"] * 100, 2) if data.get("pc") else None,
    }


def get_finnhub_company_news(ticker: str, days_back: int = 7) -> list[dict]:
    """Recent company-specific news from Finnhub."""
    key = os.getenv("FINNHUB_API_KEY", "")
    if not key:
        return []
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    data = _get(f"{FINNHUB_BASE}/company-news",
                {"symbol": ticker, "from": start, "to": end, "token": key})
    if not isinstance(data, list):
        return []
    return [
        {"headline": item.get("headline", ""), "summary": item.get("summary", ""),
         "source": item.get("source", ""), "datetime": item.get("datetime", 0)}
        for item in data[:10]
    ]


def get_finnhub_sentiment(ticker: str) -> dict:
    """Insider sentiment score from Finnhub (based on insider transactions)."""
    key = os.getenv("FINNHUB_API_KEY", "")
    if not key:
        return {}
    data = _get(f"{FINNHUB_BASE}/stock/insider-sentiment",
                {"symbol": ticker, "from": "2024-01-01", "token": key})
    if not data or not data.get("data"):
        return {}
    # Sum MSPR (Monthly Share Purchase Ratio) — positive = insiders buying
    scores = [item.get("mspr", 0) for item in data["data"][-6:]]  # last 6 months
    avg = sum(scores) / len(scores) if scores else 0
    return {"insider_sentiment_mspr": round(avg, 4)}


# ── ALPHA VANTAGE ─────────────────────────────────────────────────────────────

AV_BASE = "https://www.alphavantage.co/query"

def get_av_rsi(ticker: str, interval: str = "weekly") -> Optional[float]:
    """Fetch RSI from Alpha Vantage (backup to our computed RSI, saves yfinance calls)."""
    key = os.getenv("ALPHA_VANTAGE_KEY", "")
    if not key:
        return None
    data = _get(AV_BASE, {
        "function": "RSI", "symbol": ticker, "interval": interval,
        "time_period": 14, "series_type": "close", "apikey": key
    })
    technical = data.get("Technical Analysis: RSI", {})
    if not technical:
        return None
    latest_date = sorted(technical.keys())[-1]
    return float(technical[latest_date]["RSI"])


# ── FULL STOCK SNAPSHOT ───────────────────────────────────────────────────────

def get_full_snapshot(ticker: str, use_alphavantage: bool = False) -> dict:
    """
    Master function: returns a complete data snapshot for one ticker.
    Combines yfinance history, fundamentals, technicals, and Finnhub data.
    """
    print(f"  Fetching {ticker}...")

    # Price history (1 year for technicals)
    hist = get_price_history(ticker, period="1y")
    technicals = compute_technicals(hist) if hist else {}

    # Fundamentals
    info = get_stock_info(ticker)
    earnings = get_earnings_calendar(ticker)

    # Real-time quote (override current price if available)
    quote = get_finnhub_quote(ticker)
    company_news = get_finnhub_company_news(ticker)
    insider = get_finnhub_sentiment(ticker)

    current_price = (quote.get("current") or
                     technicals.get("current_price") or
                     info.get("current_price"))

    return {
        "ticker":          ticker,
        "company_name":    info.get("company_name", ""),
        "sector":          info.get("sector", ""),
        "current_price":   current_price,
        "change_pct_today": quote.get("change_pct"),
        # Fundamentals
        "market_cap":      info.get("market_cap"),
        "pe_ratio":        info.get("pe_ratio"),
        "forward_pe":      info.get("forward_pe"),
        "peg_ratio":       info.get("peg_ratio"),
        "beta":            info.get("beta"),
        "revenue_growth":  info.get("revenue_growth"),
        "earnings_growth": info.get("earnings_growth"),
        "analyst_target":  info.get("analyst_target"),
        "analyst_rec":     info.get("recommendation"),
        "short_ratio":     info.get("short_ratio"),
        # Technicals
        **technicals,
        # Earnings
        "next_earnings_date": earnings.get("next_earnings_date"),
        "eps_estimate":       earnings.get("eps_estimate"),
        # Insider sentiment
        "insider_sentiment": insider.get("insider_sentiment_mspr"),
        # Recent news (just headlines for the prompt)
        "recent_news": [n["headline"] for n in company_news[:5]],
        "fetched_at":  datetime.now().isoformat(),
    }


def get_market_overview() -> dict:
    """
    Fetch broad market indices and sector ETF performance.
    Returns SPY, QQQ, SMH (semis), XLE (energy), ARKQ (autonomous tech).
    """
    tickers = {
        "SPY":  "S&P 500",
        "QQQ":  "NASDAQ 100",
        "SMH":  "Semiconductors",
        "XLE":  "Energy",
        "ARKQ": "Autonomous Tech / AI",
        "VIX":  "Volatility Index",
    }
    overview = {}
    for symbol, name in tickers.items():
        try:
            import yfinance as yf
            t = yf.Ticker(symbol)
            hist = t.history(period="5d")
            if hist.empty:
                continue
            current = float(hist["Close"].iloc[-1])
            prev    = float(hist["Close"].iloc[-2]) if len(hist) > 1 else current
            week_ago = float(hist["Close"].iloc[0])
            overview[symbol] = {
                "name":          name,
                "current":       round(current, 2),
                "day_change_pct": round((current - prev) / prev * 100, 2),
                "week_change_pct": round((current - week_ago) / week_ago * 100, 2),
            }
        except Exception as e:
            print(f"  market overview {symbol}: {e}")

    # Overall sentiment signal
    spy = overview.get("SPY", {})
    vix = overview.get("VIX", {})
    sentiment = "neutral"
    if spy.get("week_change_pct", 0) > 2 and (vix.get("current", 20) < 20):
        sentiment = "bullish"
    elif spy.get("week_change_pct", 0) < -2 or (vix.get("current", 20) > 25):
        sentiment = "bearish"

    overview["_meta"] = {
        "overall_sentiment": sentiment,
        "fetched_at": datetime.now().isoformat(),
    }
    return overview
