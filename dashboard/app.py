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
from config.settings import DASHBOARD_PASSWORD, TOTAL_CAPITAL, DAILY_PROFIT_TARGET, ETF_UNIVERSE, TRAIL_PCT, LOCK_IN_TRAIL_PCT, MIN_REWARD_RISK, DAILY_LOCK_IN_TARGET, DAILY_BONUS_TARGET, STRATEGY_MIN_SCORE, DAILY_LOSS_LIMIT, MIN_POSITION_PCT, MAX_POSITION_PCT
from eval import _compute_metrics
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


# ── Halt banner — shown on every page if agent is stopped ─────────
_halt_rows = db.select("scan_results", filters={"scan_type": "halt_flag"})
if _halt_rows:
    _hr = _halt_rows[0].get("results", {})
    _halted_at = _hr.get("halted_at", "")[:16].replace("T", " ")
    _closed    = _hr.get("positions_closed", [])
    _pos_note  = (f"{len(_closed)} position(s) closed: {', '.join(_closed)}"
                  if _closed else "Open positions left running — Alpaca native stops active")
    st.error(
        f"🛑 **TRADING HALTED** — {_hr.get('reason', 'Manual override')}  "
        f"·  Since {_halted_at} UTC  ·  {_pos_note}  ·  "
        f"*Trigger 'Restart Trading Agent' in GitHub Actions to resume.*",
        icon=None,
    )

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


def fmt_vwap_badge(ticker: str, vwap_sigs: dict) -> str:
    """Inline HTML badge: VWAP position + relative strength at entry time. Empty string if no data."""
    sig = vwap_sigs.get(ticker)
    if not sig or sig.get("above_vwap") is None:
        return ""
    above  = sig["above_vwap"]
    rs     = sig.get("rs_vs_spy")
    color  = "#1a5276" if above else "#7f8c8d"
    label  = "▲ VWAP" if above else "▼ VWAP"
    rs_str = f" · RS {rs:.1f}×" if rs is not None else ""
    return (
        f"<span style='background:{color};color:white;padding:2px 7px;"
        f"border-radius:4px;font-size:11px;margin-left:6px'>{label}{rs_str}</span>"
    )


