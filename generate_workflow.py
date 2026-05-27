"""Generates Trading Agent A full-day agent workflow diagram as PNG."""
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

# ── Canvas ─────────────────────────────────────────────────────────────────────
W, H = 30, 56
DPI  = 120
fig, ax = plt.subplots(figsize=(W, H))
ax.set_xlim(0, W)
ax.set_ylim(0, H)
ax.axis("off")
fig.patch.set_facecolor("#F4F6F8")
ax.set_facecolor("#F4F6F8")

# ── Colors ─────────────────────────────────────────────────────────────────────
PRE_L = "#2E5FA3"
INT_L = "#1E7A3E"
EOD_L = "#C96A1A"
DB_L  = "#4A5568"
SKIP  = "#C0392B"
ALP   = "#0D2D5A"
WHITE = "#FFFFFF"
FONT  = "DejaVu Sans"

BH = 1.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def rbox(cx, cy, w, h, title, sub="", fill=PRE_L, title_sz=10, sub_sz=8.5):
    pad = 0.12
    for kw in [dict(linewidth=0, facecolor=fill),
               dict(linewidth=1.1, edgecolor="white", facecolor="none", alpha=0.22)]:
        ax.add_patch(FancyBboxPatch((cx-w/2, cy-h/2), w, h,
                                    boxstyle=f"round,pad={pad},rounding_size=0.22",
                                    zorder=4, **kw))
    if sub:
        ax.text(cx, cy+0.19, title, fontsize=title_sz, fontweight="bold",
                color=WHITE, fontfamily=FONT, va="center", ha="center", zorder=6)
        ax.text(cx, cy-0.24, sub, fontsize=sub_sz, color=WHITE,
                fontfamily=FONT, va="center", ha="center", zorder=6, alpha=0.87)
    else:
        ax.text(cx, cy, title, fontsize=title_sz, fontweight="bold",
                color=WHITE, fontfamily=FONT, va="center", ha="center", zorder=6)


def diamond(cx, cy, w, h, label, fill=SKIP, fsz=8.2):
    pts = [(cx, cy+h/2), (cx+w/2, cy), (cx, cy-h/2), (cx-w/2, cy)]
    ax.add_patch(plt.Polygon(pts, closed=True, facecolor=fill,
                             edgecolor="white", linewidth=1, zorder=4, alpha=0.92))
    ax.text(cx, cy, label, fontsize=fsz, fontweight="bold", color=WHITE,
            fontfamily=FONT, va="center", ha="center", zorder=6)


def section_bg(y_bot, y_top, fill):
    ax.add_patch(FancyBboxPatch((0.5, y_bot), W-1.0, y_top-y_bot,
                                boxstyle="round,pad=0,rounding_size=0.4",
                                linewidth=1.4, edgecolor=fill,
                                facecolor=fill, alpha=0.06, zorder=1))


def section_hdr(y, label, fill, right_note=""):
    ax.add_patch(FancyBboxPatch((0.8, y-0.38), 14.0, 0.76,
                                boxstyle="round,pad=0,rounding_size=0.38",
                                linewidth=0, facecolor=fill, alpha=0.15, zorder=3))
    ax.text(1.4, y, label, fontsize=12.5, fontweight="bold", color=fill,
            fontfamily=FONT, va="center", ha="left", zorder=4)
    if right_note:
        ax.text(W-0.8, y, right_note, fontsize=8.2, color="#888888",
                fontfamily=FONT, va="center", ha="right", zorder=4, fontstyle="italic")


def arr(x1, y1, x2, y2, color="#888888", lw=1.7, label="", lside="right"):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=lw, mutation_scale=15), zorder=3)
    if label:
        mx, my = (x1+x2)/2, (y1+y2)/2
        dx = 0.18 if lside == "right" else -0.18
        ax.text(mx+dx, my, label, fontsize=7.5, color=color,
                fontstyle="italic", fontfamily=FONT, va="center",
                ha="left" if lside == "right" else "right", zorder=4)


def v_arr(cx, y1, y2, color="#888888", lw=1.7, label="", lside="right"):
    arr(cx, y1, cx, y2, color, lw, label, lside)


