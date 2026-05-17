import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
for _key in ["ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_KEY", "DASHBOARD_PASSWORD"]:
    if _key in st.secrets:
        os.environ[_key] = st.secrets[_key]

import streamlit as st
import pandas as pd
from datetime import date, datetime
from core import db
from config.settings import DASHBOARD_PASSWORD, TOTAL_CAPITAL, DAILY_PROFIT_TARGET
from config.company_names import COMPANY_NAMES


def add_company_col(df, ticker_col="ticker"):
    """Insert a Company column right after the ticker column."""
    if ticker_col not in df.columns:
        return df
    col_pos = df.columns.get_loc(ticker_col) + 1
    df.insert(col_pos, "company", df[ticker_col].map(lambda t: COMPANY_NAMES.get(t, "")))
    return df

st.set_page_config(
    page_title="Trading Agent",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Auth ──────────────────────────────────────────────────────────
def check_password():
    if st.session_state.get("authenticated"):
        return True
    pwd = st.text_input("Password", type="password", key="pwd")
    if st.button("Login"):
        if pwd == DASHBOARD_PASSWORD:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password")
    return False

if not check_password():
    st.stop()


# ── Sidebar ───────────────────────────────────────────────────────
st.sidebar.title("📈 Trading Agent")
st.sidebar.caption(f"Capital: ${TOTAL_CAPITAL:,} | Target: ${DAILY_PROFIT_TARGET:,}/day")
page = st.sidebar.radio("View", ["Today", "Positions", "Performance", "Scan Log"])
st.sidebar.markdown("---")
if st.sidebar.button("🔄 Refresh"):
    st.rerun()
st.sidebar.caption(f"Last refresh: {datetime.now().strftime('%H:%M:%S')}")


# ── Helpers ───────────────────────────────────────────────────────
def fmt_pnl(val):
    return f"+${val:,.2f}" if val > 0 else f"-${abs(val):,.2f}" if val < 0 else "$0.00"

def pnl_color(val):
    return "green" if val > 0 else "red" if val < 0 else "gray"


# ── TODAY WORKFLOW ─────────────────────────────────────────────────
if page == "Today":
    today = date.today().isoformat()

    # Load scan result (premarket) — fall back to most recent
    scans = db.select("scan_results", filters={"date": today, "scan_type": "premarket"})
    if not scans:
        scans = db.select("scan_results", filters={"scan_type": "premarket"}, order="created_at", limit=1)
    scan = scans[0] if scans else None
    results = scan["results"] if scan else {}
    run_date = scan["date"] if scan else today

    # Load trade plan + trades for that date
    plans = db.select("trade_plans", filters={"date": run_date})
    plan = plans[0] if plans else None
    trades = db.select("planned_trades", filters={"plan_id": plan["id"]}) if plan else []

    # Live positions
    open_pos = db.select("positions", filters={"status": "OPEN"})
    all_closed = db.select("positions", filters={"status": "CLOSED"})
    today_closed = [p for p in all_closed if (p.get("closed_at") or "").startswith(run_date)]

    # Unpack scan results
    skipped       = results.get("skipped", False)
    vix           = results.get("vix")
    fear_greed    = results.get("fear_greed")
    econ_events   = results.get("economic_events", [])
    futures       = results.get("futures", {})
    futures_bias  = results.get("futures_bias", "NEUTRAL")
    intl          = results.get("intl_markets", {})
    candidates      = results.get("candidates", [])
    blackout        = results.get("blackout_tickers", [])
    sector_blocked  = results.get("sector_blocked", [])

    # ── Header ─────────────────────────────────────────────────────
    if skipped:
        badge, badge_color = "SKIPPED", "#c0392b"
    elif not results:
        badge, badge_color = "PENDING", "#7f8c8d"
    elif plan:
        badge, badge_color = "TRADING", "#27ae60"
    else:
        badge, badge_color = "NO TRADES", "#e67e22"

    h1, h2 = st.columns([4, 1])
    h1.title(f"Trading Workflow — {run_date}")
    h2.markdown(
        f"<div style='text-align:right;padding-top:14px'>"
        f"<span style='background:{badge_color};color:white;padding:6px 14px;"
        f"border-radius:6px;font-weight:bold;font-size:16px'>{badge}</span></div>",
        unsafe_allow_html=True
    )

    if not results:
        st.info("No data yet. Premarket pipeline runs at 9:00 AM ET.")
        st.stop()

    st.divider()

    # ── STEP 0: Market Conditions ───────────────────────────────────
    st.subheader("0️⃣  Market Conditions")

    if skipped:
        st.error(f"⛔ {results.get('reason', 'Trading skipped')}")
        c1, c2 = st.columns(2)
        if vix:
            c1.metric("VIX", f"{vix:.1f}",
                      help="CBOE Volatility Index — measures expected market volatility over 30 days. >30 = skip trading.")
        if fear_greed:
            c2.metric("Fear & Greed", f"{fear_greed['value']} — {fear_greed['classification']}",
                      help="CNN Fear & Greed Index (0–100). Extreme Fear (<25) reduces positions. Extreme Greed (>80) signals overextension.")
    else:
        # ── Economic calendar banner ──────────────────────────────────
        ECON_LABELS = {
            "FOMC": "FOMC (Federal Open Market Committee) rate decision — Fed announces interest rate change at 2PM ET. High uncertainty, positions reduced to 8.",
            "CPI":  "CPI (Consumer Price Index) release — inflation data at 8:30AM ET. Market moves sharply on surprise vs estimate. Positions reduced to 10.",
            "NFP":  "NFP (Non-Farm Payrolls) jobs report — monthly employment data at 8:30AM ET. Strong market-mover. Positions reduced to 10.",
        }
        for ev in econ_events:
            st.warning(f"📅 **{ev} day** — {ECON_LABELS.get(ev, ev)}")

        # ── Row 1: VIX + Fear & Greed + Futures ──────────────────────
        vix_icon = "🟢" if (vix or 0) < 20 else "🟡" if (vix or 0) < 30 else "🔴"
        fut_icon = "🟢" if futures_bias == "BULLISH" else "🔴" if futures_bias == "BEARISH" else "⚪"

        if fear_greed:
            fg_val = fear_greed["value"]
            fg_icon = "🔴" if fg_val < 25 else "🟡" if fg_val < 45 else "🟢" if fg_val < 80 else "🟡"
        else:
            fg_icon = "⚪"

        fut_items = list(futures.items())
        cols = st.columns(6)
        cols[0].metric(
            f"{vix_icon} VIX", f"{vix:.1f}" if vix else "N/A",
            help="CBOE Volatility Index — expected market volatility over 30 days. <20 normal, 20–25 caution (10 pos), 25–30 high caution (5 pos), >30 skip trading."
        )
        cols[1].metric(
            f"{fg_icon} Fear & Greed",
            f"{fear_greed['value']}" if fear_greed else "N/A",
            delta=fear_greed["classification"] if fear_greed else None,
            delta_color="off",
            help="CNN Fear & Greed Index (0–100). Extreme Fear <25 → reduce to 5 pos. Fear 25–45 → reduce to 10 pos. Extreme Greed >80 → reduce to 10 pos (overextended)."
        )
        cols[2].metric(
            f"{fut_icon} Futures Bias", futures_bias,
            help="Pre-market direction of US index futures. BULLISH = avg up >0.5%, BEARISH = avg down >0.5%, NEUTRAL = flat. Down >1.5% = skip trading."
        )
        for i, (name, data) in enumerate(fut_items[:3]):
            chg = data["change_pct"]
            help_map = {
                "S&P500": "ES=F — S&P 500 E-mini futures. Broad US market direction.",
                "Nasdaq": "NQ=F — Nasdaq 100 E-mini futures. Tech-heavy index direction.",
                "Dow":    "YM=F — Dow Jones E-mini futures. Blue-chip index direction.",
            }
            cols[i + 3].metric(name, f"${data['price']:,.0f}", delta=f"{chg:+.2f}%",
                               help=help_map.get(name, name))

        if intl:
            with st.expander("🌍 International Markets"):
                icols = st.columns(len(intl))
                intl_help = {
                    "Nikkei (Japan)":  "^N225 — Japan's benchmark index. Asian market sentiment.",
                    "FTSE (UK)":       "^FTSE — UK's top 100 companies. European market open signal.",
                    "DAX (Germany)":   "^GDAXI — Germany's benchmark. European economic health.",
                    "Hang Seng (HK)":  "^HSI — Hong Kong index. China/Asia proxy.",
                    "Shanghai":        "000001.SS — China's main stock index.",
                }
                for i, (mkt_name, mkt_data) in enumerate(intl.items()):
                    chg = mkt_data["change_pct"]
                    icon = "🟢" if chg > 0 else "🔴"
                    icols[i].metric(f"{icon} {mkt_name}", f"{chg:+.2f}%",
                                    help=intl_help.get(mkt_name, mkt_name))

    st.divider()

    # ── STEP 1: Scanner ─────────────────────────────────────────────
    st.subheader("1️⃣  Market Scanner")

    total_scanned = len(candidates) + len(blackout)
    s1, s2, s3 = st.columns(3)
    s1.metric("Candidates Found", total_scanned,
              help=f"Stocks from the 430+ universe that passed minimum filters: price ≥$5, avg volume ≥500K, technical score ≥3/10.")
    s2.metric("Earnings Blocked", len(blackout),
              delta=f"-{len(blackout)}" if blackout else None, delta_color="inverse",
              help="Tickers removed because they report earnings today or tomorrow. Earnings = binary event with gap risk — not suitable for day trading.")
    s3.metric("Passed to Strategy", len(candidates),
              help="Remaining candidates sent to Claude's strategy agent after earnings blackout filter.")

    if blackout:
        with st.expander(f"⛔ Earnings Blackout — {len(blackout)} ticker(s)"):
            for b in blackout:
                st.markdown(f"- **{b['ticker']}**: {b['reason']}")

    if candidates:
        with st.expander(f"📋 Screened Candidates — {len(candidates)} stocks", expanded=True):
            df_scan = pd.DataFrame(candidates)
            show_cols = ["ticker", "price", "technical_score", "rsi", "volume_ratio", "atr_pct", "signals"]
            df_scan = df_scan[[c for c in show_cols if c in df_scan.columns]]
            if "technical_score" in df_scan.columns:
                df_scan = df_scan.sort_values("technical_score", ascending=False)
            df_scan = add_company_col(df_scan)
            st.dataframe(df_scan, use_container_width=True, height=320)

    st.divider()

    # ── STEP 2: Strategy & Risk ─────────────────────────────────────
    st.subheader("2️⃣  Strategy & Risk")

    if not plan:
        st.info("No trade plan generated yet.")
    else:
        st.markdown(f"**Claude's Market Read:** {plan['market_context']}")
        st.caption(f"Risk note: {plan['risk_note']}")

        pct_of_target = plan["total_estimated_profit"] / DAILY_PROFIT_TARGET * 100
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Trades Selected", len(trades),
                  help="Number of trades approved by both strategy agent (Claude) and risk agent. May be less than max_positions if conviction is low.")
        p2.metric("Est. Profit", f"${plan['total_estimated_profit']:,.0f}",
                  help="Sum of estimated profit across all approved trades, assuming all hit their 3% target. Actual results will vary.")
        p3.metric("Daily Target", f"${DAILY_PROFIT_TARGET:,}",
                  help="$1,000/day target. At $750/day average (backtest), capital compounds to reach this naturally in ~45 trading days.")
        p4.metric("Coverage", f"{pct_of_target:.0f}%",
                  delta=f"{pct_of_target - 100:+.0f}% vs target",
                  delta_color="normal" if pct_of_target >= 100 else "inverse",
                  help="How much of today's $1K target the selected trades could generate if all hit. <100% is common — not all trades will hit target.")

        if trades:
            df_t = pd.DataFrame(trades)
            display_cols = ["ticker", "action", "entry_price", "target_price", "stop_loss",
                            "shares", "position_size", "estimated_profit", "confidence", "status"]
            df_t = df_t[[c for c in display_cols if c in df_t.columns]]
            df_t = add_company_col(df_t)
            df_t["position_size"]    = df_t["position_size"].apply(lambda x: f"${x:,.0f}")
            df_t["estimated_profit"] = df_t["estimated_profit"].apply(lambda x: f"${x:,.0f}")
            st.dataframe(df_t, use_container_width=True)

            with st.expander("💬 Claude's Reasoning per Trade"):
                for t in trades:
                    conf_color = "green" if t["confidence"] == "HIGH" else (
                                 "orange" if t["confidence"] == "MEDIUM" else "gray")
                    st.markdown(
                        f"**{t['ticker']}** — "
                        f"<span style='color:{conf_color};font-weight:bold'>{t['confidence']}</span>: "
                        f"{t['reasoning']}",
                        unsafe_allow_html=True
                    )

        if sector_blocked:
            with st.expander(f"🏭 Sector Cap — {len(sector_blocked)} ticker(s) blocked",
                             help="V2d: max 3 positions per sector. Lowest-confidence excess trades dropped."):
                for s in sector_blocked:
                    st.markdown(f"- **{s['ticker']}** ({s['sector']}): {s['reason']}")

    st.divider()

    # ── STEP 3: Live Positions ──────────────────────────────────────
    st.subheader("3️⃣  Live Positions")

    total_unrealized = sum(p.get("unrealized_pnl", 0) for p in open_pos)
    total_realized   = sum(p.get("realized_pnl",   0) for p in today_closed)

    lp1, lp2, lp3, lp4 = st.columns(4)
    lp1.metric("Open Positions", len(open_pos),
               help="Positions currently active. Intraday agent checks prices every 30 min and closes on +3% target or -1% stop loss.")
    lp2.metric("Closed Today", len(today_closed),
               help="Positions closed so far today via TARGET hit (+3%), STOP hit (-1%), or EOD (4:30PM forced close).")
    lp3.metric("Unrealized P&L", fmt_pnl(total_unrealized),
               help="Paper profit/loss on still-open positions based on current price. Not locked in until position closes.")
    lp4.metric("Realized P&L", fmt_pnl(total_realized),
               help="Actual locked-in profit/loss from positions closed today. This is the real score for the day so far.")

    if open_pos:
        st.markdown("**Open**")
        for pos in open_pos:
            pnl  = pos.get("unrealized_pnl", 0)
            icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 3, 2])
            name = COMPANY_NAMES.get(pos["ticker"], "")
            label = f"{pos['ticker']} · {name}" if name else pos["ticker"]
            c1.markdown(f"**{icon} {label}** `{pos['action']}`")
            c2.markdown(f"Entry: **${pos['entry_price']:.2f}**")
            c3.markdown(f"Current: **${pos.get('current_price', 0):.2f}**")
            c4.markdown(f"Target: ${pos['target_price']:.2f} | Stop: ${pos['stop_loss']:.2f}")
            c5.markdown(
                f"<span style='color:{pnl_color(pnl)};font-weight:bold'>{fmt_pnl(pnl)}</span>",
                unsafe_allow_html=True
            )
            st.divider()

    if today_closed:
        st.markdown("**Closed Today**")
        df_cl = pd.DataFrame(today_closed)
        cl_cols = ["ticker", "action", "entry_price", "close_price", "shares", "realized_pnl", "close_reason"]
        df_cl = df_cl[[c for c in cl_cols if c in df_cl.columns]]
        df_cl = add_company_col(df_cl)
        df_cl["realized_pnl"] = df_cl["realized_pnl"].apply(fmt_pnl)
        st.dataframe(df_cl, use_container_width=True)

    if not open_pos and not today_closed:
        st.info("No positions yet.")


