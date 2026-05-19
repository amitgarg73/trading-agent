"""Generates a clean full-day agent workflow diagram as SVG + PNG."""
import os, math
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch

# ── Canvas ────────────────────────────────────────────────────────────────────
W, H = 32, 52          # figure inches
DPI  = 120
fig, ax = plt.subplots(figsize=(W, H))
ax.set_xlim(0, W)
ax.set_ylim(0, H)
ax.axis("off")
fig.patch.set_facecolor("#F4F6F8")
ax.set_facecolor("#F4F6F8")

# ── Colors ────────────────────────────────────────────────────────────────────
PRE   = "#1A3A6A"   # navy   – premarket
INT   = "#155724"   # green  – intraday
EOD   = "#7B3100"   # brown  – eod
DB    = "#2D3748"   # slate  – infra / supabase
SKIP  = "#C0392B"   # red    – gate / decision
WHITE = "#FFFFFF"
LIGHT = "#F0F4FF"

PRE_L = "#2E5FA3"
INT_L = "#1E7A3E"
EOD_L = "#C96A1A"
DB_L  = "#4A5568"

FONT  = "DejaVu Sans"

# ── Helpers ───────────────────────────────────────────────────────────────────
BW, BH = 5.8, 1.05    # default box width / height
GAP_X  = 1.0          # horizontal gap between boxes
ROW_H  = 2.1          # row-to-row vertical spacing

def rbox(cx, cy, w, h, title, sub="", fill=PRE_L, title_sz=10.5, sub_sz=8.5):
    """Rounded rectangle box."""
    pad = 0.12
    rect = FancyBboxPatch((cx - w/2, cy - h/2), w, h,
                           boxstyle=f"round,pad={pad},rounding_size=0.25",
                           linewidth=0, facecolor=fill, zorder=4)
    ax.add_patch(rect)
    # subtle inner border
    rect2 = FancyBboxPatch((cx - w/2, cy - h/2), w, h,
                            boxstyle=f"round,pad={pad},rounding_size=0.25",
                            linewidth=1.2, edgecolor="white", facecolor="none",
                            alpha=0.25, zorder=5)
    ax.add_patch(rect2)
    if sub:
        ax.text(cx, cy + 0.2, title, fontsize=title_sz, fontweight="bold",
                color=WHITE, fontfamily=FONT, va="center", ha="center", zorder=6)
        ax.text(cx, cy - 0.26, sub, fontsize=sub_sz, color=WHITE,
                fontfamily=FONT, va="center", ha="center", zorder=6, alpha=0.85)
    else:
        ax.text(cx, cy, title, fontsize=title_sz, fontweight="bold",
                color=WHITE, fontfamily=FONT, va="center", ha="center", zorder=6)

def diamond(cx, cy, w, h, label, fill=SKIP):
    """Decision diamond."""
    pts = [(cx, cy+h/2), (cx+w/2, cy), (cx, cy-h/2), (cx-w/2, cy)]
    poly = plt.Polygon(pts, closed=True, facecolor=fill, edgecolor="white",
                       linewidth=1, zorder=4, alpha=0.9)
    ax.add_patch(poly)
    ax.text(cx, cy, label, fontsize=9.5, fontweight="bold", color=WHITE,
            fontfamily=FONT, va="center", ha="center", zorder=6)

def section_bg(y_bot, y_top, fill, alpha=0.06):
    rect = FancyBboxPatch((0.6, y_bot), W - 1.2, y_top - y_bot,
                           boxstyle="round,pad=0,rounding_size=0.5",
                           linewidth=1.5, edgecolor=fill,
                           facecolor=fill, alpha=alpha, zorder=1)
    ax.add_patch(rect)

def section_hdr(y, label, fill, right_note=""):
    # colored pill header
    pill = FancyBboxPatch((0.9, y - 0.42), 9.5, 0.84,
                           boxstyle="round,pad=0,rounding_size=0.42",
                           linewidth=0, facecolor=fill, alpha=0.15, zorder=3)
    ax.add_patch(pill)
    ax.text(1.5, y, label, fontsize=13, fontweight="bold", color=fill,
            fontfamily=FONT, va="center", ha="left", zorder=4)
    if right_note:
        ax.text(W - 1.0, y, right_note, fontsize=9, color="#888888",
                fontfamily=FONT, va="center", ha="right", zorder=4,
                fontstyle="italic")