def h_arr(x1, x2, cy, color="#888888", lw=1.7, label=""):
    arr(x1, cy, x2, cy, color, lw, label)


def elbow(x1, y1, x2, y2, color="#888888", lw=1.7):
    ax.plot([x1, x1, x2], [y1, y2, y2], color=color, lw=lw, zorder=3,
            solid_capstyle="round")
    ax.annotate("", xy=(x2, y2), xytext=(x2-0.001*(1 if x2>x1 else -1), y2),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=lw, mutation_scale=15), zorder=3)


def note(x, y, text, color="#666666", ha="left", fsz=7.8):
    ax.text(x, y, text, fontsize=fsz, color=color, fontfamily=FONT,
            va="center", ha=ha, fontstyle="italic", zorder=5, linespacing=1.4)


CX = W / 2   # 15.0


# ══════════════════════════════════════════════════════════════════════════════
# TITLE
# ══════════════════════════════════════════════════════════════════════════════
ax.text(CX, 55.15, "Trading Agent A — Full Day Agent Workflow",
        fontsize=21, fontweight="bold", color="#1A3A6A",
        fontfamily=FONT, va="center", ha="center", zorder=6)
ax.text(CX, 54.35, "Broad Universe Strategy  ·  All agents, gates, conditions, and data flow",
        fontsize=11, color="#666666", fontfamily=FONT, va="center", ha="center", zorder=6)
ax.plot([1.2, W-1.2], [53.9, 53.9], color="#CCCCCC", lw=1.1, zorder=3)


# ══════════════════════════════════════════════════════════════════════════════
# ① PREMARKET   y: 35.5 → 53.5
# ══════════════════════════════════════════════════════════════════════════════
section_bg(35.5, 53.5, PRE_L)
section_hdr(53.0, "①  PREMARKET  —  10:00 AM ET  (delayed from 9:00 AM — spreads stabilize)", PRE_L,
            right_note="orchestrator.premarket()")

# ── System gates (3 diamonds) ─────────────────────────────────────────────────
GY = 51.6
diamond(CX-6.0, GY, 3.6, 1.05, "Trading\nDay?", SKIP)
diamond(CX,     GY, 3.6, 1.05, "Halted\ntoday?", SKIP)
diamond(CX+6.0, GY, 3.6, 1.05, "Dupe\nrun?", SKIP)

note(CX-9.0, GY, "NO → skip\n(weekend/holiday)", SKIP, fsz=7.8)
note(CX+1.9, GY, "YES → skip", SKIP, fsz=7.8)
note(CX+7.9, GY, "YES → skip", SKIP, fsz=7.8)

h_arr(CX-4.2, CX-1.8, GY, PRE_L, lw=1.6, label="  pass")
h_arr(CX+1.8, CX+4.2, GY, PRE_L, lw=1.6, label="  pass")
v_arr(CX+6.0, GY-0.52, GY-1.1, PRE_L, lw=1.6)

# ── Market Context ────────────────────────────────────────────────────────────
rbox(CX, 50.0, 10.0, 1.05,
     "Market Context",
     "VIX · Fear & Greed · US Futures · Intl Markets · Economic Calendar · 11 Sector ETFs",
     PRE_L, title_sz=11, sub_sz=8.5)

diamond(CX, 48.6, 4.0, 1.05, "Futures\n< -1.5%?", SKIP)
v_arr(CX, 49.47, 49.12, PRE_L, lw=1.8)
note(CX+2.2, 48.6, "YES → SKIP\nno trades today", SKIP, fsz=7.8)
note(CX+4.9, 50.1, "Sets:\n• max_positions\n• quiet_day flag", PRE_L, fsz=7.8)

v_arr(CX, 48.07, 47.4, PRE_L, lw=1.8)
note(CX+2.1, 47.8, "NO ↓  capital gate: avail_capital caps max_positions", PRE_L, fsz=7.5)

# ── Scanner ───────────────────────────────────────────────────────────────────
rbox(CX, 46.9, 12.0, 1.05,
     "Scanner  —  600+ tickers",
     "RSI · MACD · Bollinger Bands · Vol Ratio · SMA20/50 trend · Breakout freshness · ORB · VWAP (5-min bars)",
     PRE_L, title_sz=11, sub_sz=8.5)
