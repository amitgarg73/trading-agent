"""Generates a full-day agent workflow diagram as a PNG."""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

FIG_W, FIG_H = 22, 30
fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
ax.set_xlim(0, FIG_W)
ax.set_ylim(0, FIG_H)
ax.axis("off")
fig.patch.set_facecolor("#F7F8FA")

# ── Palette ───────────────────────────────────────────────────────────────────
C_PRE_BG   = "#1A3A6A"   # navy
C_PRE_BOX  = "#2C5F9E"
C_PRE_ACC  = "#4A90D9"
C_INT_BG   = "#145A32"   # dark green
C_INT_BOX  = "#1E8449"
C_INT_ACC  = "#27AE60"
C_EOD_BG   = "#784212"   # dark orange
C_EOD_BOX  = "#B7550A"
C_EOD_ACC  = "#F39C12"
C_DB_BG    = "#2C3E50"
C_DB_BOX   = "#4A5568"
C_SKIP     = "#C0392B"
C_WHITE    = "#FFFFFF"
C_LIGHT    = "#ECF0F1"
C_ARROW    = "#555555"
C_SECTION  = "#FAFAFA"

FONT = "DejaVu Sans"


# ── Helpers ───────────────────────────────────────────────────────────────────
def section_bg(x, y, w, h, color, alpha=0.12, radius=0.4):
    box = FancyBboxPatch((x, y), w, h,
                         boxstyle=f"round,pad=0,rounding_size={radius}",
                         linewidth=2, edgecolor=color,
                         facecolor=color, alpha=alpha, zorder=1)
    ax.add_patch(box)


def section_label(x, y, text, color):
    ax.text(x, y, text, fontsize=13, fontweight="bold", color=color,
            fontfamily=FONT, va="center", ha="left", zorder=5,
            bbox=dict(boxstyle="round,pad=0.3", facecolor=color, alpha=0.15, edgecolor="none"))


def box(cx, cy, w, h, label, sublabel="", bg="#2C5F9E", fg="#FFFFFF", fontsize=9.5):
    bx = FancyBboxPatch((cx - w/2, cy - h/2), w, h,
                         boxstyle="round,pad=0.08,rounding_size=0.15",
                         linewidth=1.5, edgecolor=bg,
                         facecolor=bg, alpha=0.92, zorder=3)
    ax.add_patch(bx)
    if sublabel:
        ax.text(cx, cy + 0.13, label, fontsize=fontsize, fontweight="bold",
                color=fg, fontfamily=FONT, va="center", ha="center", zorder=4)
        ax.text(cx, cy - 0.18, sublabel, fontsize=7.5, color=fg,
                fontfamily=FONT, va="center", ha="center", zorder=4, alpha=0.85)
    else:
        ax.text(cx, cy, label, fontsize=fontsize, fontweight="bold",
                color=fg, fontfamily=FONT, va="center", ha="center", zorder=4)
    return (cx, cy)


def diamond(cx, cy, w, h, label, color=C_SKIP):
    pts = [(cx, cy+h/2), (cx+w/2, cy), (cx, cy-h/2), (cx-w/2, cy)]
    poly = plt.Polygon(pts, closed=True, facecolor=color, edgecolor=color,
                       alpha=0.85, linewidth=1.5, zorder=3)
    ax.add_patch(poly)
    ax.text(cx, cy, label, fontsize=8, fontweight="bold", color=C_WHITE,
            fontfamily=FONT, va="center", ha="center", zorder=4)


def arrow(x1, y1, x2, y2, color=C_ARROW, label="", lw=1.8):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=color,
                                lw=lw, mutation_scale=14),
                zorder=2)
    if label:
        mx, my = (x1+x2)/2, (y1+y2)/2
        ax.text(mx + 0.12, my, label, fontsize=7.5, color=color,
                fontfamily=FONT, va="center", ha="left", zorder=5, fontstyle="italic")


