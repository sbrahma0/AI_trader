"""
app.py — AI Trading Recommendation App (Streamlit)

Run:  streamlit run app.py
"""

import streamlit as st
import json
from datetime import datetime, date

# ── PAGE CONFIG (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="AI Trader",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={"About": "AI-powered trading recommendations — personal use only."}
)

# ── IMPORTS (after page config) ───────────────────────────────────────────────
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from core.database import (
    init_db, get_portfolio, get_latest_recommendations, get_action_log,
    log_action, upsert_holding, get_user_profile, update_user_profile,
    get_watchlist, snapshot_portfolio
)
from core.portfolio_parser import parse_file

# ── INITIALIZE DB ─────────────────────────────────────────────────────────────
init_db()

# ── MOBILE-FRIENDLY CUSTOM CSS ────────────────────────────────────────────────
st.markdown("""
<style>
/* Mobile responsiveness */
@media (max-width: 768px) {
    .block-container { padding: 1rem 0.75rem !important; }
    .stColumn { padding: 0.25rem !important; }
}

/* Card styles */
.rec-card {
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 10px;
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

.metric-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 6px; }
.metric-chip {
    background: #f1f5f9; border-radius: 8px;
    padding: 4px 10px; font-size: 12px; color: #334155;
}
.pnl-pos { color: #16a34a; font-weight: 700; }
.pnl-neg { color: #dc2626; font-weight: 700; }

/* Section headers */
.section-header {
    font-size: 18px; font-weight: 700; margin: 16px 0 8px;
    padding-bottom: 6px; border-bottom: 2px solid #e2e8f0;
}

/* Score bars */
.score-bar-bg { background: #e2e8f0; border-radius: 4px; height: 6px; margin: 2px 0 6px; }
.score-bar-fill { border-radius: 4px; height: 6px; }
</style>
""", unsafe_allow_html=True)


# ── HELPER FUNCTIONS ──────────────────────────────────────────────────────────

ACTION_LABELS = {
    "strong_buy":  ("⬆️⬆️ STRONG BUY",  "badge-strong-buy",  "strong-buy"),
    "buy":         ("⬆️ BUY",            "badge-buy",         "buy"),
    "hold":        ("⏸ HOLD",            "badge-hold",        "hold"),
    "sell":        ("⬇️ SELL",            "badge-sell",        "sell"),
    "strong_sell": ("⬇️⬇️ STRONG SELL",  "badge-strong-sell", "strong-sell"),
    "new_pick":    ("✨ NEW PICK",        "badge-new-pick",    "new-pick"),
}

def fmt_price(v):
    return f"${v:,.2f}" if v is not None else "—"

def fmt_pct(v):
    if v is None: return "—"
    sign = "+" if v >= 0 else ""
    cls = "pnl-pos" if v >= 0 else "pnl-neg"
    return f'<span class="{cls}">{sign}{v:.1f}%</span>'

def score_bar(score, color="#3b82f6"):
    if score is None:
        return ""
    pct = min(100, max(0, score * 10))
    return f"""
    <div class="score-bar-bg">
      <div class="score-bar-fill" style="width:{pct}%;background:{color};"></div>
    </div>"""

def confidence_color(c):
    if c is None: return "#94a3b8"
    if c >= 8: return "#16a34a"
    if c >= 6: return "#ca8a04"
    return "#dc2626"


# ── TOP NAVIGATION ────────────────────────────────────────────────────────────