note(CX+7.3, 47.1,
     "Drop if:\n• bid-ask spread > 0.5%\n• pre-mkt gap > 8%\n• avg range > 5%\n• ATR% > 5%\nPass: |score| ≥ 4",
     PRE_L, fsz=7.8)
v_arr(CX, 46.37, 45.75, PRE_L, lw=1.8)

# ── News Intel + Pre-filter ───────────────────────────────────────────────────
rbox(CX-3.6, 45.25, 5.8, 0.95,
     "News Intel",
     "Earnings blackout (today + tomorrow) · news context",
     PRE_L, title_sz=10, sub_sz=7.8)
rbox(CX+3.6, 45.25, 5.8, 0.95,
     "Strategy Pre-filter",
     "score ≥ PREMARKET_MIN_SCORE (5) · drops ~60% tokens",
     PRE_L, title_sz=10, sub_sz=7.8)
elbow(CX, 45.75, CX-3.6, 45.72, PRE_L, lw=1.8)
elbow(CX, 45.75, CX+3.6, 45.72, PRE_L, lw=1.8)
elbow(CX-3.6, 44.77, CX, 44.3, PRE_L, lw=1.5)
elbow(CX+3.6, 44.77, CX, 44.3, PRE_L, lw=1.5)

# ── ML Scorer ────────────────────────────────────────────────────────────────
rbox(CX, 43.8, 8.5, 0.95,
     "ML Scorer  (if model available)",
     "Predicts P(stock hits +2% intraday) · re-ranks by ml_score",
     PRE_L, title_sz=10, sub_sz=7.8)
note(CX+5.5, 43.8, "if no model:\ngracefully skipped", PRE_L, fsz=7.5)
v_arr(CX, 43.32, 42.68, PRE_L, lw=1.8)

# ── Live price + signals (Alpaca only) ────────────────────────────────────────
rbox(CX, 42.18, 10.0, 0.95,
     "Live Price Refresh + Intraday Signals  (Alpaca mode only)",
     "Alpaca ask quotes · above_vwap · vwap · rs_vs_spy · today_pct_change · sector ETF signals",
     PRE_L, title_sz=10, sub_sz=7.8)
note(CX-7.2, 42.18, "simulation:\nskipped", PRE_L, fsz=7.5)
v_arr(CX, 41.7, 41.05, PRE_L, lw=1.8)

# ── Claude Strategy Agent ─────────────────────────────────────────────────────
rbox(CX, 40.5, 11.0, 1.05,
     "Claude Strategy Agent  (claude-sonnet-4-6)",
     "Selects HIGH / MEDIUM / LOW confidence trades · sector conviction injected · quiet_day criteria",
     PRE_L, title_sz=11, sub_sz=8.5)
note(CX+6.8, 40.7,
     "Inputs per candidate:\ntechnical_score · action\nabove_vwap · rs_vs_spy\nml_score · sector_perf",
     PRE_L, fsz=7.8)
note(CX+6.8, 39.9,
     "Output:\nconfidence (H/M/L)\nentry · target · stop\nshares · reasoning",
     PRE_L, fsz=7.8)
v_arr(CX, 39.97, 39.35, PRE_L, lw=1.8)

# ── Risk | Sector Guard | ATR Sizer | Guardrails (4-up) ───────────────────────
W4 = 6.1
AGENT4 = [CX-6.9, CX-2.3, CX+2.3, CX+6.9]
AGENT4_TITLE = ["Risk Agent", "Sector Guard", "ATR Sizer", "Guardrails"]
AGENT4_SUB   = [
    "R:R ≥ 2.0 · size $2.5K-$3.5K\nloss limit · stop width OK",
    "MAX_PER_SECTOR cap\nBlocks concentration",
    "stop = ATR×1.2 · shares=$150/risk\nDrops trade if R:R < 1",
    "Duplicates · Price sanity\nLoss limit · Capital cap",
]
for cx4, t, s in zip(AGENT4, AGENT4_TITLE, AGENT4_SUB):
    rbox(cx4, 38.8, W4, 0.95, t, s, PRE_L, title_sz=9.5, sub_sz=7.5)
    elbow(CX, 39.35, cx4, 39.32, PRE_L, lw=1.5)
    elbow(cx4, 38.32, CX, 37.82, PRE_L, lw=1.4)

