import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
for _key in ["ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_KEY", "DASHBOARD_PASSWORD"]:
    if _key in st.secrets:
        os.environ[_key] = st.secrets[_key]

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import yfinance as yf
from datetime import date, datetime
from core import db
from config.settings import DASHBOARD_PASSWORD, TOTAL_CAPITAL, DAILY_PROFIT_TARGET, ETF_UNIVERSE, TRAIL_PCT, LOCK_IN_TRAIL_PCT, MIN_REWARD_RISK, DAILY_LOCK_IN_TARGET, DAILY_BONUS_TARGET
from config.company_names import COMPANY_NAMES

_ETF_SET = set(ETF_UNIVERSE)

@st.cache_data(ttl=3600)
def get_sector(ticker: str) -> str:
    if ticker in _ETF_SET:
        return "ETF"
    try:
        info = yf.Ticker(ticker).info
        return info.get("sector") or "Unknown"
    except Exception:
        return "Unknown"


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
page = st.sidebar.radio("View", ["Summary", "Today", "Positions", "Performance", "Scan Log"])
st.sidebar.markdown("---")
if st.sidebar.button("🔄 Refresh"):
    st.rerun()
st.sidebar.caption(f"Last refresh: {datetime.now().strftime('%H:%M:%S')}")


# ── Helpers ───────────────────────────────────────────────────────
def fmt_pnl(val):
    return f"+${val:,.2f}" if val > 0 else f"-${abs(val):,.2f}" if val < 0 else "$0.00"

def pnl_color(val):
    return "green" if val > 0 else "red" if val < 0 else "gray"

def fmt_stop(pos, tight=False):
    """Show trailing stop level. In tailwind mode (tight=True), reflects tighter 0.5% trail."""
    trail = LOCK_IN_TRAIL_PCT if tight else TRAIL_PCT
    if pos.get("native_trail_active"):
        if tight:
            return f"Trail **{LOCK_IN_TRAIL_PCT*100:.1f}%** ↑ (native · tailwind)"
        return f"Trail **{TRAIL_PCT*100:.0f}%** ↑ (native)"
    hw       = float(pos.get("high_watermark") or pos.get("entry_price", 0))
    eff_stop = max(pos["stop_loss"], round(hw * (1 - trail), 4))
    if eff_stop > pos["stop_loss"]:
        label = "Tight Trail" if tight else "Trail"
        return f"**{label}** ${eff_stop:.2f} ↑"
    return f"Stop ${pos['stop_loss']:.2f}"