def horiz_arrow(x1, x2, y, color=C_ARROW, label=""):
    arrow(x1, y, x2, y, color=color, label=label)


def vert_arrow(x, y1, y2, color=C_ARROW, label=""):
    arrow(x, y1, x, y2, color=color, label=label)


# ═══════════════════════════════════════════════════════════════════════════════
# TITLE
# ═══════════════════════════════════════════════════════════════════════════════
ax.text(FIG_W/2, 29.3, "AI Trading Agent — Full Day Workflow",
        fontsize=18, fontweight="bold", color="#1A3A6A",
        fontfamily=FONT, va="center", ha="center", zorder=5)
ax.text(FIG_W/2, 28.85, "Premarket  ·  Intraday  ·  EOD  ·  Supabase",
        fontsize=11, color="#666666",
        fontfamily=FONT, va="center", ha="center", zorder=5)

# thin divider
ax.plot([1, FIG_W-1], [28.6, 28.6], color="#CCCCCC", lw=1, zorder=2)


# ═══════════════════════════════════════════════════════════════════════════════
# PREMARKET SECTION  y: 19.5 → 28.2
# ═══════════════════════════════════════════════════════════════════════════════
section_bg(0.5, 19.5, FIG_W - 1, 8.9, C_PRE_BG, alpha=0.07)
section_label(1.1, 28.05, "① PREMARKET  —  9:45 AM ET", C_PRE_BG)
ax.text(FIG_W - 1.1, 28.05, "orchestrator.py  →  premarket()", fontsize=8.5,
        color="#888888", fontfamily=FONT, va="center", ha="right", zorder=5)

# Row 1: Universe + Scanner
box(4.5,  27.1, 3.2, 0.7, "Universe", "450+ tickers  (S&P 500 + Nasdaq 100)", C_PRE_BOX)
box(10.0, 27.1, 3.5, 0.7, "Technical Scanner", "RSI · MACD · Bollinger · Volume", C_PRE_BOX)
box(16.5, 27.1, 3.2, 0.7, "Market Context", "VIX · Futures · Fear&Greed · Calendar", C_PRE_BOX)
horiz_arrow(6.1, 8.25, 27.1, C_PRE_ACC)
horiz_arrow(11.75, 14.85, 27.1, C_PRE_ACC)

# Market context skip diamond
diamond(16.5, 26.1, 2.0, 0.65, "SKIP?", C_SKIP)
vert_arrow(16.5, 26.75, 26.42, C_PRE_ACC)
ax.text(17.7, 26.1, "YES → No trades today", fontsize=8, color=C_SKIP,
        fontfamily=FONT, va="center", fontstyle="italic")
ax.text(16.7, 25.75, "NO", fontsize=8, color=C_PRE_ACC,
        fontfamily=FONT, va="center", fontstyle="italic")

# Arrow from scanner to news intel
vert_arrow(10.0, 26.75, 26.05, C_PRE_ACC)

# Row 2: News Intel
box(10.0, 25.65, 3.5, 0.7, "News Intelligence", "Earnings blackout · News sentiment", C_PRE_BOX)

# Step 1.75 Pre-filter
vert_arrow(10.0, 25.3, 24.75, C_PRE_ACC)
box(10.0, 24.4, 3.5, 0.7, "Strategy Pre-filter", "Score ≥ 4  →  drops weak candidates", C_PRE_BOX)

# Row 3: ML → Live Prices → VWAP
vert_arrow(10.0, 24.05, 23.45, C_PRE_ACC)

box(4.5,  23.1, 3.2, 0.7, "ML Scorer", "P(hit +2%)  AUC 0.78  →  re-rank", C_PRE_BOX)
box(10.0, 23.1, 3.5, 0.7, "Live Price Refresh", "Alpaca ask prices  (step 1.8)", C_PRE_BOX)
box(16.5, 23.1, 3.2, 0.7, "VWAP + RS Enrichment", "above_vwap · rs_vs_spy  (step 1.85)", C_PRE_BOX)