# ── Portfolio → Alpaca ────────────────────────────────────────────────────────
rbox(CX, 37.27, 11.0, 1.05,
     "Portfolio Agent  →  Alpaca Bracket Orders",
     "Leg A (+1% partial, 50% shares)  ·  Leg B (+4% ceiling, 50% shares)  ·  ATR stop both legs  ·  tagged strata_{ticker}_{ts}",
     ALP, title_sz=11, sub_sz=8.5)
note(CX-8.8, 37.5, "Leg A:\nentry → +1% target\nATR stop\n50% shares", "#BBBBBB", fsz=7.8)
note(CX-8.8, 36.8, "Leg B:\nentry → +4% ceiling\nATR stop\n50% shares", "#BBBBBB", fsz=7.8)
elbow(CX+5.5, 37.27, CX+8.5, 37.27, DB_L, lw=1.4)
note(CX+8.6, 37.5, "positions\nscan_results\ndaily_runs", DB_L, fsz=7.8)

v_arr(CX, 36.74, 35.88, "#666666", lw=2.0, label="  market open — positions active")


# ══════════════════════════════════════════════════════════════════════════════
# ② INTRADAY   y: 22.5 → 35.5
# ══════════════════════════════════════════════════════════════════════════════
section_bg(22.5, 35.5, INT_L)
section_hdr(35.0, "②  INTRADAY  —  every 15 min  (10:00 AM – 3:45 PM ET)", INT_L,
            right_note="agents/intraday._maybe_run_intraday_scan()")

# ── Reconcile (3 passes) + Refresh ────────────────────────────────────────────
rbox(CX-4.0, 33.85, 7.8, 1.1,
     "Reconcile with Alpaca  (3 passes)",
     "① fill_price backfill — NULL positions matched to filled buys\n"
     "② stale pending orders >5 min → cancel + mark UNFILLED\n"
     "③ classify OPEN positions: TARGET / STOP / NATIVE_TRAIL / UNFILLED",
     INT_L, title_sz=10, sub_sz=7.5)
rbox(CX+5.4, 33.85, 7.0, 1.1,
     "Refresh Positions",
     "Sync price + unrealized P&L · hard-stop safety net\nPhantom STOP prevention · high/low watermark",
     INT_L, title_sz=10, sub_sz=7.5)
h_arr(CX-0.1, CX+1.9, 33.85, INT_L, lw=1.6)
note(CX+9.3, 34.1,
     "Lock-in tiers:\n• $716 realized → let ride\n• $1,000 total → close all\n\nBreakeven lock:\nLeg A +1% → Leg B\nstop updated to entry\n(DB only; enforced\nnext refresh cycle)",
     INT_L, fsz=7.8)
v_arr(CX, 33.29, 32.55, INT_L, lw=1.8)

# ── 6 Intraday Guards ─────────────────────────────────────────────────────────
note(CX, 33.05, "All 6 guards evaluated in order — any failure skips the scan this cycle", INT_L, ha="center", fsz=7.5)
GXS = [CX-10.5, CX-6.3, CX-2.1, CX+2.1, CX+6.3, CX+10.5]
GLBLS = ["UTC hour\nin window?", "runs <\nMAX_RUNS\n(6)?", "interval >\nMIN_INTV\n(30 min)?",
          "slots <\nMAX_POS\n(15)?", "entries <\nMAX_DAILY\n(12)?", "P&L above\nboth limits?"]
for i, (gx, gl) in enumerate(zip(GXS, GLBLS)):
    diamond(gx, 32.0, 3.7, 1.0, gl, SKIP, fsz=7.5)
    if i < 5:
        h_arr(GXS[i]+1.85, GXS[i+1]-1.85, 32.0, INT_L, lw=1.3, label="  pass")
