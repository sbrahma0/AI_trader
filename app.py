"""
app.py — AI Trader (Streamlit)

4 tabs:
  1. Portfolio     — sector buckets, broker accounts, refresh prices
  2. Look Out      — individual watchlist + sector-level tracking
  3. Research      — on-demand stock / sector deep-dive
  4. Recommendations — sector spotlight, horizon tabs, AI picks

Run:  streamlit run app.py
"""

import streamlit as st
import json
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# ── PAGE CONFIG ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Trader",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={"About": "AI-powered trading — personal use only."}
)

import sys
sys.path.insert(0, str(Path(__file__).parent))

from core.database import (
    init_db, get_portfolio, get_latest_recommendations, get_action_log,
    log_action, upsert_holding, get_user_profile, update_user_profile,
    get_watchlist, snapshot_portfolio, get_broker_accounts, upsert_broker_account,
    delete_broker_account, get_sector_watchlist, upsert_sector,
    get_sector_top_picks, get_research_result, save_research_result,
    refresh_portfolio_prices,
)
from core.portfolio_parser import parse_file

init_db()

# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@media (max-width: 768px) {
    .block-container { padding: 1rem 0.75rem !important; }
    .stColumn { padding: 0.25rem !important; }
}
.rec-card {
    border-radius: 12px; padding: 14px 16px; margin-bottom: 10px;
    border-left: 5px solid #ccc;
}
.rec-card.strong-buy  { background: #f0fdf4; border-color: #16a34a; }
.rec-card.buy         { background: #f7fee7; border-color: #65a30d; }
.rec-card.hold        { background: #fefce8; border-color: #ca8a04; }
.rec-card.sell        { background: #fff7ed; border-color: #ea580c; }
.rec-card.strong-sell { background: #fff1f2; border-color: #e11d48; }
.rec-card.new-pick    { background: #eff6ff; border-color: #2563eb; }

.action-badge {
    font-size: 13px; font-weight: 700; padding: 3px 10px;
    border-radius: 20px; display: inline-block;
}
.badge-strong-buy  { background: #16a34a; color: white; }
.badge-buy         { background: #65a30d; color: white; }
.badge-hold        { background: #ca8a04; color: white; }
.badge-sell        { background: #ea580c; color: white; }
.badge-strong-sell { background: #e11d48; color: white; }
.badge-new-pick    { background: #2563eb; color: white; }

.horizon-badge {
    font-size: 11px; font-weight: 600; padding: 2px 8px;
    border-radius: 12px; display: inline-block; margin-left: 6px;
}
.horizon-short  { background: #fef3c7; color: #92400e; }
.horizon-medium { background: #dbeafe; color: #1e40af; }
.horizon-long   { background: #f3e8ff; color: #6b21a8; }

.play-badge {
    font-size: 11px; font-weight: 600; padding: 2px 8px;
    border-radius: 12px; display: inline-block; margin-left: 4px;
    background: #f1f5f9; color: #475569;
}

.metric-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 6px; }
.metric-chip {
    background: #f1f5f9; border-radius: 8px;
    padding: 4px 10px; font-size: 12px; color: #334155;
}
.pnl-pos { color: #16a34a; font-weight: 700; }
.pnl-neg { color: #dc2626; font-weight: 700; }
.section-header {
    font-size: 18px; font-weight: 700; margin: 16px 0 8px;
    padding-bottom: 6px; border-bottom: 2px solid #e2e8f0;
}
.score-bar-bg  { background: #e2e8f0; border-radius: 4px; height: 6px; margin: 2px 0 6px; }
.score-bar-fill { border-radius: 4px; height: 6px; }

.sector-card {
    background: #f8fafc; border-radius: 10px; padding: 12px 14px;
    margin-bottom: 8px; border: 1px solid #e2e8f0;
}
.macro-badge-green  { background: #dcfce7; color: #166534; border-radius: 8px; padding: 2px 8px; font-size: 12px; }
.macro-badge-yellow { background: #fef9c3; color: #713f12; border-radius: 8px; padding: 2px 8px; font-size: 12px; }
.macro-badge-red    { background: #fee2e2; color: #991b1b; border-radius: 8px; padding: 2px 8px; font-size: 12px; }
</style>
""", unsafe_allow_html=True)


# ── HELPERS ────────────────────────────────────────────────────────────────────

ACTION_LABELS = {
    "strong_buy":  ("⬆️⬆️ STRONG BUY",  "badge-strong-buy",  "strong-buy"),
    "buy":         ("⬆️ BUY",            "badge-buy",         "buy"),
    "hold":        ("⏸ HOLD",            "badge-hold",        "hold"),
    "sell":        ("⬇️ SELL",            "badge-sell",        "sell"),
    "strong_sell": ("⬇️⬇️ STRONG SELL",  "badge-strong-sell", "strong-sell"),
    "new_pick":    ("✨ NEW PICK",        "badge-new-pick",    "new-pick"),
}

HORIZON_LABELS = {
    "short":  ("Short-term", "horizon-short"),
    "medium": ("Medium",     "horizon-medium"),
    "long":   ("Long-term",  "horizon-long"),
}

SECTOR_BUCKETS = [
    "AI Infrastructure", "Quantum Computing", "Space",
    "Clean Energy", "Nuclear Energy", "Semiconductors",
    "Software", "Crypto", "Other",
]

def fmt_price(v):
    return f"${v:,.2f}" if v is not None else "—"

def fmt_pct(v):
    if v is None: return "—"
    sign = "+" if v >= 0 else ""
    cls  = "pnl-pos" if v >= 0 else "pnl-neg"
    return f'<span class="{cls}">{sign}{v:.1f}%</span>'

def score_bar(score, color="#3b82f6"):
    if score is None: return ""
    pct = min(100, max(0, (score or 0) * 10))
    return (f'<div class="score-bar-bg">'
            f'<div class="score-bar-fill" style="width:{pct}%;background:{color};"></div>'
            f'</div>')

def confidence_color(c):
    if c is None: return "#94a3b8"
    if c >= 8:  return "#16a34a"
    if c >= 6:  return "#ca8a04"
    return "#dc2626"

def macro_badge(score):
    if score is None: return ""
    if score >= 7:   return f'<span class="macro-badge-green">Macro ▲</span>'
    if score >= 4:   return f'<span class="macro-badge-yellow">Macro ~</span>'
    return f'<span class="macro-badge-red">Macro ▼</span>'

def render_rec_card(r):
    """Render a single recommendation card (shared by Recommendations tab)."""
    action    = r.get("action", "hold")
    label, badge_cls, card_cls = ACTION_LABELS.get(action, ("HOLD", "badge-hold", "hold"))
    conf      = r.get("confidence")
    horizon   = r.get("horizon", "medium")
    play_type = r.get("play_type", "core")
    h_label, h_cls = HORIZON_LABELS.get(horizon, ("Medium", "horizon-medium"))
    thesis_raw = r.get("thesis", "")
    # Cap thesis at 3 sentences
    sentences = [s.strip() for s in thesis_raw.replace("\n", " ").split(".") if s.strip()]
    thesis    = ". ".join(sentences[:3]) + ("." if sentences else "")

    st.markdown(f"""
    <div class="rec-card {card_cls}">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:6px;">
        <div>
          <strong style="font-size:16px;">{r.get('ticker','')}</strong>
          <span style="color:#64748b; margin-left:6px; font-size:13px;">{r.get('company_name','')}</span>
        </div>
        <div>
          <span class="action-badge {badge_cls}">{label}</span>
          <span class="horizon-badge {h_cls}">{h_label}</span>
          <span class="play-badge">{play_type}</span>
        </div>
      </div>
      <div class="metric-row" style="margin-top:8px;">
        <span class="metric-chip">Price {fmt_price(r.get('current_price'))}</span>
        <span class="metric-chip">Confidence <strong style="color:{confidence_color(conf)};">{conf}/10</strong></span>
        <span class="metric-chip">Buy {fmt_price(r.get('buy_range_low'))}–{fmt_price(r.get('buy_range_high'))}</span>
        <span class="metric-chip">Sell {fmt_price(r.get('sell_target_low'))}–{fmt_price(r.get('sell_target_high'))}</span>
        {f'<span class="metric-chip">Stop {fmt_price(r.get("stop_loss"))}</span>' if r.get("stop_loss") else ""}
        <span class="metric-chip">⏱ {r.get('growth_timeline','—')}</span>
        {f'<span class="metric-chip">+{r.get("growth_potential_pct"):.0f}% upside</span>' if r.get("growth_potential_pct") else ""}
      </div>
      <p style="margin:8px 0 0; font-size:13px; color:#475569; line-height:1.5;">{thesis}</p>
    </div>
    """, unsafe_allow_html=True)

    with st.expander("Scores · Risks · Catalysts"):
        c1, c2, c3 = st.columns(3)
        with c1:
            st.caption("Technical")
            st.markdown(score_bar(r.get("technical_score"), "#3b82f6"), unsafe_allow_html=True)
            st.caption(f"{r.get('technical_score','?')}/10")
        with c2:
            st.caption("Sentiment")
            st.markdown(score_bar(r.get("sentiment_score"), "#8b5cf6"), unsafe_allow_html=True)
            st.caption(f"{r.get('sentiment_score','?')}/10")
        with c3:
            st.caption("Macro")
            st.markdown(score_bar(r.get("macro_score"), "#f59e0b"), unsafe_allow_html=True)
            st.caption(f"{r.get('macro_score','?')}/10")

        risks = r.get("risks", "[]")
        if isinstance(risks, str):
            try: risks = json.loads(risks)
            except: risks = []
        catalysts = r.get("catalysts", "[]")
        if isinstance(catalysts, str):
            try: catalysts = json.loads(catalysts)
            except: catalysts = []

        if risks:
            st.markdown("**Risks:** " + " · ".join(f"⚠️ {x}" for x in risks[:3]))
        if catalysts:
            st.markdown("**Catalysts:** " + " · ".join(f"🚀 {x}" for x in catalysts[:3]))

        if st.button("✅ I followed this", key=f"follow_{r.get('id',r.get('ticker'))}"):
            st.session_state["journal_prefill"] = r
            st.rerun()


# ── TABS ───────────────────────────────────────────────────────────────────────
tab_portfolio, tab_lookout, tab_research, tab_recs = st.tabs([
    "💼 Portfolio", "👁 Look Out", "🔍 Research", "📊 Recommendations"
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — PORTFOLIO
# ═══════════════════════════════════════════════════════════════════════════════
with tab_portfolio:
    st.subheader("Current Portfolio")

    # ── Top-level metrics ─────────────────────────────────────────────────────
    holdings  = get_portfolio()
    accounts  = get_broker_accounts()
    total_cost  = sum((h.get("avg_cost_basis") or 0) * (h.get("shares") or 0) for h in holdings)
    total_val   = sum((h.get("current_price") or h.get("avg_cost_basis") or 0) * (h.get("shares") or 0) for h in holdings)
    total_pnl   = total_val - total_cost
    total_cash  = sum(a.get("cash_balance") or 0 for a in accounts)
    today_delta = 0  # would need yesterday's snapshot; shown as placeholder

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Portfolio Value",   f"${total_val:,.0f}")
    col2.metric("Total Cost Basis",  f"${total_cost:,.0f}")
    col3.metric("Unrealized P&L",    f"${total_pnl:+,.0f}", delta=f"{(total_pnl/total_cost*100) if total_cost else 0:+.1f}%")
    col4.metric("Cash (all brokers)", f"${total_cash:,.0f}")
    col5.metric("Total Capital",     f"${total_val + total_cash:,.0f}")

    # ── Refresh button ────────────────────────────────────────────────────────
    col_r1, col_r2 = st.columns([1, 4])
    with col_r1:
        if st.button("🔄 Refresh Prices", use_container_width=True):
            with st.spinner("Fetching latest prices…"):
                updated = refresh_portfolio_prices()
            st.success(f"Updated {updated} tickers")
            st.rerun()

    st.divider()

    # ── Sector buckets ────────────────────────────────────────────────────────
    if holdings:
        # Group by sector
        by_sector: dict[str, list] = {}
        for h in holdings:
            sector = h.get("sector") or "Other"
            # Normalize to known bucket or "Other"
            bucket = sector if sector in SECTOR_BUCKETS else "Other"
            by_sector.setdefault(bucket, []).append(h)

        for bucket in SECTOR_BUCKETS:
            items = by_sector.get(bucket, [])
            if not items:
                continue
            b_cost = sum((h.get("avg_cost_basis") or 0) * (h.get("shares") or 0) for h in items)
            b_val  = sum((h.get("current_price") or h.get("avg_cost_basis") or 0) * (h.get("shares") or 0) for h in items)
            b_pnl  = b_val - b_cost
            b_pct  = (b_pnl / b_cost * 100) if b_cost else 0

            with st.expander(f"**{bucket}** — {len(items)} stock{'s' if len(items) != 1 else ''}  |  ${b_val:,.0f} current  |  {b_pct:+.1f}%", expanded=True):
                for h in items:
                    cost  = h.get("avg_cost_basis") or 0
                    price = h.get("current_price") or cost
                    pnl_d = (price - cost) * (h.get("shares") or 0)
                    pnl_p = (price - cost) / cost * 100 if cost else 0
                    c1, c2, c3, c4, c5, c6 = st.columns([1.5, 2, 2, 2, 2, 2])
                    c1.markdown(f"**{h['ticker']}**")
                    c2.markdown(f"{h.get('shares',0):.2f} sh")
                    c3.markdown(f"Avg {fmt_price(cost)}")
                    c4.markdown(f"Now {fmt_price(price)}")
                    c5.markdown(f"${pnl_d:+,.0f}", unsafe_allow_html=True)
                    c6.markdown(fmt_pct(pnl_p), unsafe_allow_html=True)
    else:
        st.info("No holdings yet. Import a brokerage statement or add manually below.")

    st.divider()

    # ── Import / Add holdings ─────────────────────────────────────────────────
    with st.expander("📥 Import or Add Holdings"):
        uploaded = st.file_uploader("Upload brokerage statement (PDF or CSV)", type=["pdf", "csv"])
        if uploaded:
            with st.spinner("Parsing statement…"):
                parsed = parse_file(uploaded)
            if parsed:
                st.success(f"Found {len(parsed)} positions")
                for pos in parsed:
                    st.write(f"  {pos.get('ticker')} — {pos.get('shares')} shares @ ${pos.get('avg_cost',0):.2f}")
                if st.button("✅ Import these holdings"):
                    for pos in parsed:
                        upsert_holding(pos["ticker"], pos["shares"], pos.get("avg_cost", 0),
                                       company_name=pos.get("company_name"))
                    snapshot_portfolio(source="pdf_import")
                    st.success("Holdings imported.")
                    st.rerun()
            else:
                st.warning("Could not parse any positions from this file.")

        st.markdown("**Add manually:**")
        with st.form("add_holding"):
            col_a, col_b, col_c, col_d, col_e = st.columns([1, 1.5, 1.5, 1.5, 2])
            ticker  = col_a.text_input("Ticker").upper()
            shares  = col_b.number_input("Shares", min_value=0.0, step=0.01)
            cost    = col_c.number_input("Avg Cost $", min_value=0.0, step=0.01)
            sector  = col_d.selectbox("Sector", SECTOR_BUCKETS)
            company = col_e.text_input("Company Name (optional)")
            if st.form_submit_button("Add Holding") and ticker and shares > 0 and cost > 0:
                upsert_holding(ticker, shares, cost, company_name=company or None, sector=sector)
                st.success(f"Added {ticker}")
                st.rerun()

    st.divider()

    # ── Broker Accounts ───────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Broker Accounts & Cash</div>', unsafe_allow_html=True)

    if accounts:
        for acct in accounts:
            col_a, col_b, col_c, col_d, col_e = st.columns([2, 1.5, 1.5, 1.5, 1])
            col_a.markdown(f"**{acct['broker_name']}** — *{acct['account_type']}*")
            col_b.markdown(f"Cash: {fmt_price(acct.get('cash_balance'))}")
            col_c.markdown(f"Total: {fmt_price(acct.get('total_value'))}")
            col_d.caption(f"Updated {str(acct.get('updated_at',''))[:10]}")
            if col_e.button("🗑", key=f"del_acct_{acct['id']}"):
                delete_broker_account(acct["id"])
                st.rerun()
    else:
        st.caption("No broker accounts added yet.")

    with st.expander("➕ Add Broker Account"):
        with st.form("add_broker"):
            col_a, col_b, col_c, col_d = st.columns([2, 1.5, 1.5, 1.5])
            broker_name  = col_a.text_input("Broker Name (e.g. Robinhood)")
            account_type = col_b.selectbox("Account Type", ["taxable", "ira", "roth_ira", "401k", "savings"])
            cash_balance = col_c.number_input("Cash Balance $", min_value=0.0, step=1.0)
            total_value  = col_d.number_input("Total Account Value $", min_value=0.0, step=1.0)
            if st.form_submit_button("Add Account") and broker_name:
                upsert_broker_account(broker_name, account_type, cash_balance, total_value)
                st.success(f"Added {broker_name}")
                st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — LOOK OUT STOCKS
# ═══════════════════════════════════════════════════════════════════════════════
with tab_lookout:
    st.subheader("Look Out Stocks")

    recs_map = {r["ticker"]: r for r in get_latest_recommendations()}

    # ── A. Individual Stock Watchlist ─────────────────────────────────────────
    st.markdown('<div class="section-header">Individual Stocks</div>', unsafe_allow_html=True)

    watchlist = get_watchlist()
    if watchlist:
        by_sector_w: dict[str, list] = {}
        for w in watchlist:
            by_sector_w.setdefault(w.get("sector") or "Other", []).append(w)

        for sector, items in sorted(by_sector_w.items()):
            st.markdown(f"**{sector}**")
            for w in items:
                ticker  = w["ticker"]
                rec     = recs_map.get(ticker)
                col_a, col_b, col_c, col_d, col_e, col_f = st.columns([1.2, 2, 1.8, 2, 2.5, 1])
                col_a.markdown(f"**{ticker}**")
                col_b.markdown(w.get("company_name") or "")

                if rec:
                    action  = rec.get("action", "hold")
                    label, badge_cls, _ = ACTION_LABELS.get(action, ("HOLD", "badge-hold", "hold"))
                    ms = rec.get("macro_score") or 0
                    run_at  = str(rec.get("run_at", ""))[:16]
                    col_c.markdown(f'<span class="action-badge {badge_cls}" style="font-size:11px;">{label}</span>', unsafe_allow_html=True)
                    col_d.markdown(f"Buy {fmt_price(rec.get('buy_range_low'))}–{fmt_price(rec.get('buy_range_high'))}")
                    col_e.markdown(macro_badge(ms) + f" Sell {fmt_price(rec.get('sell_target_low'))}–{fmt_price(rec.get('sell_target_high'))}" + (f" <span style='color:#94a3b8;font-size:11px;'>({run_at})</span>" if run_at else ""), unsafe_allow_html=True)
                else:
                    col_c.caption("Not yet analyzed")
                    col_d.markdown("")
                    col_e.markdown("")

                if col_f.button("↻", key=f"refresh_wl_{ticker}", help=f"Re-run analysis for {ticker}"):
                    with st.spinner(f"Analyzing {ticker}…"):
                        try:
                            from core.collectors.market_data    import get_full_snapshot, get_market_overview
                            from core.collectors.news_sentiment import get_full_sentiment, get_geopolitical_risk
                            from core.collectors.macro_data     import get_macro_summary
                            from core.agents.base               import call_agent
                            from core.agents.pipeline           import run_pipeline
                            from core.analyzer                  import save_run, write_results_json
                            from core.database                  import get_user_profile, get_portfolio, get_watchlist

                            snapshot  = get_full_snapshot(ticker)
                            sentiment = get_full_sentiment(ticker, snapshot.get("company_name", ""))
                            macro     = get_macro_summary()
                            geo       = get_geopolitical_risk()
                            market    = get_market_overview()
                            profile   = get_user_profile()
                            holdings  = get_portfolio()
                            watchlist_data = get_watchlist()

                            data = {
                                "profile":           profile,
                                "holdings":          holdings,
                                "watchlist":         watchlist_data,
                                "portfolio_tickers": [h["ticker"] for h in holdings],
                                "new_pick_tickers":  [],
                                "market":            market,
                                "macro":             macro,
                                "geo":               geo,
                                "stocks":            {ticker: {**snapshot, "sentiment": sentiment}},
                                "trigger":           "manual",
                                "run_at":            datetime.now().isoformat(),
                            }
                            recs = run_pipeline(data, scope="full", tickers_override=[ticker])
                            save_run(data, recs)
                            write_results_json(recs, data)
                            st.success(f"{ticker} updated")
                            st.rerun()
                        except Exception as ex:
                            st.error(f"Failed: {ex}")
            st.markdown("")
    else:
        st.info("No stocks in watchlist.")

    with st.expander("➕ Add Stock to Watchlist"):
        with st.form("add_watchlist"):
            col_a, col_b, col_c = st.columns([1, 2, 3])
            wl_ticker  = col_a.text_input("Ticker").upper()
            wl_company = col_b.text_input("Company Name")
            wl_sector  = col_c.selectbox("Sector", SECTOR_BUCKETS)
            wl_reason  = st.text_input("Why watching (optional)")
            if st.form_submit_button("Add") and wl_ticker:
                from core.database import get_connection
                with get_connection() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO watchlist (ticker, company_name, sector, why_watching) VALUES (?,?,?,?)",
                        (wl_ticker, wl_company, wl_sector, wl_reason)
                    )
                st.success(f"Added {wl_ticker}")
                st.rerun()

    st.divider()

    # ── B. Sector Watch ───────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Sector Watch</div>', unsafe_allow_html=True)

    sectors_data = get_sector_watchlist()
    sector_picks = get_sector_top_picks(n=3)

    if sectors_data:
        for s in sectors_data:
            sname        = s["sector_name"]
            stocks       = s.get("key_stocks") or []
            events       = s.get("key_events") or []
            picks        = sector_picks.get(sname, [])
            last_analyzed = str(s.get("last_analyzed") or "")[:16]
            expander_label = (
                f"**{sname}** — {len(stocks)} stocks tracked"
                + (f"  ·  last refreshed {last_analyzed}" if last_analyzed else "  ·  never refreshed")
            )

            with st.expander(expander_label, expanded=True):
                col_left, col_right = st.columns([3, 2])
                with col_left:
                    st.caption(s.get("why_watching") or "")
                    if events:
                        st.markdown("**Key Events:**")
                        for ev in events[:4]:
                            st.markdown(f"  • {ev}")
                    if stocks:
                        st.markdown(f"**Tracking:** {', '.join(stocks)}")

                with col_right:
                    if picks:
                        st.caption("Top picks this run:")
                        for p in picks:
                            action   = p.get("action", "hold")
                            _, badge, _ = ACTION_LABELS.get(action, ("HOLD", "badge-hold", "hold"))
                            upside   = p.get("growth_potential_pct") or 0
                            st.markdown(
                                f'<span class="action-badge {badge}" style="font-size:11px;">{p["ticker"]}</span>'
                                f' {p.get("company_name","")[:20]}  '
                                f'<span style="color:#16a34a;font-weight:700;">+{upside:.0f}%</span>',
                                unsafe_allow_html=True
                            )
                    else:
                        st.caption("Run analysis to see sector picks")

                if st.button(f"↻ Refresh {sname}", key=f"refresh_sector_{sname}"):
                    with st.spinner(f"Researching {sname} sector…"):
                        try:
                            from core.collectors.macro_data     import get_macro_summary
                            from core.collectors.news_sentiment import get_geopolitical_risk
                            from core.collectors.market_data    import get_market_overview
                            from core.agents.base               import call_agent
                            from core.database                  import get_connection
                            _macro  = get_macro_summary()
                            _geo    = get_geopolitical_risk()
                            _market = get_market_overview()
                            _prompt = f"""You are an equity research analyst. Sector: {sname}
MACRO: Fed {_macro.get('fed_funds_rate','?')}%, Inflation {_macro.get('inflation_yoy','?')}%
Geo risk: {_geo.get('risk_index',5)}/10  Market: {_market.get('_meta',{}).get('overall_sentiment','neutral')}
Return JSON: {{"sector":"{sname}","summary":"...","tailwinds":[],"headwinds":[],"key_public_companies":[{{"ticker":"","reason":""}}],"upcoming_catalysts":[],"investment_verdict":"bullish|neutral|bearish","vs_benchmark":"..."}}"""
                            res = call_agent("claude-sonnet-4-6", _prompt, 1500, f"SectorRefresh/{sname}")
                            save_research_result(sname, "sector", res)
                            with get_connection() as _conn:
                                _conn.execute(
                                    "UPDATE sector_watchlist SET last_analyzed=CURRENT_TIMESTAMP WHERE sector_name=?",
                                    (sname,)
                                )
                            st.success(f"{sname} refreshed")
                            st.rerun()
                        except Exception as ex:
                            st.error(f"Failed: {ex}")
    else:
        st.info("No sectors added to watchlist yet.")

    with st.expander("➕ Add Sector to Watch"):
        with st.form("add_sector"):
            sec_name   = st.text_input("Sector Name (e.g. Space, Biotech)")
            sec_reason = st.text_area("Why watching", height=60)
            sec_stocks = st.text_input("Key stock tickers to track (comma-separated)")
            sec_events = st.text_area("Key events to watch (one per line)", height=80)

            if st.form_submit_button("Add Sector") and sec_name:
                stocks_list = [t.strip().upper() for t in sec_stocks.split(",") if t.strip()]
                events_list = [e.strip() for e in sec_events.split("\n") if e.strip()]
                upsert_sector(sec_name, sec_reason, stocks_list, events_list)

                # Auto-suggest stocks via Claude if stocks list is empty
                if not stocks_list:
                    st.info("Tip: run a Research on this sector to get Claude's top stock suggestions for it.")
                st.success(f"Added sector: {sec_name}")
                st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — RESEARCH
# ═══════════════════════════════════════════════════════════════════════════════
with tab_research:
    st.subheader("Research")
    st.caption("On-demand deep-dive on any stock or sector. Results cached for 6 hours.")

    col_inp, col_btn, col_force = st.columns([3, 1, 1])
    research_subject = col_inp.text_input("Ticker or sector name", placeholder="e.g. NVDA or Space").strip().upper()
    run_research     = col_btn.button("🔍 Research", use_container_width=True)
    force_fresh      = col_force.checkbox("Force refresh", value=False)

    if research_subject and run_research:
        cached = None if force_fresh else get_research_result(research_subject, max_age_hours=6)

        if cached:
            st.success(f"Showing cached result from {str(cached.get('created_at',''))[:16]}")
            result = cached.get("research", {})
        else:
            with st.spinner(f"Researching {research_subject}…"):
                try:
                    from core.collectors.market_data    import get_full_snapshot, get_market_overview
                    from core.collectors.news_sentiment import get_full_sentiment, get_geopolitical_risk
                    from core.collectors.macro_data     import get_macro_summary
                    from core.agents.base               import call_agent

                    # Determine if sector or stock
                    known_sectors = {s["sector_name"].upper() for s in get_sector_watchlist()}
                    is_sector = (research_subject in known_sectors
                                 or research_subject in [b.upper() for b in SECTOR_BUCKETS]
                                 or len(research_subject) > 6)

                    if is_sector:
                        # Sector research
                        macro = get_macro_summary()
                        geo   = get_geopolitical_risk()
                        market = get_market_overview()
                        prompt = f"""You are an equity research analyst. Produce a structured sector research report.

SECTOR: {research_subject}

MACRO: Fed Funds {macro.get('fed_funds_rate','?')}%, Inflation {macro.get('inflation_yoy','?')}%, GDP {macro.get('gdp_growth_us','?')}%
Geo Risk: {geo.get('risk_index',5)}/10 ({geo.get('risk_level','medium')})
Market Sentiment: {market.get('_meta',{}).get('overall_sentiment','neutral')}

Return this JSON:
{{
  "sector": "{research_subject}",
  "summary": "<2-3 sentences on current sector state>",
  "macro_alignment": "positive" | "neutral" | "negative",
  "macro_note": "<one sentence on macro impact>",
  "tailwinds": ["tailwind1", "tailwind2", "tailwind3"],
  "headwinds": ["headwind1", "headwind2"],
  "key_public_companies": [
    {{"ticker": "XX", "company": "...", "reason": "why this is the key player"}}
  ],
  "upcoming_catalysts": ["catalyst1", "catalyst2"],
  "investment_verdict": "bullish" | "neutral" | "bearish",
  "verdict_reason": "<one sentence>",
  "vs_benchmark": "<one sentence on expected performance vs SPY/QQQ>"
}}

Return ONLY the JSON object."""
                        result = call_agent("claude-sonnet-4-6", prompt, 2000, "Research/Sector")
                        subject_type = "sector"

                    else:
                        # Stock research
                        snapshot  = get_full_snapshot(research_subject)
                        sentiment = get_full_sentiment(research_subject, snapshot.get("company_name", ""))
                        macro     = get_macro_summary()
                        geo       = get_geopolitical_risk()
                        sent      = sentiment or {}

                        _cp  = snapshot.get('current_price') or 0
                        _chg = snapshot.get('change_pct_today') or 0
                        _lo  = snapshot.get('low_52w') or 0
                        _hi  = snapshot.get('high_52w') or 0
                        _at  = snapshot.get('analyst_target') or 0
                        _rsi = snapshot.get('rsi') or 50
                        _mcd = snapshot.get('macd_histogram') or 0
                        _s20 = snapshot.get('sma20') or 0
                        _s50 = snapshot.get('sma50') or 0
                        _s200= snapshot.get('sma200') or 0
                        _ts  = snapshot.get('technical_score') or 5
                        prompt = f"""You are an elite equity analyst. Produce a comprehensive stock research report.

STOCK: {research_subject} — {snapshot.get('company_name','')} ({snapshot.get('sector','')})
Price: ${_cp:.2f}  Day: {_chg:+.1f}%
52w: ${_lo:.2f}–${_hi:.2f}
Market Cap: ${(snapshot.get('market_cap') or 0)/1e9:.1f}B

Fundamentals:
- P/E: {snapshot.get('pe_ratio','N/A')}  Fwd P/E: {snapshot.get('forward_pe','N/A')}  PEG: {snapshot.get('peg_ratio','N/A')}
- Beta: {snapshot.get('beta','N/A')}  Revenue growth: {(snapshot.get('revenue_growth') or 0)*100:.0f}%
- Earnings growth: {(snapshot.get('earnings_growth') or 0)*100:.0f}%
- Analyst target: ${_at:.0f}  Rec: {snapshot.get('analyst_rec','N/A')}

Technicals:
- RSI: {_rsi:.0f}  MACD hist: {_mcd:.3f}
- SMA20: ${_s20:.2f}  SMA50: ${_s50:.2f}  SMA200: ${_s200:.2f}
- Technical score: {_ts}/10

Sentiment: News={sent.get('news_score',5)} Trends={sent.get('trends_score',5)} Direction={sent.get('trend_direction','stable')}
Top news: {'; '.join((sent.get('top_headlines') or [])[:2])[:150]}

Macro: Fed {macro.get('fed_funds_rate','?')}%, Inflation {macro.get('inflation_yoy','?')}%, Geo risk {geo.get('risk_index',5)}/10
Next earnings: {snapshot.get('next_earnings_date','unknown')}

Strategy: Maximize profit vs SPY/QQQ benchmark. Open to short-term or long-term plays.

Return this JSON:
{{
  "ticker": "{research_subject}",
  "company_name": "{snapshot.get('company_name','')}",
  "sector": "{snapshot.get('sector','')}",
  "summary": "<2-3 sentences on current situation>",
  "bull_case": "<2 sentences>",
  "bear_case": "<2 sentences>",
  "action": "strong_buy|buy|hold|sell|strong_sell",
  "confidence": <1-10>,
  "horizon": "short|medium|long",
  "play_type": "core|momentum|seasonal",
  "current_price": {_cp},
  "buy_range": [{_cp*0.95:.2f}, {_cp*1.02:.2f}],
  "target_1yr": <float>,
  "stop_loss": <float>,
  "thesis": "<3 sentences max — core investment case vs SPY/QQQ>",
  "technical_summary": "<one sentence>",
  "macro_context": "<one sentence on macro impact>",
  "key_risks": ["risk1", "risk2", "risk3"],
  "key_catalysts": ["cat1", "cat2"],
  "vs_benchmark": "<expected outperformance vs SPY/QQQ and why>"
}}

Return ONLY the JSON object."""
                        result = call_agent("claude-sonnet-4-6", prompt, 2000, "Research/Stock")
                        subject_type = "stock"

                    save_research_result(research_subject, subject_type, result)
                    st.success("Research complete")

                except Exception as e:
                    st.error(f"Research failed: {e}")
                    result = {}

        # ── Display results ────────────────────────────────────────────────
        if result:
            if result.get("sector"):
                # Sector display
                st.markdown(f"### {result.get('sector','')} — Sector Research")
                verdict = result.get("investment_verdict", "neutral")
                col_a, col_b = st.columns([3, 1])
                col_a.markdown(result.get("summary", ""))
                col_b.metric("Verdict", verdict.upper())

                col_l, col_r = st.columns(2)
                with col_l:
                    st.markdown("**Tailwinds**")
                    for t in result.get("tailwinds", []):
                        st.markdown(f"  🟢 {t}")
                with col_r:
                    st.markdown("**Headwinds**")
                    for h in result.get("headwinds", []):
                        st.markdown(f"  🔴 {h}")

                st.markdown("**Key Public Companies:**")
                for c in result.get("key_public_companies", []):
                    st.markdown(f"  **{c.get('ticker','')}** — {c.get('company','')} — _{c.get('reason','')}_")

                st.markdown(f"**Upcoming Catalysts:** {', '.join(result.get('upcoming_catalysts', []))}")
                st.info(f"vs Benchmark: {result.get('vs_benchmark','')}")
                st.caption(result.get("macro_note", ""))

            else:
                # Stock display
                action = result.get("action", "hold")
                label, badge_cls, card_cls = ACTION_LABELS.get(action, ("HOLD", "badge-hold", "hold"))
                h_label, h_cls = HORIZON_LABELS.get(result.get("horizon","medium"), ("Medium", "horizon-medium"))

                st.markdown(f"""
                <div class="rec-card {card_cls}" style="margin-top:12px;">
                  <strong style="font-size:18px;">{result.get('ticker','')} — {result.get('company_name','')}</strong>
                  <span style="color:#64748b; margin-left:8px;">{result.get('sector','')}</span><br>
                  <span class="action-badge {badge_cls}" style="margin-top:6px;">{label}</span>
                  <span class="horizon-badge {h_cls}">{h_label}</span>
                  <span class="play-badge">{result.get('play_type','core')}</span>
                </div>
                """, unsafe_allow_html=True)

                col1, col2, col3, col4 = st.columns(4)
                br = result.get("buy_range", [None, None])
                col1.metric("Current Price",  fmt_price(result.get("current_price")))
                col2.metric("Buy Range",      f"{fmt_price(br[0])} – {fmt_price(br[1])}" if br else "—")
                col3.metric("1Y Target",      fmt_price(result.get("target_1yr")))
                col4.metric("Stop Loss",      fmt_price(result.get("stop_loss")))

                st.markdown("**Thesis:**")
                st.markdown(result.get("thesis", ""))

                col_l, col_r = st.columns(2)
                with col_l:
                    st.markdown("**Bull Case:** " + result.get("bull_case", ""))
                    st.markdown("**Catalysts:** " + ", ".join(result.get("key_catalysts", [])))
                with col_r:
                    st.markdown("**Bear Case:** " + result.get("bear_case", ""))
                    st.markdown("**Risks:** " + ", ".join(result.get("key_risks", [])))

                st.markdown(f"**Technical:** {result.get('technical_summary','')}")
                st.markdown(f"**Macro context:** {result.get('macro_context','')}")
                st.info(f"vs SPY/QQQ: {result.get('vs_benchmark','')}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — RECOMMENDATIONS
# ═══════════════════════════════════════════════════════════════════════════════
with tab_recs:
    st.subheader("Recommendations")

    all_recs = get_latest_recommendations()

    # ── Sector spotlight ──────────────────────────────────────────────────────
    sector_picks = get_sector_top_picks(n=3)
    if sector_picks:
        st.markdown('<div class="section-header">Sector Spotlight — Top Picks</div>', unsafe_allow_html=True)
        cols = st.columns(min(len(sector_picks), 4))
        for i, (sector, picks) in enumerate(sector_picks.items()):
            with cols[i % len(cols)]:
                st.markdown(f"**{sector}**")
                for p in picks:
                    action = p.get("action", "hold")
                    _, badge, _ = ACTION_LABELS.get(action, ("HOLD","badge-hold","hold"))
                    upside = p.get("growth_potential_pct") or 0
                    st.markdown(
                        f'<span class="action-badge {badge}" style="font-size:11px;">{p["ticker"]}</span>'
                        f' <span style="color:#16a34a;font-weight:700;">+{upside:.0f}%</span>',
                        unsafe_allow_html=True
                    )
        st.divider()

    # ── Run metadata ──────────────────────────────────────────────────────────
    if all_recs:
        run_at = all_recs[0].get("run_at", "")
        run_at_str = str(run_at)[:16] if run_at else "—"
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        try:
            ms = json.loads(all_recs[0].get("market_summary") or "{}")
            sentiment = ms.get("overall_sentiment", "—")
            spy_w     = ms.get("spy_week")
        except Exception:
            sentiment, spy_w = "—", None
        col_m1.metric("Analysis Time",   run_at_str)
        col_m2.metric("Stocks Analyzed", len(all_recs))
        col_m3.metric("Market Sentiment", sentiment.upper() if sentiment else "—")
        col_m4.metric("SPY Week",        f"{spy_w:+.1f}%" if spy_w is not None else "—")

    # ── Filters ───────────────────────────────────────────────────────────────
    col_f1, col_f2, col_f3, col_f4 = st.columns([2, 2, 1.5, 1.5])
    with col_f1:
        filter_actions = st.multiselect(
            "Action",
            ["strong_buy", "buy", "new_pick", "hold", "sell", "strong_sell"],
            default=["strong_buy", "buy", "new_pick"],
        )
    with col_f2:
        filter_horizon = st.multiselect(
            "Horizon",
            ["short", "medium", "long"],
            default=["short", "medium", "long"],
        )
    with col_f3:
        filter_conf = st.slider("Min Confidence", 1, 10, 6)
    with col_f4:
        filter_play = st.multiselect("Play Type", ["core", "momentum", "seasonal"], default=["core","momentum","seasonal"])

    # Apply filters
    filtered = [
        r for r in all_recs
        if r.get("action") in filter_actions
        and r.get("confidence", 0) >= filter_conf
        and r.get("horizon", "medium") in filter_horizon
        and r.get("play_type", "core") in filter_play
    ]

    if not filtered and not all_recs:
        st.info("No recommendations yet. Run the analysis pipeline first.")
    elif not filtered:
        st.warning(f"No recommendations match the current filters ({len(all_recs)} total in latest run).")
    else:
        st.caption(f"Showing {len(filtered)} of {len(all_recs)} recommendations")
        for r in filtered:
            render_rec_card(r)

    # ── Pre-fill journal if user clicked "I followed this" ────────────────────
    if "journal_prefill" in st.session_state:
        prefill = st.session_state.pop("journal_prefill")
        st.divider()
        st.markdown("**Log this trade:**")
        with st.form("quick_journal"):
            col_a, col_b, col_c = st.columns(3)
            shares = col_a.number_input("Shares", min_value=0.0, step=0.01)
            price  = col_b.number_input("Price $", min_value=0.0, step=0.01,
                                         value=float(prefill.get("current_price") or 0))
            action = col_c.selectbox("Action", ["buy", "sell", "hold_noted"])
            reason = st.text_input("Reasoning (optional)")
            if st.form_submit_button("Log Trade") and prefill.get("ticker") and shares > 0 and price > 0:
                log_action(prefill["ticker"], action, shares, price,
                           was_suggested=True, rec_id=prefill.get("id"), reasoning=reason)
                st.success(f"Logged {action} {prefill['ticker']}")