horiz_arrow(8.25, 6.1,  23.1, C_PRE_ACC)   # ← to ML
horiz_arrow(11.75, 14.85, 23.1, C_PRE_ACC) # → to VWAP
# re-sort after VWAP
horiz_arrow(14.85, 11.75, 22.5, C_PRE_ACC)
ax.text(13.3, 22.62, "re-sort", fontsize=7.5, color=C_PRE_ACC,
        fontfamily=FONT, va="center", ha="center", fontstyle="italic")

# Claude Strategy
vert_arrow(10.0, 22.75, 22.1, C_PRE_ACC)
box(10.0, 21.75, 4.2, 0.7, "Claude Strategy Agent", "Selects trades · entry · target · stop · confidence", C_PRE_BOX, fontsize=9.5)

# Risk → Sector → Guardrails in a row
vert_arrow(10.0, 21.4, 20.75, C_PRE_ACC)

box(5.5,  20.4, 2.8, 0.7, "Risk Agent", "R:R ≥ 3:1 · sizing · price", C_PRE_BOX)
box(10.5, 20.4, 2.8, 0.7, "Sector Guard", "Max 3 per sector", C_PRE_BOX)
box(15.5, 20.4, 2.8, 0.7, "Guardrails", "6 safety checks", C_PRE_BOX)

horiz_arrow(8.4, 6.9, 20.4, C_PRE_ACC)
horiz_arrow(11.9, 14.1, 20.4, C_PRE_ACC)
horiz_arrow(12.85, 14.1, 20.4, C_PRE_ACC)

# Approved trades
vert_arrow(10.0, 20.05, 19.8, C_PRE_ACC)
box(10.0, 19.45, 4.2, 0.65, "Alpaca Bracket Orders", "Entry limit · Take-profit · Native trailing stop", "#1A5276", fontsize=9.5)

# DB arrow from premarket
arrow(12.1, 19.45, 17.2, 19.45, C_DB_BG, label="positions · trade_plans\nscan_results", lw=1.4)


# ═══════════════════════════════════════════════════════════════════════════════
# INTRADAY SECTION  y: 11.5 → 19.0
# ═══════════════════════════════════════════════════════════════════════════════
section_bg(0.5, 11.5, FIG_W - 1, 7.2, C_INT_BG, alpha=0.07)
section_label(1.1, 18.75, "② INTRADAY  —  every 15 min  (10:00 AM – 3:45 PM ET)", C_INT_BG)
ax.text(FIG_W - 1.1, 18.75, "orchestrator.py  →  intraday()", fontsize=8.5,
        color="#888888", fontfamily=FONT, va="center", ha="right", zorder=5)

# Section join arrow
vert_arrow(10.0, 19.12, 18.5, C_INT_ACC, label="")
ax.text(10.3, 18.8, "Positions open", fontsize=7.5, color=C_INT_ACC,
        fontfamily=FONT, va="center", fontstyle="italic")

# Reconcile + Refresh
box(5.5,  18.05, 3.0, 0.65, "Alpaca Reconcile", "OPEN in DB but gone → UNFILLED", C_INT_BOX)
box(11.5, 18.05, 3.0, 0.65, "Refresh Positions", "Sync price · P&L · high watermark", C_INT_BOX)
horiz_arrow(7.0, 10.0, 18.05, C_INT_ACC)

# Decision row
vert_arrow(11.5, 17.72, 17.1, C_INT_ACC)

box(4.0,  16.7, 2.4, 0.65, "TARGET hit?", "+2% reached", C_INT_BOX)
box(8.2,  16.7, 2.4, 0.65, "STOP hit?", "-0.67% reached", C_INT_BOX)
box(12.5, 16.7, 2.4, 0.65, "TRAIL fired?", "Alpaca native stop", C_INT_BOX)
box(17.0, 16.7, 2.6, 0.65, "Tiered Lock-in?", "Realized ≥ $716", C_INT_BOX)

