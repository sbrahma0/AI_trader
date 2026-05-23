"""
news_sentiment.py -- News, social sentiment, and geopolitical signals.

Sources:
  - NewsAPI  : Financial news headlines per ticker + market-wide (free)
  - pytrends : Google Trends momentum (free, no key)
  - RSS feeds: Geopolitical risk from Reuters, AP, BBC World (free)

NOTE: Reddit (PRAW) was removed -- Reddit now requires manual verification
for API credentials. May be re-added later via a marketplace connector.

Returns scored sentiment per ticker (1-10) and a geopolitical risk index.
"""

import os
import requests
import feedparser
from datetime import datetime, timedelta


# -- SIMPLE KEYWORD SENTIMENT SCORER ------------------------------------------

BULLISH_WORDS = {
    "surge", "soar", "rally", "beat", "record", "breakthrough", "win",
    "growth", "profit", "upgrade", "outperform", "strong", "accelerate",
    "expand", "bullish", "positive", "upside", "buy", "target raised",
    "partnership", "contract", "awarded", "ai", "data center", "demand"
}
BEARISH_WORDS = {
    "plunge", "crash", "fall", "miss", "loss", "downgrade", "underperform",
    "weak", "decline", "cut", "layoff", "warning", "bearish", "negative",
    "lawsuit", "probe", "fine", "tariff", "ban", "risk", "concern", "sell",
    "target cut", "disappointing", "below expectations"
}


def _score_text(text: str) -> float:
    """Score text -1.0 (very bearish) to +1.0 (very bullish)."""
    text_lower = text.lower()
    bull = sum(1 for w in BULLISH_WORDS if w in text_lower)
    bear = sum(1 for w in BEARISH_WORDS if w in text_lower)
    total = bull + bear
    if total == 0:
        return 0.0
    return (bull - bear) / total


def _to_1_10(raw_score: float, bull_bias: float = 0.1) -> int:
    """Convert raw score (-1 to +1) to 1-10 scale, with slight bull bias."""
    adjusted = raw_score + bull_bias
    scaled = (adjusted + 1) / 2 * 9 + 1
    return max(1, min(10, round(scaled)))


# -- NEWSAPI ------------------------------------------------------------------

NEWSAPI_BASE = "https://newsapi.org/v2"


def get_ticker_news(ticker: str, company_name: str = "", days_back: int = 7) -> dict:
    """Fetch and score news headlines for a ticker from NewsAPI."""
    key = os.getenv("NEWS_API_KEY", "")
    if not key:
        return {"ticker": ticker, "articles": [], "sentiment_score": 5, "source": "unavailable"}

    query = ticker if not company_name else f'{ticker} OR "{company_name}"'
    from_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    try:
        r = requests.get(f"{NEWSAPI_BASE}/everything", params={
            "q":        query,
            "from":     from_date,
            "sortBy":   "relevancy",
            "language": "en",
            "pageSize": 20,
            "apiKey":   key,
        }, timeout=10)
        data = r.json()
    except Exception as e:
        print(f"  NewsAPI {ticker}: {e}")
        return {"ticker": ticker, "articles": [], "sentiment_score": 5}

    articles = data.get("articles", [])
    if not articles:
        return {"ticker": ticker, "articles": [], "sentiment_score": 5, "headline_count": 0}

    scores = []
    headlines = []
    for a in articles:
        text = (a.get("title") or "") + " " + (a.get("description") or "")
        scores.append(_score_text(text))
        headlines.append(a.get("title", ""))

    avg_score = sum(scores) / len(scores) if scores else 0.0
    return {
        "ticker":          ticker,
        "articles":        headlines[:10],
        "raw_score":       round(avg_score, 3),
        "sentiment_score": _to_1_10(avg_score),
        "headline_count":  len(articles),
        "source":          "newsapi",
    }


def get_market_news() -> list:
    """Fetch top financial market news (not ticker-specific)."""
    key = os.getenv("NEWS_API_KEY", "")
    if not key:
        return []
    try:
        r = requests.get(f"{NEWSAPI_BASE}/top-headlines", params={
            "category": "business",
            "language": "en",
            "pageSize": 15,
            "apiKey":   key,
        }, timeout=10)
        data = r.json()
        return [{"title": a.get("title", ""), "source": a.get("source", {}).get("name", "")}
                for a in data.get("articles", [])]
    except Exception as e:
        print(f"  NewsAPI market news: {e}")
        return []


# -- REDDIT SENTIMENT ---------------------------------------------------------
# DISABLED: Reddit stopped issuing API credentials without manual verification.
# Returns a neutral placeholder so the rest of the pipeline is unaffected.
# TODO: Re-enable via a marketplace Reddit connector when available.

def get_reddit_sentiment(ticker: str, limit: int = 50) -> dict:
    """Reddit sentiment -- currently disabled pending credential verification."""
    return {"ticker": ticker, "mention_count": 0, "sentiment_score": 5, "source": "disabled"}


