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
def color_pnl(val):
    color = "green" if val > 0 else ("red" if val < 0 else "gray")
    return f"color: {color}; font-weight: bold"

def fmt_pnl(val):
    return f"+${val:,.2f}" if val > 0 else f"-${abs(val):,.2f}" if val < 0 else "$0.00"

# ── TODAY ─────────────────────────────────────────────────────────
if page == "Today":
    st.title("Today's Trade Plan")
    today = date.today().isoformat()

    plans = db.select("trade_plans", filters={"date": today})
    if not plans:
        plans = db.select("trade_plans", order="date", limit=1)
    if not plans:
        st.info("No trade plan generated yet for today. Premarket runs at 9:00 AM ET.")
        st.stop()

    plan = plans[0]
    st.markdown(f"**Market context:** {plan['market_context']}")

    col1, col2, col3 = st.columns(3)
    col1.metric("Est. Profit", f"${plan['total_estimated_profit']:,.0f}", delta=None)
    col2.metric("Target", f"${DAILY_PROFIT_TARGET:,}")
    col3.markdown(f"*{plan['risk_note']}*")

    st.markdown("---")
    trades = db.select("planned_trades", filters={"plan_id": plan["id"]})
    if trades:
        df = pd.DataFrame(trades)
        display_cols = ["ticker", "action", "entry_price", "target_price", "stop_loss",
                        "shares", "position_size", "estimated_profit", "confidence", "status"]
        df = df[[c for c in display_cols if c in df.columns]]
        df["position_size"] = df["position_size"].apply(lambda x: f"${x:,.0f}")
        df["estimated_profit"] = df["estimated_profit"].apply(lambda x: f"${x:,.0f}")
        st.dataframe(df, use_container_width=True)

        with st.expander("Reasoning"):
            for t in trades:
                st.markdown(f"**{t['ticker']}** ({t['confidence']}): {t['reasoning']}")

# ── POSITIONS ─────────────────────────────────────────────────────
elif page == "Positions":
    st.title("Open Positions")

    open_pos = db.select("positions", filters={"status": "OPEN"})

    if not open_pos:
        st.info("No open positions.")
    else:
        total_unrealized = sum(p.get("unrealized_pnl", 0) for p in open_pos)
        pnl_color = "green" if total_unrealized >= 0 else "red"
        st.markdown(f"### Total Unrealized P&L: <span style='color:{pnl_color}'>{fmt_pnl(total_unrealized)}</span>",
                    unsafe_allow_html=True)
        st.markdown("---")
        for pos in open_pos:
            pnl = pos.get("unrealized_pnl", 0)
            icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            with st.container():
                c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 2, 2])
                c1.markdown(f"**{icon} {pos['ticker']}** `{pos['action']}`")
                c2.markdown(f"Entry: **${pos['entry_price']:.2f}**")
                c3.markdown(f"Current: **${pos.get('current_price', 0):.2f}**")
                c4.markdown(f"Target: ${pos['target_price']:.2f} | Stop: ${pos['stop_loss']:.2f}")
                c5.markdown(f"P&L: **{fmt_pnl(pnl)}**")
                st.divider()

    st.subheader("Closed Today")
    today_str = date.today().isoformat()
    closed = db.select("positions", filters={"status": "CLOSED"})
    today_closed = [p for p in closed if (p.get("closed_at") or "").startswith(today_str)]

    if today_closed:
        total_realized = sum(p.get("realized_pnl", 0) for p in today_closed)
        pnl_color = "green" if total_realized >= 0 else "red"
        st.markdown(f"**Realized P&L today: <span style='color:{pnl_color}'>{fmt_pnl(total_realized)}</span>**",
                    unsafe_allow_html=True)
        df = pd.DataFrame(today_closed)[["ticker", "action", "entry_price", "close_price",
                                          "shares", "realized_pnl", "close_reason", "closed_at"]]
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

    # Summary metrics
    total_pnl   = df["total_pnl"].sum()
    avg_daily   = df["total_pnl"].mean()
    win_days    = (df["total_pnl"] > 0).sum()
    total_days  = len(df)
    avg_win_rate= df["win_rate"].mean()
    latest_cap  = df.iloc[-1]["ending_capital"]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total P&L",      fmt_pnl(total_pnl))
    c2.metric("Avg Daily P&L",  fmt_pnl(avg_daily))
    c3.metric("Win Days",       f"{win_days}/{total_days}")
    c4.metric("Avg Trade Win %",f"{avg_win_rate:.1f}%")
    c5.metric("Portfolio Value",f"${latest_cap:,.0f}")

    st.markdown("---")
    st.subheader("Daily P&L")
    chart_df = df[["date", "total_pnl"]].set_index("date")
    st.bar_chart(chart_df)

    st.subheader("Portfolio Value")
    cap_df = df[["date", "ending_capital"]].set_index("date")
    st.line_chart(cap_df)

    st.subheader("Daily Log")
    display = df[["date", "total_pnl", "total_trades", "win_count", "loss_count",
                  "win_rate", "ending_capital", "notes"]].sort_values("date", ascending=False)
    display["total_pnl"] = display["total_pnl"].apply(fmt_pnl)
    display["ending_capital"] = display["ending_capital"].apply(lambda x: f"${x:,.0f}")
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
            if scan["scan_type"] == "premarket" and "candidates" in results:
                df = pd.DataFrame(results["candidates"])
                if not df.empty:
                    show_cols = ["ticker", "price", "technical_score", "rsi",
                                 "volume_ratio", "atr_pct", "signals"]
                    df = df[[c for c in show_cols if c in df.columns]]
                    st.dataframe(df, use_container_width=True)
            else:
                st.json(results)
