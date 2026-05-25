"""
pipeline.py — Multi-agent orchestrator.

Execution flow (2 parallel stages + 1 sequential):
  Stage A (parallel): Momentum Agent + Macro/Geo Agent
  Stage B (parallel): Short-term Scanner + Long-term Analyst  (filtered from Stage A)
  Stage C:            Synthesis Agent  (consumes all prior outputs)
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from .momentum  import run_momentum_agent
from .macro_geo import run_macro_agent
from .short_term import run_short_term_agent
from .long_term  import run_long_term_agent
from .synthesis  import run_synthesis_agent


def _resolve_tickers(data: dict, scope: str, tickers_override: list | None) -> tuple[list, set]:
    """
    Returns (tickers_list, portfolio_tickers_set) based on scope.
    """
    portfolio_tickers = set(data.get("portfolio_tickers", []))
    watchlist_tickers = {w["ticker"] for w in data.get("watchlist", [])}
    all_tickers       = list(data.get("stocks", {}).keys())

    if tickers_override:
        tickers = [t.upper() for t in tickers_override if t.upper() in data.get("stocks", {})]
    elif scope == "portfolio_only":
        tickers = [t for t in all_tickers if t in portfolio_tickers]
    elif scope == "watchlist_only":
        tickers = [t for t in all_tickers if t in watchlist_tickers]
    elif scope in ("full", "momentum_scan"):
        tickers = all_tickers
    else:
        tickers = all_tickers

    return tickers, portfolio_tickers


def run_pipeline(
    data: dict,
    scope: str = "full",
    tickers_override: list | None = None,
) -> list:
    """
    Run the full multi-agent pipeline and return a list of FinalRecommendation dicts.

    Args:
        data:             Output of collect_all_data() — stocks, macro, geo, market, etc.
        scope:            'full' | 'portfolio_only' | 'watchlist_only' | 'momentum_scan' | 'tickers'
        tickers_override: Explicit ticker list (used when scope='tickers' or ad-hoc research)
    """
    tickers, portfolio_tickers = _resolve_tickers(data, scope, tickers_override)
    print(f"\n[Pipeline] scope={scope}  tickers={len(tickers)}  portfolio={len(portfolio_tickers)}")

    stocks   = data.get("stocks", {})
    macro    = data.get("macro", {})
    geo      = data.get("geo", {})
    market   = data.get("market", {})
    holdings = data.get("holdings", [])
    profile  = data.get("profile", {})

    # ── Stage A: Momentum + Macro in parallel ────────────────────────────────
    print("\n[Pipeline] Stage A — Momentum + Macro/Geo (parallel)")
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_momentum = pool.submit(run_momentum_agent, {t: stocks[t] for t in tickers if t in stocks})
        f_macro    = pool.submit(run_macro_agent, macro, geo, market)
        momentum_outputs = f_momentum.result()
        macro_result     = f_macro.result()

    print(f"  momentum signals: {len(momentum_outputs)}  |  regime: {macro_result.get('market_regime','?')}")

    # ── Python filter for Stage B ─────────────────────────────────────────────
    eligible_for_st = [
        t for t in tickers
        if (momentum_outputs.get(t, {}).get("momentum_score", 0) >= 7
            or momentum_outputs.get(t, {}).get("has_near_catalyst", False))
    ]
    print(f"  short-term eligible: {len(eligible_for_st)}  {eligible_for_st[:10]}")

    # ── Stage B: Short-term + Long-term (parallel, unless momentum_scan) ─────
    print("\n[Pipeline] Stage B — Short-term + Long-term (parallel)")
    short_term_plays    = []
    long_term_convictions = []

    if scope == "momentum_scan":
        # Long-term agent skipped for speed
        short_term_plays = run_short_term_agent(eligible_for_st, momentum_outputs, macro_result, stocks)
    else:
        lt_tickers = tickers  # long-term covers full scope
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_st = pool.submit(run_short_term_agent, eligible_for_st, momentum_outputs, macro_result, stocks)
            f_lt = pool.submit(run_long_term_agent, lt_tickers, macro_result, stocks, holdings, profile)
            short_term_plays      = f_st.result()
            long_term_convictions = f_lt.result()

    print(f"  short-term plays: {len(short_term_plays)}  |  long-term convictions: {len(long_term_convictions)}")

    # ── Stage C: Synthesis ───────────────────────────────────────────────────
    print("\n[Pipeline] Stage C — Synthesis")

    # For momentum_scan: only synthesize tickers that got a short-term play
    if scope == "momentum_scan":
        synthesis_tickers = [p["ticker"] for p in short_term_plays]
    else:
        synthesis_tickers = tickers

    from core.database import get_action_log
    action_log = get_action_log(limit=20)

    final_recs = run_synthesis_agent(
        tickers          = synthesis_tickers,
        momentum_outputs = momentum_outputs,
        macro_result     = macro_result,
        short_term_plays = short_term_plays,
        long_term_convictions = long_term_convictions,
        stocks           = stocks,
        holdings         = holdings,
        profile          = profile,
        action_log       = action_log,
        portfolio_tickers = portfolio_tickers,
    )

    print(f"\n[Pipeline] Complete — {len(final_recs)} recommendations")
    return final_recs