def arr(x1, y1, x2, y2, color="#888888", lw=1.8, label="", label_side="right"):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=lw,
                                mutation_scale=16), zorder=3)
    if label:
        mx, my = (x1+x2)/2, (y1+y2)/2
        dx = 0.2 if label_side == "right" else -0.2
        ha = "left" if label_side == "right" else "right"
        ax.text(mx + dx, my, label, fontsize=8, color=color, fontstyle="italic",
                fontfamily=FONT, va="center", ha=ha, zorder=4)

def v_arr(cx, y1, y2, color="#888888", lw=1.8, label=""):
    arr(cx, y1, cx, y2, color=color, lw=lw, label=label)

def h_arr(x1, x2, cy, color="#888888", lw=1.8, label=""):
    arr(x1, cy, x2, cy, color=color, lw=lw, label=label)

def elbow(x1, y1, x2, y2, color="#888888", lw=1.8):
    """L-shaped connector: go vertical first, then horizontal."""
    ax.plot([x1, x1, x2], [y1, y2, y2], color=color, lw=lw, zorder=3,
            solid_capstyle="round")
    ax.annotate("", xy=(x2, y2), xytext=(x2 - 0.01 * (1 if x2 > x1 else -1), y2),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=lw,
                                mutation_scale=16), zorder=3)


# ══════════════════════════════════════════════════════════════════════════════
# TITLE
# ══════════════════════════════════════════════════════════════════════════════
ax.text(W/2, 51.1, "AI Trading Agent — Full Day Workflow",
        fontsize=22, fontweight="bold", color="#1A3A6A",
        fontfamily=FONT, va="center", ha="center", zorder=6)
ax.text(W/2, 50.35, "Premarket  ·  Intraday (every 15 min)  ·  EOD  ·  Supabase",
        fontsize=12, color="#666666",
        fontfamily=FONT, va="center", ha="center", zorder=6)
ax.plot([1.5, W-1.5], [49.9, 49.9], color="#CCCCCC", lw=1.2, zorder=3)


# ══════════════════════════════════════════════════════════════════════════════
# ① PREMARKET   y: 33.0 → 49.5
# ══════════════════════════════════════════════════════════════════════════════
section_bg(33.0, 49.5, PRE)
section_hdr(49.0, "①  PREMARKET  —  9:45 AM ET", PRE,
            right_note="orchestrator.py → premarket()")

# --- Row A: Universe → Scanner -----------------------------------------------
rbox(8.0,  47.8, BW, BH, "Universe", "450+ tickers  (S&P 500 + Nasdaq 100)", PRE_L)
rbox(16.0, 47.8, BW, BH, "Technical Scanner", "RSI · MACD · Bollinger Bands · Volume ratio", PRE_L)
h_arr(10.9, 13.1, 47.8, PRE_L, lw=2)

# --- Market Context (right column) -------------------------------------------
rbox(26.0, 47.8, BW, BH, "Market Context", "VIX gate · Futures signal · Fear & Greed · Calendar", PRE_L)
# scanner → market context (via top)
elbow(16.0, 48.32, 26.0, 48.32, PRE_L, lw=2)

# SKIP gate
diamond(26.0, 46.45, 3.4, 1.0, "SKIP?", SKIP)
v_arr(26.0, 47.32, 46.95, PRE_L, lw=2)
# skip label
ax.text(27.9, 46.45, "YES  →  No trades today", fontsize=9, color=SKIP,
        fontfamily=FONT, va="center", fontstyle="italic")
ax.text(26.35, 45.85, "NO ↓", fontsize=9, color="#AAAAAA",
        fontfamily=FONT, va="center")

# --- Row B: News Intelligence -------------------------------------------------
rbox(16.0, 45.6, BW, BH, "News Intelligence", "Earnings blackout filter · News sentiment", PRE_L)
v_arr(16.0, 47.32, 46.12, PRE_L, lw=2)

# --- Row C: Pre-filter --------------------------------------------------------
rbox(16.0, 43.4, BW, BH, "Strategy Pre-filter  (step 1.75)", "Drops candidates with score < 4", PRE_L)
v_arr(16.0, 45.12, 43.92, PRE_L, lw=2)