horiz_arrow(10.3, 5.2, 16.7, C_INT_ACC)
horiz_arrow(9.1, 7.0, 16.7, C_INT_ACC)
horiz_arrow(13.7, 11.4, 16.7, C_INT_ACC)
horiz_arrow(15.9, 14.7, 16.7, C_INT_ACC)

# Outcomes
vert_arrow(4.0,  16.37, 15.55, C_INT_ACC)
vert_arrow(8.2,  16.37, 15.55, C_INT_ACC)
vert_arrow(12.5, 16.37, 15.55, C_INT_ACC)

box(8.2, 15.2, 3.6, 0.65, "Close Position", "Book P&L  →  close_reason  →  exit_mechanism", C_INT_BOX)

# Tier 1 / Tier 2
vert_arrow(17.0, 16.37, 15.55, C_INT_ACC)
box(17.0, 15.2, 2.8, 0.65, "Tier 1  $716", "Tighten trail · let winners ride", C_INT_BOX)
vert_arrow(17.0, 14.87, 14.35, C_INT_ACC)
box(17.0, 14.0, 2.8, 0.65, "Tier 2  $1,000", "Close all · protect the day", C_INT_BOX)

# Loop back
ax.annotate("", xy=(10.0, 18.37), xytext=(2.0, 14.0),
            arrowprops=dict(arrowstyle="-|>", color=C_INT_ACC, lw=1.6,
                            connectionstyle="arc3,rad=-0.3"), zorder=2)
ax.text(1.2, 16.2, "repeat\nevery\n15 min", fontsize=8, color=C_INT_ACC,
        fontfamily=FONT, va="center", ha="center", fontstyle="italic")

# DB arrow intraday
arrow(9.8, 15.2, 17.5, 15.2, C_DB_BG, label="positions updated", lw=1.4)

# Transition to EOD
vert_arrow(8.2, 11.87, 11.3, C_INT_ACC)
ax.text(8.7, 11.6, "4:30 PM — market close", fontsize=8, color="#888888",
        fontfamily=FONT, va="center", fontstyle="italic")


# ═══════════════════════════════════════════════════════════════════════════════
# EOD SECTION  y: 3.5 → 11.0
# ═══════════════════════════════════════════════════════════════════════════════
section_bg(0.5, 3.5, FIG_W - 1, 7.2, C_EOD_BG, alpha=0.07)
section_label(1.1, 10.75, "③ EOD  —  4:30 PM ET", C_EOD_BG)
ax.text(FIG_W - 1.1, 10.75, "orchestrator.py  →  eod()", fontsize=8.5,
        color="#888888", fontfamily=FONT, va="center", ha="right", zorder=5)

# Close all
box(5.5, 10.1, 3.0, 0.65, "Close All Positions", "Market-sell remaining · cancel brackets", C_EOD_BOX)
horiz_arrow(4.0, 10.1, 10.1, C_EOD_ACC)

# Performance
horiz_arrow(7.0, 8.5, 10.1, C_EOD_ACC)
box(9.8, 10.1, 2.8, 0.65, "Performance Agent", "P&L · win rate · best/worst · capital", C_EOD_BOX)

# Daily Summary
horiz_arrow(11.2, 13.0, 10.1, C_EOD_ACC)
box(14.3, 10.1, 2.8, 0.65, "Daily Summary", "Claude Haiku · 3-sentence narrative", C_EOD_BOX)

# Eval
horiz_arrow(15.7, 17.5, 10.1, C_EOD_ACC)
box(18.8, 10.1, 2.8, 0.65, "Eval Agent", "Grade · scorecard · integrity checks", C_EOD_BOX)