def _vwap_legend():
    with st.expander("ℹ️ VWAP & RS signals explained"):
        st.markdown(
            "**▲ VWAP** — Entry price was *above* today's Volume-Weighted Average Price — "
            "the institutional benchmark. Above VWAP = sustained buying pressure since open, "
            "confirming momentum direction. This is the preferred setup.\n\n"
            "**▼ VWAP** — Price was below VWAP at entry. Indicates selling pressure; a weaker setup "
            "that Claude selected only when other signals were strong enough to override.\n\n"
            "**RS ×** (Relative Strength vs SPY since open) — how much the stock's move outpaced "
            "the market. RS 2.0× = stock moved twice as far as SPY. "
            "Target threshold: > 1.5× for strong momentum. < 0 = stock fell while market rose — avoid.\n\n"
            "*Captured at premarket entry time. Only available on Alpaca paper runs; absent in simulation mode.*"
        )


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

    scan_raw     = scan["results"] if scan else {}
    vwap_signals = scan_raw.get("vwap_signals", {})

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
        if vwap_signals:
            _vwap_legend()
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
            vwap_badge = fmt_vwap_badge(pos["ticker"], vwap_signals)
            c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 3, 2])
            c1.markdown(f"**{icon} {label}**{tail_badge}{vwap_badge}", unsafe_allow_html=True)
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

    vwap_signals_today = results.get("vwap_signals", {})

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

    pipeline      = results.get("pipeline_counts", {})
    post_blackout = pipeline.get("post_blackout", len(candidates) + len(blackout))
    final_count   = pipeline.get("final_count",   len(candidates))

    s1, s2, s3 = st.columns(3)
    s1.metric("Passed Scan",
              post_blackout + len(blackout),
              help="Stocks from the 430+ universe that passed minimum filters: price ≥$5, avg volume ≥500K, technical score ≥3/10.")
    s2.metric("Earnings Blocked", len(blackout),
              delta=f"-{len(blackout)}" if blackout else None, delta_color="inverse",
              help="Tickers removed because they report earnings today or tomorrow. Earnings = binary event with gap risk — not suitable for day trading.")
    s3.metric("Sent to Claude", final_count,
              help=f"Candidates after ALL filters (score pre-filter ≥{STRATEGY_MIN_SCORE}, ML ranking, live prices, VWAP enrichment). This is exactly what Claude sees.")

    if blackout:
        with st.expander(f"⛔ Earnings Blackout — {len(blackout)} ticker(s)"):
            for b in blackout:
                st.markdown(f"- **{b['ticker']}**: {b['reason']}")

    # ── Pipeline funnel ───────────────────────────────────────────
    if pipeline:
        with st.expander("🔬 Pipeline detail — how the candidate list was built before Claude saw it"):
            st.caption(
                "Four filters run between the raw scan and Claude's call. "
                "Each one narrows the list and enriches what remains."
            )
            pf1, pf2, pf3, pf4, pf5 = st.columns(5)
            pf1.metric(
                "After Earnings Filter", pipeline.get("post_blackout", "?"),
                help="Survived the earnings blackout check — no reports today or tomorrow."
            )
            dropped = pipeline.get("prefilter_dropped", 0)
            pf2.metric(
                f"After Score Filter ≥{STRATEGY_MIN_SCORE}", pipeline.get("post_prefilter", "?"),
                delta=f"-{dropped} dropped" if dropped else "none dropped",
                delta_color="off",
                help=(
                    f"Step 1.75: kept only candidates with technical_score ≥ {STRATEGY_MIN_SCORE}. "
                    "Reduces Claude's input tokens by ~60-70% without losing trade quality."
                )
            )
            ml_n = pipeline.get("ml_scored", 0)
            pf3.metric(
                "ML Scored", ml_n if ml_n else "—",
                help=(
                    "Step 1.76: ML model assigns each candidate a probability of hitting +2% intraday. "
                    "Candidates re-ranked highest → lowest before Claude sees them. "
                    "0 means model not trained yet — run train_model.py."
                )
            )
            vwap_n = pipeline.get("vwap_enriched", 0)
            pf4.metric(
                "VWAP Enriched", vwap_n if vwap_n else "—",
                help=(
                    "Step 1.85 (Alpaca only): VWAP position and RS vs SPY fetched for each candidate. "
                    "Candidates sorted above-VWAP first, then by RS descending. "
                    "Absent in simulation mode."
                )
            )
            above_n = pipeline.get("above_vwap", 0)
            pf5.metric(
                "▲ Above VWAP", above_n if vwap_n else "—",
                help=(
                    "Of the VWAP-enriched candidates, how many were trading above the institutional VWAP benchmark. "
                    "These appear first in Claude's candidate list — the preferred momentum setup."
                )
            )

            st.markdown("**Selection funnel:**")
            funnel_steps = [
                ("Universe",           f"{len(candidates) + len(blackout) + pipeline.get('prefilter_dropped', 0):,} stocks",  "Full watchlist scanned"),
                ("Passed scanner",     f"{post_blackout + len(blackout)}",      "Price ≥$5, volume ≥500K, technical score ≥3"),
                ("Earnings clear",     f"{pipeline.get('post_blackout', '?')}",  "No earnings today/tomorrow"),
                (f"Score ≥{STRATEGY_MIN_SCORE}",     f"{pipeline.get('post_prefilter', '?')}",  "Technical pre-filter — bullish setups only"),
                ("ML ranked",          f"{pipeline.get('post_prefilter', '?')}",  f"Re-ranked by P(hit +2%) — {ml_n} with score" if ml_n else "ML model not available"),
                ("VWAP enriched",      f"{vwap_n if vwap_n else '—'}",           f"{above_n} above VWAP, sorted first" if vwap_n else "Simulation mode — no VWAP"),
                ("Sent to Claude",     f"{final_count}",                          "This is the exact list Claude's strategy agent saw"),
            ]
            for step, count, note in funnel_steps:
                st.markdown(
                    f"<span style='color:#f47b20;font-weight:bold'>{step}</span> → "
                    f"<span style='font-weight:bold'>{count}</span>  "
                    f"<span style='color:#888;font-size:12px'>{note}</span>",
                    unsafe_allow_html=True
                )

    if candidates:
        with st.expander(f"📋 Screened Candidates — {len(candidates)} stocks (as Claude saw them)", expanded=True):
            df_scan = pd.DataFrame(candidates)
            show_cols = ["ticker", "technical_score", "ml_score", "rsi", "volume_ratio",
                         "above_vwap", "rs_vs_spy", "price", "atr_pct", "signals"]
            df_scan = df_scan[[c for c in show_cols if c in df_scan.columns]]
            df_scan = add_company_col(df_scan)
            st.dataframe(df_scan, use_container_width=True, height=320)
            st.caption(
                f"Sorted by order Claude saw them: above VWAP first, then RS vs SPY descending. "
                f"ml_score = P(hit +2% intraday). technical_score = scanner score ({STRATEGY_MIN_SCORE}–10 after pre-filter)."
            )

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
        if vwap_signals_today:
            _vwap_legend()
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
            _vwap_badge = fmt_vwap_badge(pos["ticker"], vwap_signals_today)
            c1.markdown(f"**{icon} {label}** `{pos['action']}`{_tail_badge}{_vwap_badge}", unsafe_allow_html=True)
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

    # Load VWAP signals from today's premarket scan (available in Alpaca mode)
    _pos_today = date.today().isoformat()
    _pos_scans = db.select("scan_results", filters={"date": _pos_today, "scan_type": "premarket"})
    _pos_vwap  = (_pos_scans[0]["results"] if _pos_scans else {}).get("vwap_signals", {})

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
        if _pos_vwap:
            _vwap_legend()
        st.markdown("---")
        for pos in open_pos:
            pnl  = pos.get("unrealized_pnl", 0)
            icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 2, 2])
            name = COMPANY_NAMES.get(pos["ticker"], "")
            label = f"{pos['ticker']} · {name}" if name else pos["ticker"]
            _pb   = fmt_vwap_badge(pos["ticker"], _pos_vwap)
            c1.markdown(f"**{icon} {label}** `{pos['action']}`{_pb}", unsafe_allow_html=True)
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

    # ── Date range selector — only show ranges with enough data ──
    _all_perf      = db.select("daily_performance", order="date")
    _total_days    = len(_all_perf)
    _all_range_opts = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90, "All time": None}
    _range_opts    = {k: v for k, v in _all_range_opts.items() if v is None or _total_days >= v}
    if not _range_opts:
        _range_opts = {"Last 7 days": 7}
    _selected   = st.radio("Date range", list(_range_opts.keys()), horizontal=True, index=0)
    _n_days     = _range_opts[_selected]

    # ── Today's summary ──────────────────────────────────────────
    _today_summary_rows = db.select("scan_results", filters={"scan_type": "daily_summary"}, order="date", limit=1)
    if _today_summary_rows:
        _ts = _today_summary_rows[0]
        _tr = _ts.get("results", {})
        _pnl_val = _tr.get("total_pnl", 0) or 0
        _pnl_color = "#1e8449" if _pnl_val >= 0 else "#e74c3c"
        st.markdown(
            f"**Today's Summary** — {_ts['date']}  "
            f"<span style='color:{_pnl_color}'>**${_pnl_val:+,.2f}**</span>",
            unsafe_allow_html=True,
        )
        st.info(_tr.get("summary", "Summary not available."))
        st.caption(f"Generated at {(_tr.get('generated_at') or '')[:16].replace('T', ' ')} UTC")

    st.markdown("---")

    # ── Load & filter perf data ───────────────────────────────────
    if not _all_perf:
        st.info("No performance data yet.")
        st.stop()

    df = pd.DataFrame(_all_perf).sort_values("date")
    if _n_days:
        _cutoff = (pd.Timestamp.today() - pd.Timedelta(days=_n_days)).strftime("%Y-%m-%d")
        df = df[df["date"] >= _cutoff]
    if df.empty:
        st.info(f"No trading data in the selected range ({_selected}).")
        st.stop()

    # ── Live scorecard metrics for selected window ────────────────
    ev   = _compute_metrics(perf_rows=df.to_dict("records")) or {}
    days = len(df)

    # ── Agent Scorecard ───────────────────────────────────────────
    if ev:
        grade_label = {"A": "Excellent", "B": "Good", "C": "Mediocre", "D": "Poor"}.get(ev.get("grade", ""), "")

        with st.expander(f"Agent Scorecard — {days} trading day{'s' if days != 1 else ''} of data ({_selected}) · Grade **{ev.get('grade','?')}** ({grade_label})", expanded=True):

            # ── VERDICT ──────────────────────────────────────────────
            st.markdown("#### Verdict")
            st.caption(
                f"Score = P&L vs target (up to 40 pts: avg daily P&L ÷ ${DAILY_PROFIT_TARGET:,} × 40)  "
                f"+ Win day rate (30 pts: profitable days ÷ total days × 30)  "
                f"+ Trade win rate (30 pts: % of trades won × 30).  "
                f"Grade: A ≥ 80 · B ≥ 60 · C ≥ 40 · D < 40."
            )
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

            grade_color = {"A": "#1e8449", "B": "#1e8449", "C": "#f39c12", "D": "#e74c3c"}.get(ev.get("grade", ""), "#888")
            grade_word  = {"A": "excellent", "B": "good", "C": "mixed", "D": "poor"}.get(ev.get("grade", ""), "")

            summary_parts = []
            if wins:
                summary_parts.append(" ".join(wins))
            verdict_text = f"<span style='color:{grade_color}'><b>Grade {ev.get('grade','?')} — {grade_word.upper()}.</b></span> " + (
                " ".join(summary_parts) if summary_parts else "No standout positives yet — more data needed."
            )

            watch_text = ""
            if watchs:
                watch_text = f"<span style='color:#f39c12'><b>Watch:</b> {('  ·  ').join(watchs)}.</span>"

            action_text = ""
            if actions:
                action_text = f"<span style='color:#e74c3c'><b>Action required:</b> {('  ·  ').join(actions)}.</span>"
            else:
                action_text = f"<span style='color:#1e8449'>No action required.</span>"

            st.markdown(
                verdict_text + ("  " + watch_text if watch_text else "") + "  " + action_text,
                unsafe_allow_html=True,
            )

            st.markdown("---")

            # ── Score metrics ─────────────────────────────────────────
            st.markdown("#### Score Breakdown")
            sc1, sc2, sc3, sc4, sc5, sc6 = st.columns(6)
            ps = ev.get("pnl_score", 0)
            ws = ev.get("winday_score", 0)
            rs = ev.get("winrate_score", 0)
            sc1.metric("Score", f"{ev.get('score', 0):.0f} / 100",
                       help=f"Composite 0–100 score across 3 dimensions:\n"
                            f"• P&L vs target: {ps:.0f}/40 pts (avg daily P&L ÷ ${DAILY_PROFIT_TARGET:,} target × 40)\n"
                            f"• Win day rate: {ws:.0f}/30 pts (profitable days ÷ total days × 30)\n"
                            f"• Trade win rate: {rs:.0f}/30 pts (% of trades that won × 30)\n"
                            f"Grade: A ≥80, B ≥60, C ≥40, D <40")
            sc2.metric("Avg Daily P&L", f"${ev.get('avg_daily_pnl', 0):,.0f}",
                       delta=f"target ${DAILY_PROFIT_TARGET:,}",
                       help=f"Average realized P&L per trading day over the {days}-day eval window. Target: ${DAILY_PROFIT_TARGET:,}/day.")
            sc3.metric("Win Days", f"{win_days} / {days}",
                       help="Days where total realized P&L was positive. Target: ≥80% of days profitable (consistent execution).")
            sc4.metric("Trade Win Rate", f"{ev.get('avg_win_rate', 0):.1f}%",
                       help="Average % of individual trades (not days) that closed in profit. "
                            "At 3:1 reward:risk you only need 25% to break even. Target: ≥60%.")
            sc5.metric("Actual R:R", f"{ev.get('actual_rr', 0):.2f}x",
                       help="Reward:Risk ratio = avg winning trade ÷ avg losing trade (absolute values). "
                            "3.0x means your wins are 3× bigger than your losses on average. "
                            "Target: ≥3.0x. Below 2.0x means stops may be too tight or targets too far.")
            sc6.metric("Ann. Return", f"{ev.get('ann_return', 0):+.0f}%",
                       help=f"Extrapolates the {days}-day return to a full 250-day trading year. "
                            f"Treat as directional — it stabilizes with more data. >50% = exceptional, >20% = strong.")

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
                st.markdown("**Integrity checks**  — *guardrail audit; these should all be ✅*")
                unfill_n = ev.get("unfilled_count", 0)
                unfill_flag = "✅" if unfill_pct < 10 else "⚠️"
                st.markdown(f"{unfill_flag} UNFILLED rate: **{unfill_n}** ({unfill_pct:.0f}%)  "
                            f"<span style='color:#888;font-size:0.82em'>— limit order submitted but entry never filled (stock moved away). >10% means entry price buffer is too tight.</span>",
                            unsafe_allow_html=True)
                st.markdown(f"{'✅' if not orphaned else '❌'} Orphaned open positions: **{len(orphaned)}**  "
                            f"<span style='color:#888;font-size:0.82em'>— positions opened on a prior day still showing OPEN. Should be zero; means EOD close missed them.</span>",
                            unsafe_allow_html=True)
                dups = ev.get("duplicate_count", 0)
                st.markdown(f"{'✅' if dups == 0 else '❌'} Duplicate tickers same day: **{dups}**  "
                            f"<span style='color:#888;font-size:0.82em'>— same ticker entered twice in one day. The open-position guardrail should block this.</span>",
                            unsafe_allow_html=True)
                missing = ev.get("missing_exit", 0)
                st.markdown(f"{'✅' if missing == 0 else '⚠️'} Missing exit mechanism: **{missing}**  "
                            f"<span style='color:#888;font-size:0.82em'>— closed positions where we couldn't determine if exit was TARGET, STOP, or TRAIL. Means a code path gap.</span>",
                            unsafe_allow_html=True)
                st.markdown(f"📉 Loss-limit days: **{ev.get('loss_limit_days', 0)}** / {days}  "
                            f"<span style='color:#888;font-size:0.82em'>— days where realized P&L went below the daily loss floor (${DAILY_LOSS_LIMIT:,}). Agent stops trading for the day.</span>",
                            unsafe_allow_html=True)
                st.markdown(f"🎯 Lock-in days: **{ev.get('lock_in_days', 0)}** / {days}  "
                            f"<span style='color:#888;font-size:0.82em'>— days where realized P&L crossed the ${DAILY_LOCK_IN_TARGET:,} Tier 1 floor. Remaining positions ride with a tighter 0.5% trail.</span>",
                            unsafe_allow_html=True)
                halted_ev = ev.get("halted_days", [])
                active_ev = [h for h in halted_ev if h.get("active")]
                halt_icon = "🛑" if active_ev else "✅"
                st.markdown(f"{halt_icon} Halted days in window: **{len(halted_ev)}** {'(halt still active!)' if active_ev else '(all cleared)' if halted_ev else ''}")
                for h in halted_ev:
                    st.caption(f"  → {h['date']}  {h.get('reason', '')}  {'🛑 ACTIVE' if h.get('active') else '✅ cleared'}")

            with qual_col:
                st.markdown("**Claude quality checks**  — *validates Claude is following the strategy rules*")
                rr_v = ev.get("rr_violations", [])
                st.markdown(f"{'✅' if not rr_v else '❌'} R:R violations: **{len(rr_v)}** trades below {MIN_REWARD_RISK}x  "
                            f"<span style='color:#888;font-size:0.82em'>— Reward:Risk ratio = (target − entry) ÷ (entry − stop). "
                            f"Claude is required to only submit trades where the potential gain is at least {MIN_REWARD_RISK}× the potential loss. "
                            f"Violations mean Claude submitted a trade that doesn't meet the minimum return profile.</span>",
                            unsafe_allow_html=True)
                if rr_v:
                    for v in rr_v:
                        st.caption(f"  → {v['ticker']} R:R {v['rr']:.2f}x")
                sz_v = ev.get("size_violations", [])
                st.markdown(f"{'✅' if not sz_v else '⚠️'} Position size violations: **{len(sz_v)}**  "
                            f"<span style='color:#888;font-size:0.82em'>— Each position must be sized between ${MIN_POSITION_PCT*TOTAL_CAPITAL:,.0f} and ${MAX_POSITION_PCT*TOTAL_CAPITAL:,.0f} "
                            f"({MIN_POSITION_PCT*100:.0f}%–{MAX_POSITION_PCT*100:.0f}% of ${TOTAL_CAPITAL:,} capital). "
                            f"Claude chooses size by confidence (HIGH/MEDIUM/LOW); violations mean it went outside the allowed band.</span>",
                            unsafe_allow_html=True)

                st.markdown("**Confidence cohort**  — *does Claude's confidence signal predict better outcomes?*  "
                            "<span style='color:#888;font-size:0.82em'>HIGH conviction trades get $7K, MEDIUM $6K, LOW $5K. "
                            "If HIGH confidence doesn't outperform LOW, the signal is unreliable.</span>",
                            unsafe_allow_html=True)
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

            # ── VWAP Signal Quality ────────────────────────────────────
            st.markdown("#### VWAP & Relative Strength Signal Quality")
            st.caption(
                "Do stocks that were already trading above VWAP — and outpacing the market — at the time of entry "
                "actually produce better trade outcomes? This table compares closed-trade P&L across four cohorts to answer that question."
            )
            with st.expander("ℹ️ What do VWAP and RS mean here?"):
                st.markdown(
                    "**VWAP (Volume-Weighted Average Price)** — The average price of a stock weighted by volume since market open. "
                    "Institutions use it as a benchmark: buying above VWAP means sustained demand; below VWAP means the stock is struggling to hold up. "
                    "A stock above VWAP at 9:45 AM is showing early momentum strength.\n\n"
                    "**RS (Relative Strength vs SPY)** — How much the stock's move today outpaced the S&P 500. "
                    "RS 2.0× means the stock moved twice as far as SPY since open. "
                    "RS ≥ 1.5× = market leader (the stock is outperforming the broad market). "
                    "RS < 1.0× = laggard (SPY is actually doing better). "
                    "High RS at entry is an institutional signal that money is flowing into this name specifically.\n\n"
                    "**Why it matters:** A stock above VWAP with RS ≥ 1.5× is the ideal momentum setup — "
                    "confirmed buying pressure *and* outperforming peers. This table validates whether that entry signal actually translates to better P&L."
                )
            vwap_ev = ev.get("vwap_analysis")

            if not vwap_ev:
                st.info("No VWAP data yet — accumulates from Alpaca runs from 2026-05-18 onward. Will appear once enough enriched trades have closed.")
            else:
                vq1, vq2 = st.columns(2)
                vq1.metric("Positions Matched", f"{vwap_ev['matched']} / {vwap_ev['total']}",
                           help="Positions cross-referenced to a VWAP/RS entry signal from the premarket scan. Unmatched = simulation runs or pre-enrichment history.")

                above = vwap_ev.get("above_vwap")
                below = vwap_ev.get("below_vwap")
                if above and below:
                    delta = above["avg_pnl"] - below["avg_pnl"]
                    vq2.metric("Above vs Below VWAP edge", f"{delta:+.2f} avg P&L",
                               help="Avg P&L for above-VWAP entries minus avg P&L for below-VWAP entries. "
                                    "Positive = the VWAP filter is adding value. Target: > $10 edge to confirm signal quality.")

                cohort_rows = []
                for key, label in [("above_vwap", "▲ Above VWAP at entry"), ("below_vwap", "▼ Below VWAP at entry"),
                                    ("high_rs",    "RS ≥ 1.5× (market leader)"), ("low_rs", "RS < 1.5× (laggard or in-line)")]:
                    c = vwap_ev.get(key)
                    if c:
                        cohort_rows.append({
                            "Cohort":    label,
                            "Trades":    c["count"],
                            "Win %":     f"{c['win_rate']:.1f}%",
                            "Avg P&L":   f"${c['avg_pnl']:,.2f}",
                            "Total P&L": f"${c['total_pnl']:,.0f}",
                        })
                if cohort_rows:
                    st.dataframe(pd.DataFrame(cohort_rows), use_container_width=True, hide_index=True)

                if above and below:
                    if delta > 10:
                        st.success(f"✅ VWAP filter confirmed — above-VWAP entries average +${delta:.0f} more per trade vs below-VWAP.")
                    elif delta >= 0:
                        st.info(f"⚠️ Slight VWAP edge (+${delta:.0f} avg) — signal is directionally correct but more data needed to confirm.")
                    else:
                        st.warning(f"❌ No VWAP edge yet (${delta:+.0f} avg) — below-VWAP entries are matching or beating above-VWAP.")

                high_rs = vwap_ev.get("high_rs")
                low_rs  = vwap_ev.get("low_rs")
                if high_rs and low_rs:
                    rs_delta = high_rs["avg_pnl"] - low_rs["avg_pnl"]
                    if rs_delta > 10:
                        st.success(f"✅ RS filter confirmed — high-RS entries (≥1.5×) average +${rs_delta:.0f} more per trade vs low-RS.")
                    elif rs_delta >= 0:
                        st.info(f"⚠️ Slight RS edge (+${rs_delta:.0f} avg) — directionally positive but more data needed.")
                    else:
                        st.warning(f"❌ No RS edge detected (${rs_delta:+.0f} avg) — low-RS entries are matching high-RS.")

        st.markdown("---")

    total_pnl    = df["total_pnl"].sum()
    avg_daily    = df["total_pnl"].mean()
    win_days     = (df["total_pnl"] > 0).sum()
    total_days   = len(df)
    avg_win_rate = df["win_rate"].mean()
    latest_cap   = df.iloc[-1]["ending_capital"]
    total_return = (latest_cap - TOTAL_CAPITAL) / TOTAL_CAPITAL * 100
    ann_return   = total_return / total_days * 250 if total_days > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total P&L", fmt_pnl(total_pnl),
              help=f"Sum of realized P&L across all {total_days} trading day(s) shown. Does not include any open unrealized positions.")
    c2.metric("Avg Daily P&L", fmt_pnl(avg_daily),
              help=f"Total P&L ÷ {total_days} days. Target is ${DAILY_PROFIT_TARGET:,}/day.")
    c3.metric("Win Days", f"{win_days}/{total_days}",
              help="Days where realized P&L was positive. Target: ≥80% of trading days profitable.")
    c4.metric("Avg Trade Win %", f"{avg_win_rate:.1f}%",
              help="Average % of individual trades (not days) that closed in profit. At 3:1 reward:risk, you only need 25% to break even.")

    c5, c6, c7 = st.columns(3)
    c5.metric("Portfolio Value", f"${latest_cap:,.0f}",
              help=f"Current account value = starting capital ${TOTAL_CAPITAL:,} + all realized gains to date. Updates each EOD run.")
    c6.metric("Total Return", f"{total_return:+.2f}%",
              help=f"(Portfolio Value − Starting Capital) ÷ Starting Capital. ${latest_cap - TOTAL_CAPITAL:+,.2f} gain on ${TOTAL_CAPITAL:,} since Day 1.")
    c7.metric("Ann. Return", f"{ann_return:+.0f}%",
              help=f"Extrapolates your {total_return:.2f}% return over {total_days} day(s) to a full 250-day trading year. Early days — this number stabilizes with more history.")

    st.markdown("---")

    # ── Load halt dates for chart markers ─────────────────────────
    _chart_halt_dates = set()
    for _ftype in ("halt_flag", "halt_flag_cleared"):
        for _row in db.select("scan_results", filters={"scan_type": _ftype}):
            _d = (_row.get("results", {}).get("halted_at") or "")[:10]
            if _d:
                _chart_halt_dates.add(_d)
    _chart_date_strs = set(df["date"].astype(str))

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
    for _hd in sorted(_chart_halt_dates):
        if _hd in _chart_date_strs:
            fig_bar.add_vline(x=_hd, line_dash="dash", line_color="rgba(231,76,60,0.7)",
                              annotation_text="🛑 Halted", annotation_position="top left",
                              annotation_font_color="rgba(231,76,60,0.9)")
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

    # ── Portfolio value + cumulative P&L — side by side ──────────
    df["cumulative_pnl"] = df["total_pnl"].cumsum()
    ch_left, ch_right = st.columns(2)

    with ch_left:
        st.subheader("Portfolio Value")
        fig_pv = go.Figure()
        _pv_vals = df["ending_capital"].tolist()
        # y-axis zoomed to data: pad 2% above/below the range, always include starting capital
        _pv_min = min(_pv_vals + [TOTAL_CAPITAL])
        _pv_max = max(_pv_vals + [TOTAL_CAPITAL])
        _pv_pad = max((_pv_max - _pv_min) * 0.5, TOTAL_CAPITAL * 0.005)
        fig_pv.add_trace(go.Scatter(
            x=df["date"], y=_pv_vals,
            mode="lines+markers",
            name="Portfolio Value",
            line=dict(color="#1A3A6A", width=2.5),
            marker=dict(size=7, color="#1A3A6A"),
            hovertemplate="<b>%{x}</b><br>Portfolio: $%{y:,.2f}<extra></extra>",
            fill="tonexty", fillcolor="rgba(26,58,106,0.07)",
        ))
        # invisible baseline trace so fill shades between principal and portfolio line
        fig_pv.add_trace(go.Scatter(
            x=df["date"], y=[TOTAL_CAPITAL] * len(df),
            mode="lines",
            line=dict(color="rgba(128,128,128,0.4)", width=1.5, dash="dash"),
            name=f"Starting ${TOTAL_CAPITAL:,}",
            hoverinfo="skip",
        ))
        for _hd in sorted(_chart_halt_dates):
            if _hd in _chart_date_strs:
                fig_pv.add_vline(x=_hd, line_dash="dash", line_color="rgba(231,76,60,0.7)",
                                 annotation_text="🛑", annotation_position="top left",
                                 annotation_font_color="rgba(231,76,60,0.9)")
        fig_pv.update_layout(
            yaxis_title="Portfolio Value ($)",
            height=320,
            margin=dict(l=20, r=20, t=20, b=20),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(
                gridcolor="rgba(128,128,128,0.2)",
                range=[_pv_min - _pv_pad, _pv_max + _pv_pad],
                tickformat="$,.0f",
            ),
            legend=dict(orientation="h", y=-0.2, x=0),
        )
        st.plotly_chart(fig_pv, use_container_width=True)

    with ch_right:
        st.subheader("Cumulative P&L")
        _cum_vals = df["cumulative_pnl"].tolist()
        _cum_min = min(_cum_vals + [0])
        _cum_max = max(_cum_vals + [0])
        _cum_pad = max((_cum_max - _cum_min) * 0.5, 200)
        fig_cum = go.Figure()
        # shaded area above/below zero
        fig_cum.add_trace(go.Scatter(
            x=df["date"], y=_cum_vals,
            mode="lines+markers",
            name="Cumulative P&L",
            line=dict(color="#27ae60", width=2.5),
            marker=dict(
                size=7,
                color=["#27ae60" if v >= 0 else "#e74c3c" for v in _cum_vals],
            ),
            hovertemplate="<b>%{x}</b><br>Cum. P&L: $%{y:+,.2f}<extra></extra>",
            fill="tozeroy",
            fillcolor="rgba(39,174,96,0.10)",
        ))
        fig_cum.add_hline(y=0, line_dash="dash", line_color="rgba(128,128,128,0.5)",
                          annotation_text="Break even", annotation_position="bottom right")
        for _hd in sorted(_chart_halt_dates):
            if _hd in _chart_date_strs:
                fig_cum.add_vline(x=_hd, line_dash="dash", line_color="rgba(231,76,60,0.7)",
                                  annotation_text="🛑", annotation_position="top left",
                                  annotation_font_color="rgba(231,76,60,0.9)")
        fig_cum.update_layout(
            yaxis_title="Cumulative P&L ($)",
            height=320,
            margin=dict(l=20, r=20, t=20, b=20),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(
                gridcolor="rgba(128,128,128,0.2)",
                range=[_cum_min - _cum_pad, _cum_max + _cum_pad],
                tickformat="$+,.0f",
            ),
            showlegend=False,
        )
        st.plotly_chart(fig_cum, use_container_width=True)

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