elbow(CX, 32.55, GXS[0], 32.52, INT_L, lw=1.8)
note(CX-13.0, 32.0, "FAIL →\nskip this\ncycle", SKIP, fsz=7.8)
v_arr(GXS[-1], 31.5, 30.9, INT_L, lw=1.6, label="  all pass", lside="right")
elbow(GXS[-1], 30.9, CX, 30.9, INT_L, lw=1.8)
note(CX, 31.25, "DAILY_LOSS_LIMIT  ·  DAILY_BONUS_TARGET", INT_L, ha="center", fsz=7.3)

# ── Scanner ────────────────────────────────────────────────────────────────────
v_arr(CX, 30.9, 30.2, INT_L, lw=1.8)
rbox(CX, 29.7, 11.5, 0.95,
     "Intraday Scanner + Momentum Scanner",
     "Technical (score ≥ STRATEGY_MIN_SCORE=4) + Intraday momentum movers · Already-traded tickers excluded · Merged by ticker",
     INT_L, title_sz=10, sub_sz=7.8)
note(CX+7.2, 29.7,
     "Momentum signals:\n• up ≥ 0.5% today\n• above VWAP\n• rs_vs_spy ≥ 0\nMerged into\ntechnical pool",
     INT_L, fsz=7.8)
v_arr(CX, 29.22, 28.58, INT_L, lw=1.8)

# ── Claude Strategy Agent ─────────────────────────────────────────────────────
rbox(CX, 28.05, 11.0, 1.05,
     "Claude Strategy Agent  (claude-sonnet-4-6)",
     "Sector conviction injected (hot/weak ETFs) · quiet_day criteria if Fear&Greed<35 · target capped at +1% intraday",
     INT_L, title_sz=11, sub_sz=8.5)
v_arr(CX, 27.52, 26.9, INT_L, lw=1.8)

# ── Risk | Sector Guard (2-up; ATR sizer not run for intraday) ────────────────
rbox(CX-3.5, 26.38, 6.5, 0.95,
     "Risk Agent",
     "R:R ≥ 2.0 · +1% intraday target · size bounds · loss limit",
     INT_L, title_sz=10, sub_sz=7.8)
rbox(CX+3.5, 26.38, 6.5, 0.95,
     "Sector Guard",
     "MAX_PER_SECTOR cap · ATR sizer not run for intraday entries",
     INT_L, title_sz=10, sub_sz=7.8)
elbow(CX, 26.9, CX-3.5, 26.87, INT_L, lw=1.5)
elbow(CX, 26.9, CX+3.5, 26.87, INT_L, lw=1.5)
elbow(CX-3.5, 25.9, CX, 25.42, INT_L, lw=1.4)
elbow(CX+3.5, 25.9, CX, 25.42, INT_L, lw=1.4)

# ── Portfolio → Alpaca ────────────────────────────────────────────────────────
rbox(CX, 24.9, 10.0, 1.05,
     "Portfolio Agent  →  Alpaca Bracket Orders",
     "Leg A: +1% partial · Leg B: +1% intraday cap · confidence stored on position row",
     ALP, title_sz=11, sub_sz=8.5)
elbow(CX+5.0, 24.9, CX+8.5, 24.9, DB_L, lw=1.4)
note(CX+8.6, 24.9, "positions\ndaily_runs", DB_L, fsz=7.8)

# Loop-back arrow left spine
ax.plot([1.0, 1.0], [24.37, 33.85], color=INT_L, lw=2.0, zorder=3)
ax.annotate("", xy=(1.0, 33.85), xytext=(1.0, 33.84),
            arrowprops=dict(arrowstyle="-|>", color=INT_L, lw=2.0, mutation_scale=16), zorder=3)
elbow(1.0, 33.85, CX-7.9, 33.85, INT_L, lw=2.0)
ax.text(0.42, 29.1, "repeat\nevery\n15 min", fontsize=9.5, color=INT_L,
        fontfamily=FONT, va="center", ha="center", fontweight="bold", rotation=90)

v_arr(CX, 24.37, 22.9, "#666666", lw=2.0, label="  4:30 PM — market close")