# --- Row D: ML · Live Prices · VWAP (3-up) -----------------------------------
rbox(7.5,  41.2, 5.4, BH, "ML Scorer  (step 1.76)", "P(hit +2%)  AUC 0.78  ·  re-ranks candidates", PRE_L)
rbox(16.0, 41.2, 5.4, BH, "Live Price Refresh  (step 1.8)", "Alpaca real-time ask prices", PRE_L)
rbox(24.5, 41.2, 5.4, BH, "VWAP + RS Enrichment  (step 1.85)", "above_vwap · rs_vs_spy  ·  re-sort", PRE_L)

v_arr(16.0, 42.92, 41.72, PRE_L, lw=2)
# elbow from center to ML (left) and VWAP (right)
elbow(16.0, 41.72, 7.5, 41.72, PRE_L, lw=2)
h_arr(13.7, 18.7, 41.2, PRE_L, lw=2)   # ML → Live Prices
h_arr(18.7, 21.8, 41.2, PRE_L, lw=2)   # Live Prices → VWAP

# VWAP re-sort arrow loops back to center
elbow(24.5, 40.72, 16.0, 40.72, PRE_L, lw=1.5)
ax.text(20.5, 40.5, "re-sort  ↓", fontsize=8.5, color=PRE_L,
        fontfamily=FONT, va="center", ha="center", fontstyle="italic")

# --- Row E: Claude Strategy ---------------------------------------------------
rbox(16.0, 39.2, 7.0, BH, "Claude Strategy Agent", "Selects trades  ·  entry  ·  target  ·  stop  ·  confidence", PRE_L, title_sz=11)
v_arr(16.0, 40.72, 39.72, PRE_L, lw=2)

# --- Row F: Risk · Sector · Guardrails ----------------------------------------
rbox(7.5,  37.0, 5.4, BH, "Risk Agent", "R:R ≥ 3:1  ·  position sizing  ·  price sanity", PRE_L)
rbox(16.0, 37.0, 5.4, BH, "Sector Guard", "Max 3 positions per sector", PRE_L)
rbox(24.5, 37.0, 5.4, BH, "Guardrails", "6 safety checks  ·  daily loss limit  ·  capital cap", PRE_L)

v_arr(16.0, 38.72, 37.52, PRE_L, lw=2)
elbow(16.0, 37.52, 7.5,  37.52, PRE_L, lw=2)
elbow(16.0, 37.52, 24.5, 37.52, PRE_L, lw=2)

# converge back
elbow(7.5,  36.48, 16.0, 36.48, PRE_L, lw=2)
elbow(24.5, 36.48, 16.0, 36.48, PRE_L, lw=2)

# --- Row G: Alpaca Bracket Orders --------------------------------------------
rbox(16.0, 34.8, 7.5, BH, "Alpaca Bracket Orders", "Entry limit  ·  Take-profit  ·  Native trailing stop", "#0D2D5A", title_sz=11.5)
v_arr(16.0, 36.48, 35.32, PRE_L, lw=2)
# DB write
elbow(19.75, 34.8, 26.0, 34.8, DB_L, lw=1.5)
ax.text(23.5, 35.05, "positions · trade_plans", fontsize=8.5,
        color=DB_L, fontfamily=FONT, va="center", ha="center", fontstyle="italic")

# section-to-section arrow
v_arr(16.0, 34.27, 33.35, "#666666", lw=2.2, label="  positions open")


# ══════════════════════════════════════════════════════════════════════════════
# ② INTRADAY   y: 20.5 → 32.5
# ══════════════════════════════════════════════════════════════════════════════
section_bg(20.5, 32.5, INT)
section_hdr(32.0, "②  INTRADAY  —  every 15 min  (10:00 AM – 3:45 PM ET)", INT,
            right_note="orchestrator.py → intraday()")

# --- Row A: Reconcile · Refresh ----------------------------------------------
rbox(9.5,  30.9, 5.8, BH, "Alpaca Reconcile", "OPEN in DB but gone from Alpaca  →  UNFILLED", INT_L)
rbox(22.5, 30.9, 5.8, BH, "Refresh Positions", "Sync price  ·  P&L  ·  high watermark", INT_L)
h_arr(12.4, 19.6, 30.9, INT_L, lw=2)