# DB arrows from EOD
arrow(5.5, 9.77, 5.5, 8.8, C_DB_BG, lw=1.4)
arrow(9.8, 9.77, 9.8, 8.8, C_DB_BG, lw=1.4)
arrow(14.3, 9.77, 14.3, 8.8, C_DB_BG, lw=1.4)
arrow(18.8, 9.77, 18.8, 8.8, C_DB_BG, lw=1.4)


# ═══════════════════════════════════════════════════════════════════════════════
# SUPABASE + DASHBOARD  y: 3.8 → 8.6
# ═══════════════════════════════════════════════════════════════════════════════
section_bg(0.5, 3.8, FIG_W - 1, 4.7, C_DB_BG, alpha=0.07)
section_label(1.1, 8.35, "④ SUPABASE  +  DASHBOARD", C_DB_BG)

# Supabase tables
tables = [
    ("trade_plans", "Daily trade plan"),
    ("positions", "Every open & closed position"),
    ("daily_performance", "EOD P&L record"),
    ("scan_results", "Premarket scans · scorecard\nintraday snapshots · daily summary"),
]
table_xs = [3.0, 7.5, 12.5, 17.5]
for (name, desc), tx in zip(tables, table_xs):
    box(tx, 7.2, 3.8, 0.85, name, desc, C_DB_BOX, fontsize=9)

# Dashboard
box(11.0, 5.5, 5.5, 1.0,
    "Streamlit Dashboard",
    "Summary · Today · Positions · Performance · Scan Log",
    "#2C3E50", fontsize=10)

# Arrows from tables to dashboard
for tx in table_xs:
    arrow(tx, 6.77, 10.5 + (tx - 10.0) * 0.12, 6.0, C_DB_BOX, lw=1.2)

# Dashboard URL
ax.text(11.0, 4.9, "trading-agent.streamlit.app  ·  password protected",
        fontsize=8.5, color="#888888", fontfamily=FONT, va="center", ha="center")

# GitHub Actions cron note
box(4.5, 5.5, 4.5, 1.0,
    "GitHub Actions + cron-job.org",
    "Premarket 9:45 AM  ·  Intraday /15 min\nEOD 4:30 PM  ·  3× retry on failure",
    "#4A5568", fontsize=9)

# Health check
box(17.5, 5.5, 3.5, 1.0,
    "Health Check",
    "8:45 AM ET  Mon–Fri\nEmail alert on failure",
    "#4A5568", fontsize=9)

# bottom note
ax.text(FIG_W/2, 4.05,
        "Alpaca Paper Trading  ·  Native trailing stop  ·  Bracket orders (entry + take-profit + stop)",
        fontsize=9, color="#555555", fontfamily=FONT, va="center", ha="center",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="#EFEFEF", edgecolor="#CCCCCC", alpha=0.8))

# ── Legend ────────────────────────────────────────────────────────────────────
legend_items = [
    (C_PRE_BOX,  "Premarket agent / step"),
    (C_INT_BOX,  "Intraday agent / check"),
    (C_EOD_BOX,  "EOD agent / step"),
    (C_DB_BOX,   "Infrastructure / storage"),
    (C_SKIP,     "Decision / gate"),
]
lx, ly = 1.2, 3.3
for i, (color, label) in enumerate(legend_items):
    patch = FancyBboxPatch((lx + i*3.8, ly - 0.18), 0.45, 0.36,
                           boxstyle="round,pad=0.04,rounding_size=0.06",
                           facecolor=color, edgecolor=color, alpha=0.9, zorder=4)
    ax.add_patch(patch)
    ax.text(lx + i*3.8 + 0.6, ly, label, fontsize=8, color="#333333",
            fontfamily=FONT, va="center", zorder=5)

# ── Save ──────────────────────────────────────────────────────────────────────
plt.tight_layout(pad=0)
out = "/Users/amitgarg/Claude Projects/trading-agent/workflow_diagram.png"
plt.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
print(f"Saved: {out}")
plt.close()
