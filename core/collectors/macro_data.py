"""
macro_data.py — Macro-economic indicators and global context.

Sources:
  - FRED API   : Fed Funds Rate, CPI, unemployment, yield curve, GDP (free)
  - World Bank : Global GDP growth, trade data (free, no key)
  - BLS API    : US jobs data (free, no key for basic series)
  - Derived    : Yield curve spread (10Y - 2Y), recession signal
"""

import os
import requests
from datetime import datetime, timedelta
from typing import Optional


def _get(url, params=None, timeout=12):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  GET {url} failed: {e}")
        return {}


# ── FRED API ──────────────────────────────────────────────────────────────────

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# Key FRED series IDs
FRED_SERIES = {
    "fed_funds_rate":    "FEDFUNDS",       # Federal Funds Effective Rate (monthly)
    "cpi_yoy":           "CPIAUCSL",       # CPI All Urban Consumers (for YoY calc)
    "unemployment":      "UNRATE",         # Unemployment Rate
    "gdp_growth":        "A191RL1Q225SBEA",# Real GDP Growth Rate (quarterly)
    "treasury_10y":      "DGS10",          # 10-Year Treasury Constant Maturity
    "treasury_2y":       "DGS2",           # 2-Year Treasury Constant Maturity
    "treasury_3m":       "DGS3MO",         # 3-Month Treasury
    "m2_money_supply":   "M2SL",           # M2 Money Supply (for liquidity)
    "consumer_sentiment":"UMCSENT",        # U Michigan Consumer Sentiment
    "industrial_prod":   "INDPRO",         # Industrial Production Index
}

def _fetch_fred_series(series_id: str, limit: int = 12) -> list[dict]:
    """Fetch the latest N observations for a FRED series."""
    key = os.getenv("FRED_API_KEY", "")
    if not key:
        return []
    data = _get(FRED_BASE, {
        "series_id":    series_id,
        "api_key":      key,
        "file_type":    "json",
        "sort_order":   "desc",
        "limit":        limit,
    })
    obs = data.get("observations", [])
    results = []
    for o in obs:
        try:
            val = float(o["value"])
            results.append({"date": o["date"], "value": val})
        except (ValueError, KeyError):
            continue
    return sorted(results, key=lambda x: x["date"])  # ascending


def get_fred_snapshot() -> dict:
    """
    Fetch all key FRED indicators and return a structured macro snapshot.
    """
    snapshot = {}

    for name, series_id in FRED_SERIES.items():
        obs = _fetch_fred_series(series_id, limit=14)
        if not obs:
            snapshot[name] = {"current": None, "trend": "unknown"}
            continue

        current = obs[-1]["value"]
        prev    = obs[-2]["value"] if len(obs) >= 2 else current
        year_ago = obs[0]["value"] if len(obs) >= 12 else prev

        snapshot[name] = {
            "current":    round(current, 3),
            "prev":       round(prev, 3),
            "year_ago":   round(year_ago, 3),
            "change":     round(current - prev, 3),
            "yoy_change": round(current - year_ago, 3),
            "trend":      "rising" if current > prev else "falling" if current < prev else "stable",
            "date":       obs[-1]["date"],
        }

    # ── Derived signals ───────────────────────────────────────────────────────

    # Yield curve spread: 10Y - 2Y (inverted = recession risk)
    t10 = snapshot.get("treasury_10y", {}).get("current")
    t2  = snapshot.get("treasury_2y", {}).get("current")
    t3m = snapshot.get("treasury_3m", {}).get("current")
    if t10 and t2:
        spread_10_2 = round(t10 - t2, 3)
        snapshot["yield_curve_10_2"] = {
            "spread":  spread_10_2,
            "inverted": spread_10_2 < 0,
            "signal":  "recession_risk" if spread_10_2 < -0.3 else
                       "caution"        if spread_10_2 < 0    else "normal",
        }

    # Rate environment assessment
    fed_rate = snapshot.get("fed_funds_rate", {}).get("current")
    if fed_rate is not None:
        snapshot["rate_environment"] = (
            "restrictive" if fed_rate > 4.5 else
            "neutral"     if fed_rate > 2.5 else
            "accommodative"
        )

    # Inflation status
    cpi = snapshot.get("cpi_yoy", {})
    cpi_yoy = cpi.get("yoy_change")  # approximate YoY change in index level
    if cpi_yoy is not None:
        # CPIAUCSL is the index level; calculate true YoY%
        cpi_current  = cpi.get("current")
        cpi_year_ago = cpi.get("year_ago")
        if cpi_current and cpi_year_ago and cpi_year_ago > 0:
            true_yoy = round((cpi_current - cpi_year_ago) / cpi_year_ago * 100, 2)
            snapshot["inflation_yoy_pct"] = true_yoy
            snapshot["inflation_status"] = (
                "high"     if true_yoy > 4.0 else
                "elevated" if true_yoy > 2.5 else
                "target"   if true_yoy > 1.5 else
                "low"
            )

    snapshot["_meta"] = {"fetched_at": datetime.now().isoformat(), "source": "FRED"}
    return snapshot


# ── WORLD BANK ────────────────────────────────────────────────────────────────