# ── SUMMARY ───────────────────────────────────────────────────────
if page == "Summary":
    today = date.today().isoformat()

    # Load latest premarket scan (today or most recent)
    scans = db.select("scan_results", filters={"date": today, "scan_type": "premarket"})
    is_stale = not scans
    if is_stale:
        scans = db.select("scan_results", filters={"scan_type": "premarket"}, order="created_at", limit=1)
    scan     = scans[0] if scans else None
    run_date = scan["date"] if scan else today

    plans  = db.select("trade_plans", filters={"date": run_date})
    plan   = plans[0] if plans else None
    trades = db.select("planned_trades", filters={"plan_id": plan["id"]}) if plan else []

    plan_trade_ids = {t["id"] for t in trades}
    all_open   = db.select("positions", filters={"status": "OPEN"})
    open_pos   = [p for p in all_open if p["planned_trade_id"] in plan_trade_ids]
    all_closed = db.select("positions", filters={"status": "CLOSED"})
    # Scope closed positions to this plan only; exclude manual CLEANUP entries
    run_closed = [
        p for p in all_closed
        if (p.get("closed_at") or "").startswith(run_date)
        and p.get("planned_trade_id") in plan_trade_ids
        and p.get("close_reason") not in ("CLEANUP", "UNFILLED")
    ]

    # Only show planned_trades that actually have a position (open or real closed).
    # This filters out ghost rows from repeated test premarket runs that opened
    # different tickers — the cap is on positions, not planned_trades rows.
    executed_trade_ids = {p["planned_trade_id"] for p in open_pos + run_closed}
    trades = [t for t in trades if t["id"] in executed_trade_ids]

    realized   = sum(p.get("realized_pnl",   0) or 0 for p in run_closed)
    unrealized = sum(p.get("unrealized_pnl", 0) or 0 for p in open_pos)
    total_pnl  = realized + unrealized
    pct_return = total_pnl / TOTAL_CAPITAL * 100
    anticipated = plan["total_estimated_profit"] if plan else 0
    coverage    = anticipated / DAILY_PROFIT_TARGET * 100 if anticipated else 0

    # Tiered lock-in state for display
    realized_ex_lockin = sum(
        p.get("realized_pnl", 0) or 0 for p in run_closed
        if p.get("close_reason") not in ("LOCK_IN",)
    )
    in_tailwind   = realized_ex_lockin >= DAILY_LOCK_IN_TARGET and total_pnl < DAILY_BONUS_TARGET and bool(open_pos)
    exceptional_day = total_pnl >= DAILY_BONUS_TARGET

    # ── Header ────────────────────────────────────────────────────
    h1, h2 = st.columns([4, 1])
    h1.title(f"📊 Summary — {run_date}")
    if is_stale:
        badge, badge_color = "STALE", "#7f8c8d"
    elif plan:
        badge, badge_color = "TRADING", "#27ae60"
    else:
        badge, badge_color = "PENDING", "#7f8c8d"
    h2.markdown(
        f"<div style='text-align:right;padding-top:14px'>"
        f"<span style='background:{badge_color};color:white;padding:6px 14px;"
        f"border-radius:6px;font-weight:bold;font-size:16px'>{badge}</span></div>",
        unsafe_allow_html=True
    )
    if is_stale:
        st.warning(f"⚠️ Showing {run_date} data — no premarket run today yet.")

    st.divider()

    # ── KPI Row ───────────────────────────────────────────────────
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Capital", f"${TOTAL_CAPITAL:,}")
    k2.metric("Today's P&L",
              fmt_pnl(total_pnl),
              delta=f"{pct_return:+.2f}% return",
              delta_color="normal" if total_pnl >= 0 else "inverse")
    k3.metric("Realized", fmt_pnl(realized))
    k4.metric("Unrealized", fmt_pnl(unrealized))
    k5.metric("Anticipated", f"${anticipated:,.0f}",
              delta=f"{coverage:.0f}% of ${DAILY_PROFIT_TARGET:,} target",
              delta_color="normal" if coverage >= 100 else "inverse")
    k6.metric("% Return", f"{pct_return:+.2f}%")

    st.divider()

    # ── Trade Stats Row ───────────────────────────────────────────
    won  = [p for p in run_closed if (p.get("realized_pnl") or 0) > 0]
    lost = [p for p in run_closed if (p.get("realized_pnl") or 0) <= 0]
    win_rate = len(won) / len(run_closed) * 100 if run_closed else 0

    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Open Positions", len(open_pos))
    t2.metric("Closed Today",   len(run_closed))
    t3.metric("Win Rate Today", f"{win_rate:.0f}%" if run_closed else "—",
              delta=f"{len(won)}W / {len(lost)}L" if run_closed else None,
              delta_color="off")
    t4.metric("Total Trades",   len(trades), help="Trades selected by Claude today")

    # ── Tiered lock-in banner ─────────────────────────────────────
    if exceptional_day:
        st.success(
            f"🏆 **Exceptional day locked** — Total P&L **{fmt_pnl(total_pnl)}** hit the "
            f"${DAILY_BONUS_TARGET:,} ceiling. All positions closed and gains protected."
        )
    elif in_tailwind:
        progress = max(0.0, min(1.0, (total_pnl - DAILY_LOCK_IN_TARGET) / (DAILY_BONUS_TARGET - DAILY_LOCK_IN_TARGET)))
        st.info(
            f"🚀 **Tailwind Mode** — **{fmt_pnl(realized_ex_lockin)}** realized and secured. "
            f"{len(open_pos)} position(s) riding to **${DAILY_BONUS_TARGET:,}** with tight "
            f"{LOCK_IN_TRAIL_PCT*100:.1f}% trail."
        )
        st.progress(progress, text=f"Total {fmt_pnl(total_pnl)}  →  ${DAILY_BONUS_TARGET:,} ceiling")

    # Build position lookup (planned_trade_id → position)
    pos_by_trade = {p["planned_trade_id"]: p for p in open_pos + run_closed}

    def trade_status(trade_id):
        """Return (status_label, pnl_value) for a planned trade."""
        pos = pos_by_trade.get(trade_id)
        if not pos:
            return "⏳ Pending", 0
        if pos["status"] == "OPEN":
            if in_tailwind:
                return "🚀 Riding Tailwind", pos.get("unrealized_pnl", 0) or 0
            return "🟢 In Flight", pos.get("unrealized_pnl", 0) or 0
        reason = (pos.get("close_reason") or "Closed").upper()
        pnl = pos.get("realized_pnl", 0) or 0
        if reason == "STOP":
            return ("🔶 Trail Stop" if pnl > 0 else "🔴 Stop Hit"), pnl
        label_map = {
            "TARGET":  "✅ Target Hit",
            "EOD":     "⏰ EOD Close",
            "LOCK_IN": "🎯 Day Locked",
        }
        return label_map.get(reason, f"⚪ {reason.title()}"), pnl

    st.divider()

    # ── In Flight ─────────────────────────────────────────────────
    section_header = "🚀 Riding Tailwind" if in_tailwind else ("🏆 Exceptional Day" if exceptional_day else "🟢 In Flight")
    st.subheader(f"{section_header} — {len(open_pos)} position{'s' if len(open_pos) != 1 else ''}")
    if open_pos:
        tail_badge = (
            "<span style='background:#1e8449;color:white;padding:2px 8px;"
            "border-radius:4px;font-size:11px;margin-left:6px'>🚀 tailwind</span>"
            if in_tailwind else ""
        )
        for pos in open_pos:
            pnl  = pos.get("unrealized_pnl", 0) or 0
            icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            name = COMPANY_NAMES.get(pos["ticker"], "")
            label = f"{pos['ticker']} · {name}" if name else pos["ticker"]
            c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 3, 2])
            c1.markdown(f"**{icon} {label}**{tail_badge}", unsafe_allow_html=True)
            c2.markdown(f"Entry: **${pos['entry_price']:.2f}**")
            c3.markdown(f"Now: **${pos.get('current_price', 0):.2f}**")
            c4.markdown(f"Target ${pos['target_price']:.2f}  ·  {fmt_stop(pos, tight=in_tailwind)}")
            c5.markdown(
                f"<span style='color:{pnl_color(pnl)};font-weight:bold;font-size:16px'>{fmt_pnl(pnl)}</span>",
                unsafe_allow_html=True
            )
        st.markdown("")
    else:
        st.caption("No open positions right now.")

    st.divider()

    # ── Today's Plan ──────────────────────────────────────────────
    all_plan_trades = db.select("planned_trades", filters={"plan_id": plan["id"]}) if plan else []
    # De-duplicate by ticker, keep most recent
    seen_t: set = set()
    deduped_plan: list = []
    for t in reversed(all_plan_trades):
        if t["ticker"] not in seen_t:
            seen_t.add(t["ticker"])
            deduped_plan.append(t)

    st.subheader(f"📋 Today's Plan — {len(deduped_plan)} trade{'s' if len(deduped_plan) != 1 else ''} selected")
    if deduped_plan:
        plan_rows = []
        for t in deduped_plan:
            status_label, pnl_val = trade_status(t["id"])
            plan_rows.append({
                "Status":     status_label,
                "Ticker":     t["ticker"],
                "Company":    COMPANY_NAMES.get(t["ticker"], ""),
                "Conf.":      t["confidence"],
                "Entry":      f"${t['entry_price']:.2f}",
                "Target":     f"${t['target_price']:.2f}",
                "Stop":       f"${t['stop_loss']:.2f}",
                "Size":       f"${t['position_size']:,.0f}",
                "Est. P&L":   f"${t['estimated_profit']:,.0f}",
                "Actual P&L": fmt_pnl(pnl_val) if pnl_val != 0 else "—",
            })
        df_plan = pd.DataFrame(plan_rows)
        st.dataframe(df_plan, use_container_width=True, hide_index=True)

        with st.expander("💬 Claude's Reasoning"):
            for t in deduped_plan:
                conf_color = "green" if t["confidence"] == "HIGH" else (
                             "orange" if t["confidence"] == "MEDIUM" else "gray")
                st.markdown(
                    f"**{t['ticker']}** — "
                    f"<span style='color:{conf_color};font-weight:bold'>{t['confidence']}</span>: "
                    f"{t.get('reasoning', '—')}",
                    unsafe_allow_html=True
                )
    else:
        st.caption("No trade plan yet.")

    st.divider()

    # ── Trade Heatmap ─────────────────────────────────────────────
    st.subheader("🗺️ Trade Heatmap — P&L by Stock")
    all_heatmap_trades = deduped_plan if deduped_plan else trades
    if all_heatmap_trades:
        hm_labels, hm_pnl, hm_size, hm_text, hm_hover = [], [], [], [], []
        for t in all_heatmap_trades:
            status_label, pnl_val = trade_status(t["id"])
            ticker = t["ticker"]
            company = COMPANY_NAMES.get(ticker, ticker)
            hm_labels.append(ticker)
            hm_pnl.append(pnl_val)
            hm_size.append(t.get("position_size", 5000))
            hm_text.append(f"{ticker}<br>{fmt_pnl(pnl_val)}")
            hm_hover.append(
                f"<b>{ticker}</b> — {company}<br>"
                f"Status: {status_label}<br>"
                f"P&L: {fmt_pnl(pnl_val)}<br>"
                f"Entry: ${t['entry_price']:.2f} → Target: ${t['target_price']:.2f}"
            )

        max_abs = max((abs(v) for v in hm_pnl), default=1) or 1
        fig_hm = go.Figure(go.Treemap(
            labels=hm_labels,
            parents=[""] * len(hm_labels),
            values=hm_size,
            text=hm_text,
            hovertemplate="%{customdata}<extra></extra>",
            customdata=hm_hover,
            textinfo="text",
            marker=dict(
                colors=hm_pnl,
                colorscale=[
                    [0.0,  "#c0392b"],
                    [0.45, "#e74c3c"],
                    [0.5,  "#95a5a6"],
                    [0.55, "#27ae60"],
                    [1.0,  "#1e8449"],
                ],
                cmid=0,
                showscale=True,
                colorbar=dict(title="P&L ($)", thickness=12),
            ),
        ))
        fig_hm.update_layout(
            height=380,
            margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_hm, use_container_width=True)
        st.caption("Block size = position size. Color = P&L (green = profit, red = loss, gray = pending/flat).")
    else:
        st.info("No trades to display yet.")