# ── POSITIONS ─────────────────────────────────────────────────────
elif page == "Positions":
    st.title("Open Positions")

    open_pos = db.select("positions", filters={"status": "OPEN"})

    if not open_pos:
        st.info("No open positions.")
    else:
        total_unrealized = sum(p.get("unrealized_pnl", 0) for p in open_pos)
        st.markdown(
            f"### Total Unrealized P&L: "
            f"<span style='color:{pnl_color(total_unrealized)}'>{fmt_pnl(total_unrealized)}</span>",
            unsafe_allow_html=True
        )
        st.markdown("---")
        for pos in open_pos:
            pnl  = pos.get("unrealized_pnl", 0)
            icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 2, 2])
            name = COMPANY_NAMES.get(pos["ticker"], "")
            label = f"{pos['ticker']} · {name}" if name else pos["ticker"]
            c1.markdown(f"**{icon} {label}** `{pos['action']}`")
            c2.markdown(f"Entry: **${pos['entry_price']:.2f}**")
            c3.markdown(f"Current: **${pos.get('current_price', 0):.2f}**")
            c4.markdown(f"Target: ${pos['target_price']:.2f} | Stop: ${pos['stop_loss']:.2f}")
            c5.markdown(
                f"<span style='color:{pnl_color(pnl)};font-weight:bold'>{fmt_pnl(pnl)}</span>",
                unsafe_allow_html=True
            )
            st.divider()

    st.subheader("Closed Today")
    today_str  = date.today().isoformat()
    all_closed = db.select("positions", filters={"status": "CLOSED"})
    today_closed = [p for p in all_closed if (p.get("closed_at") or "").startswith(today_str)]

    if today_closed:
        total_realized = sum(p.get("realized_pnl", 0) for p in today_closed)
        st.markdown(
            f"**Realized P&L today: "
            f"<span style='color:{pnl_color(total_realized)}'>{fmt_pnl(total_realized)}</span>**",
            unsafe_allow_html=True
        )
        df = pd.DataFrame(today_closed)[["ticker", "action", "entry_price", "close_price",
                                          "shares", "realized_pnl", "close_reason", "closed_at"]]
        df = add_company_col(df)
        df["realized_pnl"] = df["realized_pnl"].apply(fmt_pnl)
        st.dataframe(df, use_container_width=True)