tabs = st.tabs(["📊 Recommendations", "💼 Portfolio", "📓 Trade Journal", "👁 Watchlist", "⚙️ Profile"])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — RECOMMENDATIONS
# ═══════════════════════════════════════════════════════════════════════════════
with tabs[0]:
    st.markdown('<div class="section-header">Latest Recommendations</div>', unsafe_allow_html=True)

    recs = get_latest_recommendations()

    if not recs:
        st.info("No recommendations yet. Run the analysis pipeline or wait for the scheduled job (9:30 AM / 4:00 PM ET).")
        st.caption("💡 To manually trigger: run `python core/analyzer.py` from your project folder.")
    else:
        # Show run metadata
        run_at = recs[0].get("run_at", "")
        run_at_str = str(run_at)[:16] if run_at else "—"
        market = recs[0].get("market_summary")
        if market:
            try:
                ms = json.loads(market)
                sentiment = ms.get("overall_sentiment", "")
                col1, col2, col3 = st.columns(3)
                col1.metric("Market Sentiment", sentiment)
                col2.metric("Analysis Time", run_at_str)
                col3.metric("Stocks Analyzed", len(recs))
            except Exception:
                st.caption(f"Analysis run: {run_at_str}")

        st.markdown("---")

        # Filter controls
        fcol1, fcol2 = st.columns([2, 1])
        with fcol1:
            filter_action = st.multiselect(
                "Filter by action",
                ["strong_buy", "buy", "hold", "sell", "strong_sell", "new_pick"],
                default=["strong_buy", "buy", "new_pick"],
                format_func=lambda x: ACTION_LABELS.get(x, (x,))[0]
            )
        with fcol2:
            min_confidence = st.slider("Min confidence", 1, 10, 6)

        filtered = [r for r in recs
                   if (not filter_action or r["action"] in filter_action)
                   and (r.get("confidence") or 0) >= min_confidence]

        if not filtered:
            st.warning("No recommendations match your filters.")

        for rec in filtered:
            action = rec.get("action", "hold")
            label, badge_cls, card_cls = ACTION_LABELS.get(action, ("—", "", "hold"))
            conf = rec.get("confidence")
            conf_color = confidence_color(conf)
            ticker = rec.get("ticker", "")
            company = rec.get("company_name") or ticker
            is_new = rec.get("is_new_pick", 0)

            st.markdown(f"""
            <div class="rec-card {card_cls}">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:6px;">
                <div>
                  <span style="font-size:20px;font-weight:800;">{ticker}</span>
                  <span style="color:#64748b;margin-left:8px;font-size:13px;">{company}</span>
                  {' <span style="font-size:11px;background:#dbeafe;color:#1d4ed8;padding:2px 7px;border-radius:10px;margin-left:4px;">NEW</span>' if is_new else ''}
                </div>
                <div style="display:flex;gap:8px;align-items:center;">
                  <span class="action-badge {badge_cls}">{label}</span>
                  <span style="font-size:12px;color:{conf_color};font-weight:700;">Conf: {conf}/10</span>
                </div>
              </div>

              <div class="metric-row">
                <span class="metric-chip">💵 Current: {fmt_price(rec.get('current_price'))}</span>
                <span class="metric-chip">🎯 Buy: {fmt_price(rec.get('buy_range_low'))} – {fmt_price(rec.get('buy_range_high'))}</span>
                <span class="metric-chip">📤 Sell: {fmt_price(rec.get('sell_target_low'))} – {fmt_price(rec.get('sell_target_high'))}</span>
                <span class="metric-chip">🛑 Stop: {fmt_price(rec.get('stop_loss'))}</span>
                <span class="metric-chip">📈 Upside: {fmt_pct(rec.get('growth_potential_pct'))}</span>
                <span class="metric-chip">⏱ {rec.get('growth_timeline') or '—'}</span>
              </div>

              <div style="margin-top:10px;font-size:13px;color:#1e293b;line-height:1.5;">
                <strong>Thesis:</strong> {rec.get('thesis') or '—'}
              </div>
            </div>
            """, unsafe_allow_html=True)

            # Expandable details
            with st.expander(f"📐 Technical • Sentiment • Macro scores — {ticker}"):
                sc1, sc2, sc3 = st.columns(3)
                with sc1:
                    st.caption("Technical")
                    st.markdown(score_bar(rec.get("technical_score"), "#3b82f6"), unsafe_allow_html=True)
                    st.caption(f"{rec.get('technical_score') or '—'}/10")
                with sc2:
                    st.caption("Sentiment")
                    st.markdown(score_bar(rec.get("sentiment_score"), "#8b5cf6"), unsafe_allow_html=True)
                    st.caption(f"{rec.get('sentiment_score') or '—'}/10")
                with sc3:
                    st.caption("Macro")
                    st.markdown(score_bar(rec.get("macro_score"), "#f59e0b"), unsafe_allow_html=True)
                    st.caption(f"{rec.get('macro_score') or '—'}/10")

                risks = rec.get("risks")
                catalysts = rec.get("catalysts")
                if risks:
                    try: risks = json.loads(risks)
                    except: pass
                    if isinstance(risks, list):
                        st.markdown("**⚠️ Risks:** " + " • ".join(risks))
                if catalysts:
                    try: catalysts = json.loads(catalysts)
                    except: pass
                    if isinstance(catalysts, list):
                        st.markdown("**🚀 Catalysts:** " + " • ".join(catalysts))

                suggested_pct = rec.get("suggested_pct_portfolio")
                suggested_shares = rec.get("suggested_shares")
                if suggested_pct or suggested_shares:
                    st.markdown(f"**💡 Suggested position:** {suggested_pct or '—'}% of portfolio"
                               + (f" ({suggested_shares:.0f} shares)" if suggested_shares else ""))

                # Quick action button
                if action in ["strong_buy", "buy", "new_pick"]:
                    if st.button(f"📓 Log a trade for {ticker}", key=f"log_{ticker}_{rec['id']}"):
                        st.session_state["journal_prefill"] = {
                            "ticker": ticker,
                            "action": "buy",
                            "rec_id": rec["id"],
                            "was_suggested": True,
                        }
                        st.info("Switch to the 📓 Trade Journal tab to log this trade.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — PORTFOLIO
# ═══════════════════════════════════════════════════════════════════════════════
with tabs[1]:
    st.markdown('<div class="section-header">My Portfolio</div>', unsafe_allow_html=True)

    # ── PDF / CSV Upload ───────────────────────────────────────────────────────
    with st.expander("📄 Import from Brokerage Statement (PDF or CSV)", expanded=False):
        uploaded = st.file_uploader(
            "Upload Robinhood PDF, CSV, or any brokerage statement",
            type=["pdf", "csv", "tsv"],
            help="Your file is processed locally and never sent anywhere except the Claude API for parsing."
        )
        if uploaded:
            with st.spinner("Parsing your statement..."):
                try:
                    holdings = parse_file(uploaded.name, uploaded.read())
                    if holdings:
                        st.success(f"Found {len(holdings)} holdings!")
                        st.dataframe(holdings, use_container_width=True)
                        if st.button("✅ Import these holdings into portfolio"):
                            for h in holdings:
                                upsert_holding(
                                    ticker=h["ticker"],
                                    shares=h["shares"],
                                    avg_cost=h.get("avg_cost") or 0,
                                    company_name=h.get("company_name"),
                                )
                            snapshot_portfolio(source="pdf_import")
                            st.success("Holdings imported and snapshot saved!")
                            st.rerun()
                    else:
                        st.warning("Could not extract holdings. Try a CSV export instead, or add holdings manually below.")
                except Exception as e:
                    st.error(f"Parse error: {e}")

    # ── Manual Add ────────────────────────────────────────────────────────────
    with st.expander("➕ Add / Update Holding Manually"):
        m1, m2, m3 = st.columns(3)
        with m1:
            m_ticker = st.text_input("Ticker", placeholder="NVDA").upper()
        with m2:
            m_shares = st.number_input("Shares", min_value=0.0, step=0.01, format="%.4f")
        with m3:
            m_cost = st.number_input("Avg cost per share ($)", min_value=0.0, step=0.01, format="%.2f")
        m_name = st.text_input("Company name (optional)", placeholder="NVIDIA Corporation")
        if st.button("Save Holding") and m_ticker and m_shares > 0:
            upsert_holding(m_ticker, m_shares, m_cost, m_name or None)
            st.success(f"Saved {m_ticker}!")
            st.rerun()

    # ── Holdings Table ────────────────────────────────────────────────────────
    holdings = get_portfolio()
    if not holdings:
        st.info("No holdings yet. Import a statement or add manually above.")
    else:
        total_cost  = sum((h.get("avg_cost_basis") or 0) * (h.get("shares") or 0) for h in holdings)
        total_value = sum((h.get("current_price") or h.get("avg_cost_basis") or 0) * (h.get("shares") or 0) for h in holdings)
        total_pnl   = total_value - total_cost

        kc1, kc2, kc3 = st.columns(3)
        kc1.metric("Total Cost Basis", f"${total_cost:,.0f}")
        kc2.metric("Est. Current Value", f"${total_value:,.0f}")
        kc3.metric("Unrealized P&L", f"${total_pnl:+,.0f}", delta=f"{(total_pnl/total_cost*100 if total_cost else 0):+.1f}%")

        st.markdown("---")

        for h in holdings:
            price     = h.get("current_price") or h.get("avg_cost_basis") or 0
            cost      = h.get("avg_cost_basis") or 0
            shares    = h.get("shares") or 0
            value     = price * shares
            pnl_pct   = h.get("pnl_pct")
            pnl_dol   = h.get("pnl_dollars")

            pnl_sign  = "+" if (pnl_pct or 0) >= 0 else ""
            pnl_color = "#16a34a" if (pnl_pct or 0) >= 0 else "#dc2626"

            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"""
                <div style="padding:10px 0;border-bottom:1px solid #e2e8f0;">
                  <span style="font-size:17px;font-weight:700;">{h['ticker']}</span>
                  <span style="color:#64748b;font-size:12px;margin-left:8px;">{h.get('company_name') or ''}</span>
                  <div class="metric-row" style="margin-top:5px;">
                    <span class="metric-chip">{shares:.4f} shares</span>
                    <span class="metric-chip">Avg cost: {fmt_price(cost)}</span>
                    <span class="metric-chip">Price: {fmt_price(price)}</span>
                    <span class="metric-chip">Value: {fmt_price(value)}</span>
                    <span class="metric-chip" style="color:{pnl_color};font-weight:700;">
                      P&L: {pnl_sign}{fmt_price(pnl_dol)} ({pnl_sign}{pnl_pct or 0:.1f}%)
                    </span>
                  </div>
                </div>
                """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — TRADE JOURNAL
# ═══════════════════════════════════════════════════════════════════════════════
with tabs[2]:
    st.markdown('<div class="section-header">Trade Journal</div>', unsafe_allow_html=True)
    st.caption("Log every trade you make — whether you followed a recommendation or made your own call. This keeps the system's portfolio accurate and improves future recommendations.")

    # Pre-fill from recommendation click
    prefill = st.session_state.pop("journal_prefill", {})

    with st.form("trade_journal_form"):
        st.subheader("Log a Trade")
        j1, j2 = st.columns(2)
        with j1:
            j_ticker = st.text_input("Ticker *", value=prefill.get("ticker", "")).upper()
            j_action = st.selectbox(
                "Action *",
                ["buy", "sell", "hold_noted", "watchlist_add"],
                index=["buy", "sell", "hold_noted", "watchlist_add"].index(prefill.get("action", "buy"))
            )
            j_date = st.date_input("Date *", value=date.today())
        with j2:
            j_shares = st.number_input("Shares", min_value=0.0, step=0.01, format="%.4f")
            j_price  = st.number_input("Price per share ($)", min_value=0.0, step=0.01, format="%.2f")

        j_was_suggested = st.checkbox(
            "I followed a system recommendation",
            value=prefill.get("was_suggested", False)
        )
        j_reasoning = st.text_area(
            "Your reasoning / notes",
            placeholder="Why did you make this trade? Any context, conviction level, or things to remember later...",
            height=80
        )

        submitted = st.form_submit_button("📝 Log Trade", use_container_width=True)
        if submitted:
            if not j_ticker:
                st.error("Ticker is required.")
            elif j_action in ["buy", "sell"] and (j_shares <= 0 or j_price <= 0):
                st.error("Shares and price are required for buy/sell.")
            else:
                rec_id = prefill.get("rec_id") if j_was_suggested else None
                log_action(
                    ticker=j_ticker,
                    action=j_action,
                    shares=j_shares,
                    price=j_price,
                    was_suggested=j_was_suggested,
                    rec_id=rec_id,
                    reasoning=j_reasoning,
                )
                st.success(f"✅ Trade logged: {j_action.upper()} {j_shares} {j_ticker} @ ${j_price:.2f}")
                st.rerun()

    st.markdown("---")
    st.subheader("Trade History")

    logs = get_action_log(limit=100)
    if not logs:
        st.info("No trades logged yet.")
    else:
        action_icons = {"buy": "⬆️", "sell": "⬇️", "hold_noted": "⏸", "watchlist_add": "👁"}
        for entry in logs:
            icon = action_icons.get(entry.get("action", ""), "•")
            was_sug = "🤖 Rec" if entry.get("was_system_suggested") else "💡 Own call"
            total = (entry.get("shares") or 0) * (entry.get("price_per_share") or 0)
            st.markdown(f"""
            <div style="padding:8px 12px;border-radius:8px;background:#f8fafc;margin-bottom:6px;border-left:3px solid #94a3b8;">
              <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px;">
                <span><strong>{icon} {entry.get('action','').upper()}</strong>
                  <span style="font-size:16px;font-weight:800;margin-left:6px;">{entry.get('ticker')}</span>
                  <span style="color:#64748b;font-size:12px;margin-left:6px;">{entry.get('shares','') or ''} shares @ {fmt_price(entry.get('price_per_share'))}</span>
                </span>
                <span style="font-size:12px;color:#64748b;">{entry.get('action_date','')} &nbsp;|&nbsp; {was_sug} &nbsp;|&nbsp; {fmt_price(total) if total else ''}</span>
              </div>
              {f'<div style="font-size:12px;color:#475569;margin-top:3px;">💬 {entry.get("my_reasoning")}</div>' if entry.get("my_reasoning") else ''}
            </div>
            """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — WATCHLIST
# ═══════════════════════════════════════════════════════════════════════════════
with tabs[3]:
    st.markdown('<div class="section-header">Watchlist</div>', unsafe_allow_html=True)
    st.caption("All stocks being monitored in the analysis pipeline.")

    watchlist = get_watchlist()
    if watchlist:
        sectors = sorted(set(w.get("sector") or "Other" for w in watchlist))
        for sector in sectors:
            sector_stocks = [w for w in watchlist if (w.get("sector") or "Other") == sector]
            st.markdown(f"**{sector}**")
            cols = st.columns(min(4, len(sector_stocks)))
            for i, stock in enumerate(sector_stocks):
                with cols[i % len(cols)]:
                    st.markdown(f"""
                    <div style="background:#f1f5f9;border-radius:8px;padding:8px 10px;margin-bottom:6px;text-align:center;">
                      <div style="font-weight:700;font-size:16px;">{stock['ticker']}</div>
                      <div style="font-size:11px;color:#64748b;">{stock.get('company_name','')}</div>
                    </div>
                    """, unsafe_allow_html=True)
            st.markdown("")

    # Add to watchlist
    with st.expander("➕ Add to Watchlist"):
        w1, w2, w3 = st.columns(3)
        with w1:
            w_ticker = st.text_input("Ticker", key="wl_ticker").upper()
        with w2:
            w_name = st.text_input("Company name", key="wl_name")
        with w3:
            w_sector = st.text_input("Sector", key="wl_sector")
        w_why = st.text_input("Why watching?", key="wl_why")
        if st.button("Add to Watchlist") and w_ticker:
            from core.database import get_connection
            with get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO watchlist (ticker, company_name, sector, why_watching, is_active)
                    VALUES (?, ?, ?, ?, 1)
                """, (w_ticker, w_name or None, w_sector or None, w_why or None))
            st.success(f"Added {w_ticker}!")
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — PROFILE / PREFERENCES
# ═══════════════════════════════════════════════════════════════════════════════
with tabs[4]:
    st.markdown('<div class="section-header">Investment Profile</div>', unsafe_allow_html=True)
    st.caption("The analysis engine uses this profile to personalize every recommendation.")

    profile = get_user_profile()

    with st.form("profile_form"):
        p1, p2 = st.columns(2)
        with p1:
            p_goal = st.text_input("Investment goal", value=profile.get("investment_goal", ""))
            p_horizon = st.selectbox(
                "Time horizon",
                ["1 year", "2-3 years", "3-5 years", "5+ years"],
                index=["1 year", "2-3 years", "3-5 years", "5+ years"].index(
                    profile.get("time_horizon", "2-3 years")
                    if profile.get("time_horizon") in ["1 year", "2-3 years", "3-5 years", "5+ years"]
                    else "2-3 years"
                )
            )
            p_target = st.number_input(
                "Target return (%)",
                min_value=0.0, max_value=10000.0, step=10.0,
                value=float(profile.get("target_return") or 300.0),
                help="e.g. 300 means you want to triple your money"
            )
        with p2:
            p_risk = st.select_slider(
                "Risk tolerance",
                options=["low", "medium-low", "medium", "medium-high", "high"],
                value=profile.get("risk_tolerance", "medium-high")
            )
            p_strategy = st.text_area(
                "Strategy notes",
                value=profile.get("strategy", ""),
                height=80,
                placeholder="e.g. No day trading. Hold 6+ months. Prefer AI infrastructure leaders."
            )

        p_sectors = st.text_area(
            "Preferred sectors (one per line)",
            value="\n".join(json.loads(profile.get("preferred_sectors") or "[]")),
            height=100,
            help="Analysis will weight these sectors more heavily"
        )
        p_notes = st.text_area(
            "Personal notes for the AI",
            value=profile.get("notes") or "",
            height=80,
            placeholder="Anything else you want the AI to know about your situation or preferences..."
        )

        if st.form_submit_button("💾 Save Profile", use_container_width=True):
            sectors_list = [s.strip() for s in p_sectors.splitlines() if s.strip()]
            update_user_profile(
                investment_goal=p_goal,
                time_horizon=p_horizon,
                target_return=p_target,
                risk_tolerance=p_risk,
                strategy=p_strategy,
                preferred_sectors=json.dumps(sectors_list),
                notes=p_notes,
            )
            st.success("Profile saved! Next analysis run will use these preferences.")

    # Show current profile summary
    st.markdown("---")
    st.subheader("Current Profile Summary")
    current = get_user_profile()
    c1, c2, c3 = st.columns(3)
    c1.metric("Target Return", f"{current.get('target_return', 0):.0f}%")
    c2.metric("Time Horizon", current.get("time_horizon", "—"))
    c3.metric("Risk Tolerance", current.get("risk_tolerance", "—"))