# ── TODAY WORKFLOW ─────────────────────────────────────────────────
elif page == "Today":
    today = date.today().isoformat()

    # Load scan result (premarket) — today only; fall back to most recent with staleness flag
    scans = db.select("scan_results", filters={"date": today, "scan_type": "premarket"})
    is_stale = not scans
    if is_stale:
        scans = db.select("scan_results", filters={"scan_type": "premarket"}, order="created_at", limit=1)
    scan = scans[0] if scans else None
    results = scan["results"] if scan else {}
    run_date = scan["date"] if scan else today

    # Load trade plan + trades for that date
    plans = db.select("trade_plans", filters={"date": run_date})
    plan = plans[0] if plans else None
    trades = db.select("planned_trades", filters={"plan_id": plan["id"]}) if plan else []

    # Positions scoped to this plan only; exclude manual CLEANUP entries
    plan_trade_ids = {t["id"] for t in trades}
    all_open = db.select("positions", filters={"status": "OPEN"})
    open_pos = [p for p in all_open if p["planned_trade_id"] in plan_trade_ids]
    all_closed = db.select("positions", filters={"status": "CLOSED"})
    run_closed = [
        p for p in all_closed
        if (p.get("closed_at") or "").startswith(run_date)
        and p.get("planned_trade_id") in plan_trade_ids
        and p.get("close_reason") not in ("CLEANUP", "UNFILLED")
    ]
    # Filter trades to only those that actually executed (have a position)
    executed_trade_ids = {p["planned_trade_id"] for p in open_pos + run_closed}
    trades = [t for t in trades if t["id"] in executed_trade_ids]

    # Unpack scan results
    skipped       = results.get("skipped", False)
    vix           = results.get("vix")
    fear_greed    = results.get("fear_greed")
    econ_events   = results.get("economic_events", [])
    futures       = results.get("futures", {})
    futures_bias  = results.get("futures_bias", "NEUTRAL")
    intl          = results.get("intl_markets", {})
    candidates        = results.get("candidates", [])
    blackout          = results.get("blackout_tickers", [])
    sector_blocked    = results.get("sector_blocked", [])
    guardrail_blocked = results.get("guardrail_blocked", [])

    # ── Header ─────────────────────────────────────────────────────
    if is_stale and results:
        badge, badge_color = "STALE", "#7f8c8d"
    elif skipped:
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

    if is_stale:
        st.warning(f"⚠️ No premarket run for today yet — showing {run_date} data. Next run: 9:00 AM ET on the next trading day.")

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

        if guardrail_blocked:
            with st.expander(f"🛑 Guardrails — {len(guardrail_blocked)} ticker(s) blocked",
                             help="V5: safety checks — action whitelist, ticker whitelist, duplicate guard, price sanity, capital check, daily loss limit."):
                for g in guardrail_blocked:
                    st.markdown(f"- **{g['ticker']}**: {g['reason']}")

    st.divider()

    # ── STEP 3: Live Positions ──────────────────────────────────────
    st.subheader("3️⃣  Live Positions")

    total_unrealized = sum(p.get("unrealized_pnl", 0) for p in open_pos)
    total_realized   = sum(p.get("realized_pnl",   0) for p in run_closed)
    closed_label     = "Closed Today" if not is_stale else f"Closed {run_date}"

    # Tailwind state for Today tab
    _realized_ex = sum(
        p.get("realized_pnl", 0) or 0 for p in run_closed
        if p.get("close_reason") not in ("LOCK_IN",)
    )
    _total = total_realized + total_unrealized
    _in_tailwind    = _realized_ex >= DAILY_LOCK_IN_TARGET and _total < DAILY_BONUS_TARGET and bool(open_pos)
    _exceptional    = _total >= DAILY_BONUS_TARGET

    if _exceptional:
        st.success(f"🏆 **Exceptional day locked** — Total P&L **{fmt_pnl(_total)}** hit ${DAILY_BONUS_TARGET:,} ceiling.")
    elif _in_tailwind:
        _progress = max(0.0, min(1.0, (_total - DAILY_LOCK_IN_TARGET) / (DAILY_BONUS_TARGET - DAILY_LOCK_IN_TARGET)))
        st.info(
            f"🚀 **Tailwind Mode** — **{fmt_pnl(_realized_ex)}** secured. "
            f"{len(open_pos)} position(s) riding to **${DAILY_BONUS_TARGET:,}** with {LOCK_IN_TRAIL_PCT*100:.1f}% trail."
        )
        st.progress(_progress, text=f"Total {fmt_pnl(_total)}  →  ${DAILY_BONUS_TARGET:,} ceiling")

    lp1, lp2, lp3, lp4 = st.columns(4)
    lp1.metric("Open Positions", len(open_pos),
               help="Positions currently active. Intraday agent checks prices every 30 min and closes on +3% target or -1% stop loss.")
    lp2.metric(closed_label, len(run_closed),
               help="Positions closed via TARGET hit (+3%), STOP hit (-1%), or EOD (4:30PM forced close).")
    lp3.metric("Unrealized P&L", fmt_pnl(total_unrealized),
               help="Paper profit/loss on still-open positions based on current price. Not locked in until position closes.")
    lp4.metric("Realized P&L", fmt_pnl(total_realized),
               help="Actual locked-in profit/loss from positions closed today. This is the real score for the day so far.")

    if open_pos:
        open_header = "**🚀 Riding Tailwind**" if _in_tailwind else "**Open**"
        st.markdown(open_header)
        _tail_badge = (
            "<span style='background:#1e8449;color:white;padding:2px 8px;"
            "border-radius:4px;font-size:11px;margin-left:6px'>🚀 tailwind</span>"
            if _in_tailwind else ""
        )
        for pos in open_pos:
            pnl  = pos.get("unrealized_pnl", 0)
            icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 3, 2])
            name = COMPANY_NAMES.get(pos["ticker"], "")
            label = f"{pos['ticker']} · {name}" if name else pos["ticker"]
            c1.markdown(f"**{icon} {label}** `{pos['action']}`{_tail_badge}", unsafe_allow_html=True)
            c2.markdown(f"Entry: **${pos['entry_price']:.2f}**")
            c3.markdown(f"Current: **${pos.get('current_price', 0):.2f}**")
            c4.markdown(f"Target: ${pos['target_price']:.2f} | {fmt_stop(pos, tight=_in_tailwind)}")
            c5.markdown(
                f"<span style='color:{pnl_color(pnl)};font-weight:bold'>{fmt_pnl(pnl)}</span>",
                unsafe_allow_html=True
            )
            st.divider()

    if run_closed:
        st.markdown(f"**{closed_label}**")
        df_cl = pd.DataFrame(run_closed)
        cl_cols = ["ticker", "action", "entry_price", "close_price", "shares", "realized_pnl", "close_reason"]
        df_cl = df_cl[[c for c in cl_cols if c in df_cl.columns]]
        df_cl = add_company_col(df_cl)
        df_cl["realized_pnl"] = df_cl["realized_pnl"].apply(fmt_pnl)
        st.dataframe(df_cl, use_container_width=True)

    if not open_pos and not run_closed:
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
            c4.markdown(f"Target: ${pos['target_price']:.2f} | {fmt_stop(pos)}")
            c5.markdown(
                f"<span style='color:{pnl_color(pnl)};font-weight:bold'>{fmt_pnl(pnl)}</span>",
                unsafe_allow_html=True
            )
            st.divider()

    st.subheader("Closed Today")
    today_str  = date.today().isoformat()
    all_closed = db.select("positions", filters={"status": "CLOSED"})
    run_closed = [p for p in all_closed if (p.get("closed_at") or "").startswith(today_str)]

    if run_closed:
        total_realized = sum(p.get("realized_pnl", 0) for p in run_closed)
        st.markdown(
            f"**Realized P&L today: "
            f"<span style='color:{pnl_color(total_realized)}'>{fmt_pnl(total_realized)}</span>**",
            unsafe_allow_html=True
        )
        df = pd.DataFrame(run_closed)[["ticker", "action", "entry_price", "close_price",
                                          "shares", "realized_pnl", "close_reason", "closed_at"]]
        df = add_company_col(df)
        df["realized_pnl"] = df["realized_pnl"].apply(fmt_pnl)
        st.dataframe(df, use_container_width=True)