WB_BASE = "https://api.worldbank.org/v2"

def get_world_bank_gdp() -> dict:
    """
    Fetch GDP growth rates for key economies from World Bank.
    No API key needed.
    """
    countries = {
        "US":  "United States",
        "CN":  "China",
        "DE":  "Germany",
        "JP":  "Japan",
        "IN":  "India",
        "TW":  "Taiwan",   # key for semiconductor supply chain
    }
    results = {}
    indicator = "NY.GDP.MKTP.KD.ZG"  # GDP growth (annual %)

    for code, name in countries.items():
        try:
            data = _get(
                f"{WB_BASE}/country/{code}/indicator/{indicator}",
                {"format": "json", "mrv": 3, "per_page": 3}
            )
            if isinstance(data, list) and len(data) > 1 and data[1]:
                entries = [e for e in data[1] if e.get("value") is not None]
                if entries:
                    latest = entries[0]
                    results[code] = {
                        "name":       name,
                        "gdp_growth": round(float(latest["value"]), 2),
                        "year":       latest.get("date"),
                    }
        except Exception as e:
            print(f"  World Bank {code}: {e}")

    return results


# ── BLS (JOBS DATA) ───────────────────────────────────────────────────────────

def get_bls_jobs() -> dict:
    """
    Fetch nonfarm payrolls and jobless claims from BLS public API v2.
    Free, no key needed for basic series.
    """
    series = {
        "nonfarm_payrolls": "CES0000000001",  # Total nonfarm payrolls
        "tech_jobs":        "CES5051000001",  # IT sector employment
    }
    results = {}

    try:
        r = requests.post(
            "https://api.bls.gov/publicAPI/v2/timeseries/data/",
            json={"seriesid": list(series.values()), "startyear": str(datetime.now().year - 1), "endyear": str(datetime.now().year)},
            timeout=12
        )
        data = r.json()
        if data.get("status") == "REQUEST_SUCCEEDED":
            for result in data.get("Results", {}).get("series", []):
                sid = result.get("seriesID")
                name = next((k for k, v in series.items() if v == sid), sid)
                obs  = result.get("data", [])
                if obs:
                    latest = obs[0]
                    results[name] = {
                        "value": float(latest.get("value", 0).replace(",","")),
                        "period": latest.get("period"),
                        "year":  latest.get("year"),
                    }
    except Exception as e:
        print(f"  BLS API: {e}")

    return results


# ── COMBINED MACRO SUMMARY ────────────────────────────────────────────────────

def get_macro_summary() -> dict:
    """
    Compose a full macro snapshot for the Claude analysis prompt.
    Includes Fed policy, inflation, growth, jobs, yield curve, and global context.
    Returns a macro_score (1-10) where 10 = ideal macro backdrop for growth stocks.
    """
    fred    = get_fred_snapshot()
    world   = get_world_bank_gdp()
    bls     = get_bls_jobs()

    # ── Macro score heuristic ──────────────────────────────────────────────────
    # For Sayan's growth / AI-infra strategy:
    #   Good:  Falling rates, low inflation, strong jobs, non-inverted yield curve
    #   Bad:   Rising rates, high inflation, inverted yield curve, recession signals
    score = 5

    rate_env = fred.get("rate_environment", "neutral")
    if rate_env == "accommodative": score += 2
    elif rate_env == "neutral":     score += 1
    elif rate_env == "restrictive": score -= 1

    infl_status = fred.get("inflation_status")
    if infl_status == "target":    score += 2
    elif infl_status == "low":     score += 1
    elif infl_status == "high":    score -= 2
    elif infl_status == "elevated": score -= 1

    yc = fred.get("yield_curve_10_2", {})
    if yc.get("inverted"):
        score -= 2

    fed_trend = fred.get("fed_funds_rate", {}).get("trend")
    if fed_trend == "falling": score += 1
    elif fed_trend == "rising": score -= 1

    us_gdp = world.get("US", {}).get("gdp_growth", 0)
    if us_gdp and us_gdp > 2.5:  score += 1
    elif us_gdp and us_gdp < 1.0: score -= 1

    score = max(1, min(10, score))

    return {
        "macro_score":       score,
        "fed_funds_rate":    fred.get("fed_funds_rate", {}).get("current"),
        "rate_environment":  rate_env,
        "rate_trend":        fred.get("fed_funds_rate", {}).get("trend"),
        "inflation_yoy":     fred.get("inflation_yoy_pct"),
        "inflation_status":  infl_status,
        "unemployment":      fred.get("unemployment", {}).get("current"),
        "gdp_growth_us":     us_gdp,
        "yield_curve":       yc,
        "treasury_10y":      fred.get("treasury_10y", {}).get("current"),
        "treasury_2y":       fred.get("treasury_2y", {}).get("current"),
        "consumer_sentiment": fred.get("consumer_sentiment", {}).get("current"),
        "global_gdp": {k: v.get("gdp_growth") for k, v in world.items()},
        "taiwan_gdp":        world.get("TW", {}).get("gdp_growth"),  # semiconductor supply chain
        "tech_jobs":         bls.get("tech_jobs", {}).get("value"),
        "fetched_at":        datetime.now().isoformat(),
    }