# -- GOOGLE TRENDS ------------------------------------------------------------

def get_trends_momentum(ticker: str, company_name: str = "") -> dict:
    """
    Google Trends interest over time.
    Rising search interest often precedes price moves.
    """
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))

        keyword = company_name or ticker
        pytrends.build_payload([keyword], cat=0, timeframe="today 3-m")
        df = pytrends.interest_over_time()

        if df is None or df.empty:
            return {"ticker": ticker, "trend_score": 5}

        values = df[keyword].tolist()
        if len(values) < 4:
            return {"ticker": ticker, "trend_score": 5}

        recent_avg = sum(values[-4:]) / 4
        prior_avg  = sum(values[-12:-4]) / 8 if len(values) >= 12 else sum(values) / len(values)

        if recent_avg > prior_avg * 1.3:
            direction = "rising_strongly"
            score = min(10, 7 + round((recent_avg / prior_avg - 1) * 5))
        elif recent_avg > prior_avg * 1.1:
            direction = "rising"
            score = 7
        elif recent_avg < prior_avg * 0.8:
            direction = "falling"
            score = 4
        else:
            direction = "stable"
            score = 5

        return {
            "ticker":           ticker,
            "current_interest": int(values[-1]),
            "avg_interest":     round(sum(values) / len(values), 1),
            "trend_direction":  direction,
            "trend_score":      score,
        }
    except Exception as e:
        print(f"  Google Trends {ticker}: {e}")
        return {"ticker": ticker, "trend_score": 5}


# -- GEOPOLITICAL RISK (RSS) --------------------------------------------------

GEO_RSS_FEEDS = [
    ("Reuters World", "https://feeds.reuters.com/reuters/worldNews"),
    ("AP Top News",   "https://feeds.apnews.com/rss/apf-topnews"),
    ("BBC World",     "https://feeds.bbci.co.uk/news/world/rss.xml"),
]

GEO_RISK_KEYWORDS = {
    "high": [
        "war", "invasion", "nuclear", "sanctions", "tariff", "trade war",
        "conflict", "missile", "attack", "crisis", "emergency", "shutdown",
        "default", "collapse", "recession"
    ],
    "medium": [
        "tension", "dispute", "protest", "strike", "election", "concern",
        "inflation", "rate hike", "slowdown", "warning", "threat", "ban"
    ],
    "low": [
        "deal", "agreement", "ceasefire", "recovery", "growth",
        "cooperation", "peace", "trade deal"
    ]
}


def get_geopolitical_risk() -> dict:
    """
    Parse geopolitical RSS feeds and compute a risk index (1=low risk, 10=high risk).
    """
    headlines = []
    for name, url in GEO_RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                headlines.append({
                    "source":  name,
                    "title":   entry.get("title", ""),
                    "summary": entry.get("summary", "")[:200],
                })
        except Exception as e:
            print(f"  RSS {name}: {e}")

    if not headlines:
        return {"risk_index": 5, "top_headlines": [], "risk_level": "medium"}

    risk_points = 0
    for h in headlines:
        text = (h["title"] + " " + h["summary"]).lower()
        for kw in GEO_RISK_KEYWORDS["high"]:
            if kw in text:
                risk_points += 3
        for kw in GEO_RISK_KEYWORDS["medium"]:
            if kw in text:
                risk_points += 1
        for kw in GEO_RISK_KEYWORDS["low"]:
            if kw in text:
                risk_points -= 1

    risk_index = max(1, min(10, round(1 + (risk_points / 30) * 9)))
    risk_level = "high" if risk_index >= 7 else "medium" if risk_index >= 4 else "low"

    return {
        "risk_index":    risk_index,
        "risk_level":    risk_level,
        "top_headlines": [h["title"] for h in headlines[:5]],
        "fetched_at":    datetime.now().isoformat(),
    }


# -- COMBINED SENTIMENT SNAPSHOT ----------------------------------------------

def get_full_sentiment(ticker: str, company_name: str = "") -> dict:
    """
    Combine news + Google Trends into a single sentiment snapshot.
    Weights: news 65%, Google Trends 35%.
    (Reddit disabled -- will re-add via marketplace connector.)
    """
    news   = get_ticker_news(ticker, company_name)
    trends = get_trends_momentum(ticker, company_name)

    news_s   = news.get("sentiment_score", 5)
    trends_s = trends.get("trend_score", 5)

    combined = round(news_s * 0.65 + trends_s * 0.35)

    return {
        "ticker":          ticker,
        "sentiment_score": max(1, min(10, combined)),
        "news_score":      news_s,
        "reddit_score":    5,
        "trends_score":    trends_s,
        "mention_count":   0,
        "top_headlines":   news.get("articles", [])[:3],
        "trend_direction": trends.get("trend_direction", "stable"),
    }