# ── PERFORMANCE ───────────────────────────────────────────────────
elif page == "Performance":
    st.title("Performance History")

    perf = db.select("daily_performance", order="date", limit=30)
    if not perf:
        st.info("No performance data yet.")
        st.stop()

    df = pd.DataFrame(perf)

    total_pnl   = df["total_pnl"].sum()
    avg_daily   = df["total_pnl"].mean()
    win_days    = (df["total_pnl"] > 0).sum()
    total_days  = len(df)
    avg_win_rate= df["win_rate"].mean()
    latest_cap  = df.iloc[-1]["ending_capital"]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total P&L",       fmt_pnl(total_pnl))
    c2.metric("Avg Daily P&L",   fmt_pnl(avg_daily))
    c3.metric("Win Days",        f"{win_days}/{total_days}")
    c4.metric("Avg Trade Win %", f"{avg_win_rate:.1f}%")
    c5.metric("Portfolio Value", f"${latest_cap:,.0f}")

    st.markdown("---")
    st.subheader("Daily P&L")
    st.bar_chart(df[["date", "total_pnl"]].set_index("date"))

    st.subheader("Portfolio Value")
    st.line_chart(df[["date", "ending_capital"]].set_index("date"))

    st.subheader("Daily Log")
    display = df[["date", "total_pnl", "total_trades", "win_count", "loss_count",
                  "win_rate", "ending_capital", "notes"]].sort_values("date", ascending=False)
    display["total_pnl"]       = display["total_pnl"].apply(fmt_pnl)
    display["ending_capital"]  = display["ending_capital"].apply(lambda x: f"${x:,.0f}")
    st.dataframe(display, use_container_width=True)