# ══════════════════════════════════════════════════════════════════════════════
# ③ EOD   y: 13.5 → 22.5
# ══════════════════════════════════════════════════════════════════════════════
section_bg(13.5, 22.5, EOD_L)
section_hdr(22.0, "③  EOD  —  4:30 PM ET", EOD_L, right_note="orchestrator.eod()")

# Dedup gate
diamond(CX, 21.2, 4.2, 1.0, "Already\nran EOD?", SKIP)
v_arr(CX, 22.9, 21.7, EOD_L, lw=1.8)
note(CX+2.3, 21.2, "YES → skip\n(dedup guard)", SKIP, fsz=7.8)
v_arr(CX, 20.7, 20.05, EOD_L, lw=1.8)

# Close + Phantom cleanup
rbox(CX-3.8, 19.5, 6.5, 0.95,
     "Close All Positions",
     "Market-sell remaining · cancel bracket legs",
     EOD_L, title_sz=10, sub_sz=7.8)
rbox(CX+3.8, 19.5, 6.5, 0.95,
     "Phantom STOP Cleanup",
     "STOP with no fill_price → reclassify UNFILLED\nKeeps exit counts + P&L accurate",
     EOD_L, title_sz=10, sub_sz=7.8)
elbow(CX, 20.05, CX-3.8, 20.02, EOD_L, lw=1.5)
elbow(CX, 20.05, CX+3.8, 20.02, EOD_L, lw=1.5)
elbow(CX-3.8, 19.02, CX, 18.55, EOD_L, lw=1.4)
elbow(CX+3.8, 19.02, CX, 18.55, EOD_L, lw=1.4)

# Performance + Eval
rbox(CX-3.8, 18.02, 6.5, 0.95,
     "Daily Performance",
     "P&L · win rate · trade count · friction_gap\nbest/worst trade · alpha_vs_spy",
     EOD_L, title_sz=10, sub_sz=7.8)
rbox(CX+3.8, 18.02, 6.5, 0.95,
     "30-Day Rolling Eval",
     "win_rate ≥ 70% · avg P&L ≥ $500\nNATIVE_TRAIL confirmed · no integrity flags",
     EOD_L, title_sz=10, sub_sz=7.8)
v_arr(CX, 18.55, 18.5, EOD_L, lw=1.8)
elbow(CX, 18.5, CX-3.8, 18.49, EOD_L, lw=1.5)
elbow(CX, 18.5, CX+3.8, 18.49, EOD_L, lw=1.5)
elbow(CX-3.8, 17.54, CX, 17.05, EOD_L, lw=1.4)
elbow(CX+3.8, 17.54, CX, 17.05, EOD_L, lw=1.4)

# Summary + alerts
rbox(CX, 16.52, 9.0, 1.05,
     "Daily Summary + Alerts",
     "Email via Gmail SMTP · scan_results observability log · alert on crash or unclosed positions",
     EOD_L, title_sz=10.5, sub_sz=8.5)
elbow(CX+4.5, 16.52, CX+8.5, 16.52, DB_L, lw=1.4)
note(CX+8.6, 16.7, "daily_performance\nscan_results\npositions (final)", DB_L, fsz=7.8)

v_arr(CX, 15.99, 14.55, "#666666", lw=2.0)


# ══════════════════════════════════════════════════════════════════════════════
# ④ INFRASTRUCTURE   y: 2.0 → 14.0
# ══════════════════════════════════════════════════════════════════════════════
section_bg(2.0, 14.0, DB_L)
section_hdr(13.5, "④  SUPABASE  +  INFRASTRUCTURE", DB_L)

# Supabase tables top row
for cx_t, t, s in [
    (CX-6.5, "positions",
     "OPEN / CLOSED / UNFILLED · fill_price\nstop_loss · confidence · close_reason"),
    (CX,     "scan_results",
     "premarket / intraday / eod runs\nhalt_reasons · candidates · pipeline_counts"),
    (CX+6.5, "daily_performance",
     "P&L · win_rate · friction_gap\nalpha_vs_spy · 30-day rolling"),
]:
    rbox(cx_t, 12.05, 5.8, BH, t, s, DB_L, title_sz=9.5, sub_sz=7.5)