# --- Row B: Decision gates (4-up) -------------------------------------------
v_arr(16.0, 30.42, 29.52, INT_L, lw=2)

rbox(5.5,  28.85, 4.5, 0.9, "TARGET?", "+2% reached", INT_L, title_sz=10)
rbox(12.0, 28.85, 4.5, 0.9, "STOP?", "−0.67% hit", INT_L, title_sz=10)
rbox(18.5, 28.85, 4.5, 0.9, "TRAIL fired?", "Alpaca native stop", INT_L, title_sz=10)
rbox(26.0, 28.85, 4.5, 0.9, "Lock-in?", "Realized ≥ $716", INT_L, title_sz=10)

elbow(16.0, 29.52, 5.5,  29.3, INT_L, lw=2)
elbow(16.0, 29.52, 12.0, 29.3, INT_L, lw=2)
elbow(16.0, 29.52, 18.5, 29.3, INT_L, lw=2)
elbow(16.0, 29.52, 26.0, 29.3, INT_L, lw=2)

# --- Row C: Outcomes ----------------------------------------------------------
rbox(12.0, 27.2, 6.0, BH, "Close Position", "Book P&L  ·  close_reason  ·  exit_mechanism", INT_L)

elbow(5.5,  28.4, 12.0, 27.72, INT_L, lw=2)
elbow(12.0, 28.4, 12.0, 27.72, INT_L, lw=2)
elbow(18.5, 28.4, 12.0, 27.72, INT_L, lw=2)

# Tiered lock-in outcome
rbox(26.0, 27.2, 4.5, BH, "Tier 1  $716", "Tighten trail\nLet winners ride", INT_L, title_sz=10)
v_arr(26.0, 28.4, 27.72, INT_L, lw=2)
v_arr(26.0, 26.72, 25.82, INT_L, lw=1.5)
rbox(26.0, 25.35, 4.5, BH, "Tier 2  $1,000", "Close all positions\nProtect the day", INT_L, title_sz=10)

# DB write
elbow(15.0, 27.2, 19.5, 27.2, DB_L, lw=1.5)
ax.text(18.0, 27.42, "positions updated", fontsize=8.5,
        color=DB_L, fontfamily=FONT, va="center", ha="center", fontstyle="italic")

# Loop-back arrow (left spine)
ax.annotate("", xy=(4.2, 30.9), xytext=(4.2, 25.5),
            arrowprops=dict(arrowstyle="-|>", color=INT_L, lw=2,
                            connectionstyle="arc3,rad=0"), zorder=3)
ax.plot([4.2, 4.2], [25.5, 30.9], color=INT_L, lw=2, zorder=3)
elbow(4.2, 30.9, 6.6, 30.9, INT_L, lw=2)
ax.text(2.2, 28.2, "repeat\nevery\n15 min", fontsize=10, color=INT_L,
        fontfamily=FONT, va="center", ha="center", fontweight="bold")

# section-to-section
v_arr(16.0, 26.72, 21.35, "#666666", lw=2.2, label="  4:30 PM — market close")


# ══════════════════════════════════════════════════════════════════════════════
# ③ EOD   y: 12.5 → 21.0
# ══════════════════════════════════════════════════════════════════════════════
section_bg(12.5, 21.0, EOD)
section_hdr(20.5, "③  EOD  —  4:30 PM ET", EOD,
            right_note="orchestrator.py → eod()")

rbox(5.5,  18.9, 5.4, BH, "Close All Positions", "Market-sell remaining\nCancel bracket legs", EOD_L)
rbox(13.0, 18.9, 5.4, BH, "Performance Agent", "Daily P&L  ·  win rate\nbest / worst  ·  capital", EOD_L)
rbox(20.5, 18.9, 5.4, BH, "Daily Summary", "Claude Haiku\n3-sentence narrative", EOD_L)
rbox(28.0, 18.9, 5.4, BH, "Eval Agent", "Grade  ·  scorecard\nintegrity checks", EOD_L)

h_arr(8.2,  10.3, 18.9, EOD_L, lw=2)
h_arr(15.7, 17.8, 18.9, EOD_L, lw=2)
h_arr(23.2, 25.3, 18.9, EOD_L, lw=2)