# ── SCAN LOG ─────────────────────────────────────────────────────
elif page == "Scan Log":
    st.title("Scan Log")
    scans = db.select("scan_results", order="created_at", limit=20)
    if not scans:
        st.info("No scan results yet.")
        st.stop()

    for scan in scans:
        label = f"{scan['date']} — {scan['scan_type'].upper()}"
        with st.expander(label):
            results = scan.get("results", {})
            if results.get("skipped"):
                st.warning(f"Skipped: {results.get('reason')} | VIX: {results.get('vix')}")
            elif scan["scan_type"] == "premarket" and "candidates" in results:
                vix = results.get("vix")
                futures_bias = results.get("futures_bias", "")
                blackout = results.get("blackout_tickers", [])
                cands = results.get("candidates", [])

                m1, m2, m3 = st.columns(3)
                m1.metric("VIX", f"{vix:.1f}" if vix else "N/A")
                m2.metric("Futures", futures_bias or "N/A")
                m3.metric("Candidates", len(cands))

                if blackout:
                    st.caption(f"Earnings blocked: {', '.join(b['ticker'] for b in blackout)}")
                df = pd.DataFrame(cands)
                if not df.empty:
                    show_cols = ["ticker", "price", "technical_score", "rsi", "volume_ratio", "atr_pct"]
                    df = df[[c for c in show_cols if c in df.columns]]
                    df = add_company_col(df)
                    st.dataframe(df, use_container_width=True)
            else:
                st.json(results)