# ── PERFORMANCE ───────────────────────────────────────────────────
elif page == "Performance":
    st.title("Performance History")

    # ── Agent Scorecard (latest eval) ────────────────────────────
    eval_rows = db.select("scan_results", filters={"scan_type": "eval"}, order="created_at", limit=1)
    if eval_rows:
        ev = eval_rows[0].get("results", {})
        grade_label = {"A": "Excellent", "B": "Good", "C": "Mediocre", "D": "Poor"}.get(ev.get("grade", ""), "")
        days = ev.get("days", 0)

        with st.expander(f"Agent Scorecard — {eval_rows[0]['date']} · {days}-day window · Grade **{ev.get('grade','?')}** ({grade_label})", expanded=True):

            # ── VERDICT ──────────────────────────────────────────────
            st.markdown("#### Verdict")
            wins, watchs, actions = [], [], []

            pnl      = ev.get("avg_daily_pnl", 0)
            pnl_pct  = pnl / DAILY_PROFIT_TARGET * 100 if DAILY_PROFIT_TARGET else 0
            win_days = ev.get("win_days", 0)
            wd_pct   = win_days / days * 100 if days else 0
            wr       = ev.get("avg_win_rate", 0)
            rr       = ev.get("actual_rr", 0)
            cr       = ev.get("close_reasons", {})
            total_cr = sum(cr.values()) or 1
            tgt_pct  = cr.get("TARGET", 0) / total_cr * 100

            if pnl >= DAILY_PROFIT_TARGET:
                wins.append(f"Avg daily P&L ${pnl:,.0f} — on or above ${DAILY_PROFIT_TARGET:,} target")
            elif pnl_pct >= 60:
                watchs.append(f"Avg daily P&L ${pnl:,.0f} is {pnl_pct:.0f}% of ${DAILY_PROFIT_TARGET:,} target")
            else:
                actions.append(f"Avg daily P&L ${pnl:,.0f} well below ${DAILY_PROFIT_TARGET:,} target ({pnl_pct:.0f}%)")

            if wd_pct >= 80:
                wins.append(f"{win_days}/{days} profitable days ({wd_pct:.0f}%) — consistent execution")
            elif wd_pct >= 60:
                watchs.append(f"{win_days}/{days} profitable days ({wd_pct:.0f}%) — more losing days than ideal")
            else:
                actions.append(f"Only {win_days}/{days} profitable days — strategy inconsistency")

            if wr >= 60:
                wins.append(f"{wr:.0f}% trade win rate — well above 25% break-even for 3:1 R:R")
            elif wr >= 50:
                watchs.append(f"{wr:.0f}% trade win rate — above break-even but room to improve")
            else:
                watchs.append(f"{wr:.0f}% trade win rate — approaching break-even; tighten entry criteria")

            if rr >= 3.0:
                wins.append(f"Reward:risk {rr:.1f}x — meeting 3:1 target")
            elif rr >= 2.0:
                watchs.append(f"Reward:risk {rr:.1f}x — below 3.0x target; losers running slightly large")
            else:
                actions.append(f"Reward:risk {rr:.1f}x — well below target; review stops and targets")

            if tgt_pct >= 50:
                wins.append(f"{tgt_pct:.0f}% of exits hit target — momentum strategy executing as designed")
            elif cr.get("STOP", 0) / total_cr > 0.5:
                watchs.append(f"More stops than targets — entries may be too late in the move")

            cs = ev.get("confidence_stats", {})
            high, low = cs.get("HIGH"), cs.get("LOW")
            if high and low:
                if high["avg_pnl"] > low["avg_pnl"]:
                    wins.append(f"HIGH confidence trades earning ${high['avg_pnl']:,.0f} avg vs ${low['avg_pnl']:,.0f} for LOW — sizing justified")
                else:
                    watchs.append(f"LOW outperforming HIGH (${low['avg_pnl']:,.0f} vs ${high['avg_pnl']:,.0f}) — confidence signal unreliable")

            native = ev.get("native_trail")
            if native:
                nt_exits = native.get("exits", {}).get("NATIVE_TRAIL", 0)
                if nt_exits > 0:
                    wins.append(f"Native trailing stop confirmed — {nt_exits} clean exits, no double-sells")
                else:
                    watchs.append("Native trailing stop enabled but no stop exits yet — need a reversal day to validate")

            orphaned = ev.get("orphaned", [])
            if orphaned:
                actions.append(f"{len(orphaned)} orphaned position(s) stuck OPEN from a prior day")
            if ev.get("rr_violations"):
                actions.append(f"{len(ev['rr_violations'])} trade(s) submitted below {MIN_REWARD_RISK}x R:R — Claude constraint drift")
            if ev.get("duplicate_count", 0) > 0:
                actions.append(f"{ev['duplicate_count']} duplicate ticker(s) same day — guardrail may have failed")
            attempted = ev.get("total_attempted", 1) or 1
            unfill_pct = ev.get("unfilled_count", 0) / attempted * 100
            if unfill_pct >= 15:
                actions.append(f"{unfill_pct:.0f}% unfilled rate — limit entry price too tight")
            elif unfill_pct >= 5:
                watchs.append(f"{unfill_pct:.0f}% unfilled rate — monitor; rising trend is a problem")

            v1, v2, v3 = st.columns(3)
            with v1:
                st.markdown("**✅ What's working**")
                for w in wins:
                    st.markdown(f"<span style='color:#1e8449'>• {w}</span>", unsafe_allow_html=True)
                if not wins:
                    st.markdown("<span style='color:#95a5a6'>— none yet</span>", unsafe_allow_html=True)
            with v2:
                st.markdown("**⚠️ Watch**")
                for w in watchs:
                    st.markdown(f"<span style='color:#f39c12'>• {w}</span>", unsafe_allow_html=True)
                if not watchs:
                    st.markdown("<span style='color:#95a5a6'>— none</span>", unsafe_allow_html=True)
            with v3:
                st.markdown("**❌ Action required**")
                for a in actions:
                    st.markdown(f"<span style='color:#e74c3c'>• {a}</span>", unsafe_allow_html=True)
                if not actions:
                    st.markdown("<span style='color:#1e8449'>✅ No action required</span>", unsafe_allow_html=True)

            st.markdown("---")

            # ── Score metrics ─────────────────────────────────────────
            st.markdown("#### Score Breakdown")
            sc1, sc2, sc3, sc4, sc5, sc6 = st.columns(6)
            sc1.metric("Score",          f"{ev.get('score', 0):.0f} / 100")
            sc2.metric("Avg Daily P&L",  f"${ev.get('avg_daily_pnl', 0):,.0f}",
                       delta=f"target ${DAILY_PROFIT_TARGET:,}")
            sc3.metric("Win Days",       f"{win_days} / {days}")
            sc4.metric("Trade Win Rate", f"{ev.get('avg_win_rate', 0):.1f}%")
            sc5.metric("Actual R:R",     f"{ev.get('actual_rr', 0):.2f}x")
            sc6.metric("Ann. Return",    f"{ev.get('ann_return', 0):+.0f}%")

            ps = ev.get("pnl_score", 0)
            ws = ev.get("winday_score", 0)
            rs = ev.get("winrate_score", 0)
            st.caption(f"Score components: P&L {ps:.0f}/40 pts · Win days {ws:.0f}/30 pts · Win rate {rs:.0f}/30 pts")

            st.markdown("---")

            # ── Trade breakdown + Recommendations ─────────────────────
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("**Exit reasons**  *(healthy: TARGET >50%, STOP <35%, EOD <20%)*")
                cr_rows = [{"Exit": k, "Count": v, "%": f"{v/total_cr*100:.0f}%"}
                           for k, v in sorted(cr.items(), key=lambda x: -x[1])]
                if cr_rows:
                    st.dataframe(pd.DataFrame(cr_rows), use_container_width=True, hide_index=True)
                if ev.get("best_ticker"):
                    st.success(f"Best: {ev['best_ticker']}  +${ev['best_pnl']:,.2f}")
                if ev.get("worst_ticker"):
                    st.error(f"Worst: {ev['worst_ticker']}  ${ev['worst_pnl']:,.2f}")
            with col_b:
                st.markdown("**Recommendations**")
                for rec in ev.get("recommendations", []):
                    st.markdown(f"• {rec}")

            st.markdown("---")

            # ── Integrity + Claude quality ─────────────────────────────
            st.markdown("#### Integrity & Claude Quality")
            int_col, qual_col = st.columns(2)

            with int_col:
                st.markdown("**Integrity checks**")
                unfill_n = ev.get("unfilled_count", 0)
                unfill_flag = "✅" if unfill_pct < 10 else "⚠️"
                st.markdown(f"{unfill_flag} UNFILLED rate: **{unfill_n}** ({unfill_pct:.0f}%)")
                st.markdown(f"{'✅' if not orphaned else '❌'} Orphaned open positions: **{len(orphaned)}**")
                dups = ev.get("duplicate_count", 0)
                st.markdown(f"{'✅' if dups == 0 else '❌'} Duplicate tickers same day: **{dups}**")
                missing = ev.get("missing_exit", 0)
                st.markdown(f"{'✅' if missing == 0 else '⚠️'} Missing exit_mechanism: **{missing}**")
                st.markdown(f"📉 Loss-limit days: **{ev.get('loss_limit_days', 0)}** / {days}")
                st.markdown(f"🎯 Lock-in days: **{ev.get('lock_in_days', 0)}** / {days}")

            with qual_col:
                st.markdown("**Claude quality checks**")
                rr_v = ev.get("rr_violations", [])
                st.markdown(f"{'✅' if not rr_v else '❌'} R:R violations: **{len(rr_v)}** trades below {MIN_REWARD_RISK}x")
                if rr_v:
                    for v in rr_v:
                        st.caption(f"  → {v['ticker']} R:R {v['rr']:.2f}x")
                sz_v = ev.get("size_violations", [])
                st.markdown(f"{'✅' if not sz_v else '⚠️'} Position size violations: **{len(sz_v)}**")

                st.markdown("**Confidence cohort**  *(validates HIGH $7K / MEDIUM $6K / LOW $5K)*")
                conf_rows = []
                for level in ("HIGH", "MEDIUM", "LOW"):
                    s = cs.get(level)
                    if s:
                        conf_rows.append({
                            "Level":    level,
                            "Trades":   s["count"],
                            "Win %":    f"{s['win_rate']:.1f}%",
                            "Avg P&L":  f"${s['avg_pnl']:,.2f}",
                            "Total":    f"${s['total_pnl']:,.0f}",
                        })
                if conf_rows:
                    st.dataframe(pd.DataFrame(conf_rows), use_container_width=True, hide_index=True)
                if high and low:
                    delta = high["avg_pnl"] - low["avg_pnl"]
                    if delta > 0:
                        st.success(f"HIGH outperforming LOW by ${delta:,.2f} avg — sizing justified")
                    else:
                        st.warning(f"LOW outperforming HIGH by ${abs(delta):,.2f} — confidence signal unreliable")

            st.markdown("---")

            # ── Trailing stop validation ───────────────────────────────
            st.markdown("#### Trailing Stop Validation")
            manual = ev.get("manual_trail")
            ts1, ts2 = st.columns(2)

            for col, label, cohort in [(ts1, "Native trail (Alpaca)", native), (ts2, "Manual trail (high_watermark)", manual)]:
                with col:
                    st.markdown(f"**{label}**")
                    if cohort:
                        st.metric("Trades",   cohort["count"])
                        st.metric("Win rate", f"{cohort['win_rate']:.1f}%")
                        st.metric("Avg P&L",  f"${cohort['avg_pnl']:,.2f}")
                        exits = cohort.get("exits", {})
                        if exits:
                            st.caption("Exits: " + "  |  ".join(f"{k}: {v}" for k, v in sorted(exits.items())))
                    else:
                        st.caption("No data yet")

            if native and manual:
                pnl_d = native["avg_pnl"] - manual["avg_pnl"]
                wr_d  = native["win_rate"] - manual["win_rate"]
                st.markdown(f"**Native vs manual:** avg P&L {pnl_d:+.2f}  ·  win rate {wr_d:+.1f}%")
            if native:
                nt_exits = native.get("exits", {}).get("NATIVE_TRAIL", 0)
                if nt_exits > 0:
                    st.success(f"✅ {nt_exits} native trail exits confirmed — no double-sells detected")
                else:
                    st.info("⏳ No NATIVE_TRAIL exits yet — stop hasn't fired; need a reversal day to validate")

            st.markdown("---")

            # ── Tailwind Analysis ──────────────────────────────────────
            st.markdown("#### Tailwind Analysis")
            st.caption(f"Extra P&L captured by letting winners ride past ${DAILY_LOCK_IN_TARGET:,} floor → ${DAILY_BONUS_TARGET:,} ceiling.")
            tw = ev.get("tailwind")

            if not tw:
                st.info(f"No tailwind days yet — realized P&L hasn't crossed ${DAILY_LOCK_IN_TARGET:,} in this eval window.")
            else:
                tw1, tw2, tw3, tw4 = st.columns(4)
                tw1.metric("Tailwind Days",    f"{tw['tailwind_day_count']} / {days}")
                tw2.metric("Tier 2 Ceiling Hit", f"{tw['tier2_day_count']} / {tw['tailwind_day_count']}",
                           help=f"Days where total P&L (realized+unrealized) hit ${DAILY_BONUS_TARGET:,} and all positions were locked")
                tw3.metric("Total Extra Captured", f"${tw['total_extra_captured']:,.2f}")
                tw4.metric("Avg Extra / Day",       f"${tw['avg_extra_per_day']:,.2f}")

                for d in tw.get("tailwind_days", []):
                    t2_badge = " 🏆" if d["tier2_hit"] else ""
                    with st.expander(
                        f"{d['date']}  —  Floor ${d['floor_pnl']:,.0f} → Final ${d['final_day_pnl']:,.0f}"
                        f"  (+${d['extra_captured']:,.0f} extra){t2_badge}"
                    ):
                        if d["riders"]:
                            rider_rows = []
                            for r in d["riders"]:
                                note = ""
                                if r["close_reason"] == "LOCK_IN":
                                    note = "Tier 2 ceiling close"
                                elif r["close_reason"] == "STOP" and r["pnl"] > 0:
                                    note = "Trail caught reversal — still profitable"
                                elif r["close_reason"] == "STOP" and r["pnl"] <= 0:
                                    note = "Stopped out after riding"
                                rider_rows.append({
                                    "Ticker": r["ticker"],
                                    "Exit":   r["close_reason"],
                                    "P&L":    fmt_pnl(r["pnl"]),
                                    "Note":   note,
                                })
                            st.dataframe(pd.DataFrame(rider_rows), use_container_width=True, hide_index=True)
                        else:
                            st.caption("No riders — all positions closed before Tier 1 trigger.")

                if tw["total_extra_captured"] > 0:
                    st.success(f"✅ Tailwind mode captured ${tw['total_extra_captured']:,.2f} extra vs. closing everything at ${DAILY_LOCK_IN_TARGET:,}.")
                else:
                    st.warning("⚠️ No net extra captured yet — riders may be closing at a loss after Tier 1.")

        st.markdown("---")

    perf = db.select("daily_performance", order="date", limit=60)
    if not perf:
        st.info("No performance data yet.")
        st.stop()

    df = pd.DataFrame(perf).sort_values("date")

    total_pnl    = df["total_pnl"].sum()
    avg_daily    = df["total_pnl"].mean()
    win_days     = (df["total_pnl"] > 0).sum()
    total_days   = len(df)
    avg_win_rate = df["win_rate"].mean()
    latest_cap   = df.iloc[-1]["ending_capital"]
    total_return = (latest_cap - TOTAL_CAPITAL) / TOTAL_CAPITAL * 100
    ann_return   = total_return / total_days * 250 if total_days > 0 else 0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total P&L",        fmt_pnl(total_pnl))
    c2.metric("Avg Daily P&L",    fmt_pnl(avg_daily))
    c3.metric("Win Days",         f"{win_days}/{total_days}")
    c4.metric("Avg Trade Win %",  f"{avg_win_rate:.1f}%")
    c5.metric("Portfolio Value",  f"${latest_cap:,.0f}")
    c6.metric("Ann. Return",      f"{ann_return:+.0f}%",
              help=f"Annualized: {total_return:.1f}% over {total_days} days × 250 trading days/year")

    st.markdown("---")

    # ── Daily P&L bar chart ────────────────────────────────────────
    st.subheader("Daily P&L")
    bar_colors = ["#27ae60" if v >= 0 else "#e74c3c" for v in df["total_pnl"]]
    fig_bar = go.Figure(go.Bar(
        x=df["date"],
        y=df["total_pnl"],
        marker_color=bar_colors,
        text=[fmt_pnl(v) for v in df["total_pnl"]],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>P&L: $%{y:,.2f}<extra></extra>",
    ))
    fig_bar.add_hline(y=DAILY_PROFIT_TARGET, line_dash="dot", line_color="#f39c12",
                      annotation_text=f"${DAILY_PROFIT_TARGET:,} target", annotation_position="top right")
    fig_bar.update_layout(
        xaxis_title="Date",
        yaxis_title="Daily P&L ($)",
        height=350,
        margin=dict(l=20, r=20, t=20, b=20),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(gridcolor="rgba(128,128,128,0.2)"),
    )
    st.plotly_chart(fig_bar, use_container_width=True)

    # ── Portfolio value + cumulative P&L ─────────────────────────
    st.subheader("Portfolio Value & Cumulative P&L")
    df["cumulative_pnl"] = df["total_pnl"].cumsum()

    fig_line = go.Figure()
    fig_line.add_trace(go.Scatter(
        x=df["date"], y=df["ending_capital"],
        name="Portfolio Value",
        line=dict(color="#1A3A6A", width=2),
        hovertemplate="<b>%{x}</b><br>Portfolio: $%{y:,.0f}<extra></extra>",
        fill="tozeroy", fillcolor="rgba(26,58,106,0.08)",
    ))
    fig_line.add_trace(go.Scatter(
        x=df["date"], y=df["cumulative_pnl"],
        name="Cumulative P&L",
        line=dict(color="#27ae60", width=2, dash="dot"),
        hovertemplate="<b>%{x}</b><br>Cum. P&L: $%{y:,.0f}<extra></extra>",
        yaxis="y2",
    ))
    fig_line.add_hline(y=TOTAL_CAPITAL, line_dash="dash", line_color="rgba(128,128,128,0.5)",
                       annotation_text="Starting capital", annotation_position="bottom right")
    fig_line.update_layout(
        height=350,
        margin=dict(l=20, r=60, t=20, b=20),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", y=1.08),
        yaxis=dict(title="Portfolio Value ($)", gridcolor="rgba(128,128,128,0.2)"),
        yaxis2=dict(title="Cumulative P&L ($)", overlaying="y", side="right",
                    gridcolor="rgba(0,0,0,0)"),
    )
    st.plotly_chart(fig_line, use_container_width=True)

    st.subheader("Daily Log")
    display = df[["date", "total_pnl", "total_trades", "win_count", "loss_count",
                  "win_rate", "ending_capital", "notes"]].sort_values("date", ascending=False)
    display["total_pnl"]       = display["total_pnl"].apply(fmt_pnl)
    display["ending_capital"]  = display["ending_capital"].apply(lambda x: f"${x:,.0f}")
    st.dataframe(display, use_container_width=True, hide_index=True)


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