# eval → write scorecard
v_arr(28.0, 18.42, 17.52, EOD_L, lw=1.6, label="  --write")

# DB writes from EOD
for cx in [5.5, 13.0, 20.5, 28.0]:
    v_arr(cx, 18.42, 17.52, DB_L, lw=1.4)

# section-to-section
v_arr(16.0, 17.52, 13.35, "#666666", lw=2.2)


# ══════════════════════════════════════════════════════════════════════════════
# ④ SUPABASE + DASHBOARD   y: 1.5 → 13.0
# ══════════════════════════════════════════════════════════════════════════════
section_bg(1.5, 13.0, DB)
section_hdr(12.5, "④  SUPABASE  +  INFRASTRUCTURE", DB)

# Supabase tables (4-up)
rbox(5.0,  11.0, 5.4, BH, "trade_plans", "Daily trade plan", DB_L)
rbox(11.5, 11.0, 5.4, BH, "positions", "Every open & closed position", DB_L)
rbox(18.5, 11.0, 5.4, BH, "daily_performance", "EOD P&L record", DB_L)
rbox(26.0, 11.0, 5.4, BH, "scan_results", "Premarket · intraday · scorecard\ndaily summary", DB_L)

# Dashboard (center)
rbox(16.0, 8.8, 9.0, 1.2, "Streamlit Dashboard",
     "Summary  ·  Today  ·  Positions  ·  Performance  ·  Scan Log",
     DB_L, title_sz=12)

# Tables → Dashboard
for cx in [5.0, 11.5, 18.5, 26.0]:
    elbow(cx, 10.47, 16.0 + (cx - 16.0)*0.08, 9.4, DB_L, lw=1.5)

ax.text(16.0, 8.15, "trading-agent.streamlit.app  ·  password protected",
        fontsize=9, color="#AAAAAA", fontfamily=FONT, va="center", ha="center")

# GitHub Actions + Health Check
rbox(7.0, 6.6, 6.5, 1.2, "GitHub Actions + cron-job.org",
     "Premarket 9:45 AM  ·  Intraday /15 min\nEOD 4:30 PM  ·  3× retry on failure", DB_L)
rbox(25.0, 6.6, 6.5, 1.2, "Health Check",
     "8:45 AM ET  Mon–Fri\nEmail alert on failure", DB_L)

# arrows connecting infra to main flow
elbow(7.0, 7.2, 7.0, 8.2, DB_L, lw=1.4)
elbow(25.0, 7.2, 25.0, 8.2, DB_L, lw=1.4)

# Alpaca note
rbox(16.0, 5.0, 9.0, 1.0, "Alpaca Paper Trading",
     "Bracket orders (entry + take-profit + native trailing stop)  ·  Simulation fallback", "#0D2D5A", title_sz=11)


# ══════════════════════════════════════════════════════════════════════════════
# LEGEND
# ══════════════════════════════════════════════════════════════════════════════
legend_items = [
    (PRE_L, "Premarket step"),
    (INT_L, "Intraday step"),
    (EOD_L, "EOD step"),
    (DB_L,  "Infrastructure / Supabase"),
    (SKIP,  "Decision gate"),
]
lx, ly = 2.0, 3.2
ax.text(lx, ly + 0.5, "Legend", fontsize=10, fontweight="bold", color="#555555",
        fontfamily=FONT, va="center")
for i, (col, lbl) in enumerate(legend_items):
    bx = FancyBboxPatch((lx, ly - i*0.7 - 0.2), 0.7, 0.4,
                         boxstyle="round,pad=0.05,rounding_size=0.08",
                         facecolor=col, linewidth=0, zorder=5)
    ax.add_patch(bx)
    ax.text(lx + 0.9, ly - i*0.7, lbl, fontsize=9, color="#444444",
            fontfamily=FONT, va="center", zorder=5)


# ── Save ──────────────────────────────────────────────────────────────────────
out = "/Users/amitgarg/Claude Projects/trading-agent/workflow_diagram.png"
plt.savefig(out, dpi=DPI, bbox_inches="tight",
            facecolor=fig.get_facecolor(), edgecolor="none")
print(f"Saved: {out}")
plt.close()