# Second row
for cx_t, t, s in [
    (CX-3.5, "trade_plans / daily_runs",
     "One row per scan event · run_id FK on positions"),
    (CX+3.5, "ML model (scanner/)",
     "train_model.py · features_*.csv\nP(hit +2%) · re-ranks candidates"),
]:
    rbox(cx_t, 10.55, 5.8, BH, t, s, DB_L, title_sz=9.5, sub_sz=7.5)

# Dashboard
rbox(CX, 8.95, 10.5, 1.1,
     "Streamlit Dashboard",
     "Today tab · Positions tracker · P&L history · Confidence slice · Sector breakdown",
     DB_L, title_sz=11, sub_sz=8.5)
for cx_t in [CX-6.5, CX, CX+6.5]:
    elbow(cx_t, 11.55, CX+(cx_t-CX)*0.08, 9.5, DB_L, lw=1.2)
elbow(CX-3.5, 10.05, CX-1.0, 9.5, DB_L, lw=1.2)
elbow(CX+3.5, 10.05, CX+1.0, 9.5, DB_L, lw=1.2)

# GitHub Actions + Alpaca
rbox(CX-5.5, 7.35, 8.5, 1.1,
     "GitHub Actions + cron-job.org",
     "Premarket 10 AM ET · Intraday 0,15,30 14-19 UTC\nEOD 55 19 UTC · workflow_dispatch trigger",
     DB_L, title_sz=10, sub_sz=8.5)
rbox(CX+5.5, 7.35, 8.5, 1.1,
     "Alpaca Paper Trading",
     "Bracket orders (BUY + take-profit + stop-loss)\nAll orders tagged strata_{ticker}_{ts}",
     ALP, title_sz=10, sub_sz=8.5)
elbow(CX-5.5, 7.9, CX-5.5, 8.4, DB_L, lw=1.3)
elbow(CX+5.5, 7.9, CX+5.5, 8.4, DB_L, lw=1.3)

# June 8 gate banner
ax.add_patch(FancyBboxPatch((CX-8.0, 5.55), 16.0, 0.95,
                             boxstyle="round,pad=0.1,rounding_size=0.2",
                             linewidth=1.5, edgecolor=SKIP, facecolor=SKIP, alpha=0.12, zorder=3))
ax.text(CX, 6.02,
        "June 8 Eval Gate:  win rate ≥ 70%  ·  avg daily P&L ≥ $500  ·"
        "  NATIVE_TRAIL confirmed  ·  no integrity flags  ·  confidence ≥ 7/10",
        fontsize=9, color="#8B0000", fontfamily=FONT, va="center", ha="center",
        fontweight="bold", zorder=5)

note(CX, 4.85,
     "Same Supabase project as Strategy B (no prefix)  ·  Same Alpaca account (strata_ tag isolates A)  ·  $50,000 paper capital",
     "#777777", ha="center", fsz=8.5)


# ── Legend ─────────────────────────────────────────────────────────────────────
legend_items = [
    (PRE_L,  "Premarket step"),
    (INT_L,  "Intraday step"),
    (EOD_L,  "EOD step"),
    (DB_L,   "Infrastructure / Supabase"),
    (SKIP,   "Decision gate / reject path"),
    (ALP,    "Alpaca execution"),
]
lx, ly = 1.5, 3.85
ax.text(lx, ly+0.55, "Legend", fontsize=9.5, fontweight="bold",
        color="#555555", fontfamily=FONT, va="center")
for i, (col, lbl) in enumerate(legend_items):
    ax.add_patch(FancyBboxPatch((lx, ly-i*0.63-0.18), 0.68, 0.38,
                                boxstyle="round,pad=0.05,rounding_size=0.07",
                                facecolor=col, linewidth=0, zorder=5))
    ax.text(lx+0.88, ly-i*0.63, lbl, fontsize=8.5, color="#444444",
            fontfamily=FONT, va="center", zorder=5)


# ── Save ───────────────────────────────────────────────────────────────────────
out = "/Users/amitgarg/Claude Projects/trading-agent/workflow_diagram.png"
plt.savefig(out, dpi=DPI, bbox_inches="tight",
            facecolor=fig.get_facecolor(), edgecolor="none")
print(f"Saved: {out}")
plt.close()
