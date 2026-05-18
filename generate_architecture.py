"""Generates two architecture diagram PNGs for the AI Trading Agent project."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import matplotlib.patheffects as pe

# Color palette
NAVY         = '#1A3A6A'
ORANGE       = '#F47B20'
GREEN        = '#27AE60'
PURPLE       = '#7D3C98'
NAVY_LIGHT   = '#D6E4F7'
ORANGE_LIGHT = '#FDEBD0'
GREEN_LIGHT  = '#D5F5E3'
PURPLE_LIGHT = '#E8DAEF'
RED_LIGHT    = '#FDEDEC'
RED_EDGE     = '#C0392B'
GRAY         = '#6C7A89'
GRAY_LIGHT   = '#F0F3F4'
WHITE        = '#FFFFFF'
DASHED_BORDER = '#4A6FA5'

PROJECT_DIR = "/Users/amitgarg/Claude Projects/trading-agent"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def rounded_box(ax, x, y, w, h, fill, edge, text, fontsize=9,
                text_color=WHITE, bold=False, radius=0.02,
                linestyle='solid', linewidth=1.5, zorder=3):
    box = FancyBboxPatch((x, y), w, h,
                         boxstyle=f"round,pad={radius}",
                         linewidth=linewidth,
                         edgecolor=edge,
                         facecolor=fill,
                         linestyle=linestyle,
                         zorder=zorder)
    ax.add_patch(box)
    weight = 'bold' if bold else 'normal'
    ax.text(x + w / 2, y + h / 2, text,
            ha='center', va='center',
            fontsize=fontsize, color=text_color,
            fontweight=weight, zorder=zorder + 1,
            wrap=False)


def arrow(ax, x1, y1, x2, y2, color=NAVY, lw=1.5, zorder=2):
    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle='->', color=color,
                                lw=lw, mutation_scale=12),
                zorder=zorder)


# ─────────────────────────────────────────────────────────────────────────────
# Diagram 1: High-Level Architecture  (16 × 10 inches)
# ─────────────────────────────────────────────────────────────────────────────

def make_high_level():
    fig, ax = plt.subplots(figsize=(16, 10))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 10)
    ax.axis('off')
    fig.patch.set_facecolor(WHITE)

    # ── Title ─────────────────────────────────────────────────────────────────
    ax.text(8, 9.72, 'AI Trading Agent — High-Level Architecture  v5.6',
            ha='center', va='center', fontsize=16,
            color=NAVY, fontweight='bold')

    # ── Top row: Triggers ─────────────────────────────────────────────────────
    trigger_y = 9.05
    trigger_h = 0.48
    triggers = [
        (0.2,  3.4,  'cron-job.org\nExternal Triggers (3 jobs)'),
        (3.9,  3.4,  'GitHub Actions\nManual Dispatch'),
        (7.6,  3.4,  'Universe Refresh\nMonday 8:30 AM ET'),
        (11.3, 3.4,  'ML Retrain Workflow\n(1st of month, 10 AM UTC)'),
    ]
    for tx, tw, label in triggers:
        rounded_box(ax, tx, trigger_y, tw, trigger_h,
                    fill=NAVY_LIGHT, edge=NAVY, text=label,
                    fontsize=7.5, text_color=NAVY, radius=0.015)

    # Arrows from triggers to orchestrator
    orch_top = 8.42
    arrow(ax, 1.9,   trigger_y, 1.9,   orch_top + 0.3, color=NAVY)
    arrow(ax, 5.6,   trigger_y, 5.6,   orch_top + 0.3, color=NAVY)
    arrow(ax, 9.3,   trigger_y, 9.3,   orch_top + 0.3, color=NAVY)
    arrow(ax, 13.0,  trigger_y, 13.0,  orch_top + 0.3, color=PURPLE)

    # ── Orchestrator ──────────────────────────────────────────────────────────
    orch_x, orch_y, orch_w, orch_h = 0.2, 7.98, 14.8, 0.52
    rounded_box(ax, orch_x, orch_y, orch_w, orch_h,
                fill=ORANGE, edge=ORANGE,
                text='orchestrator.py   —   Master Controller',
                fontsize=11, text_color=WHITE, bold=True, radius=0.02)

    # Arrow from orchestrator to pipeline
    arrow(ax, 7.6, orch_y, 7.6, 7.78, color=NAVY)

    # ── Agent Pipeline dashed container ──────────────────────────────────────
    pipe_x, pipe_y, pipe_w, pipe_h = 0.2, 3.2, 11.8, 4.5
    pipe_box = FancyBboxPatch((pipe_x, pipe_y), pipe_w, pipe_h,
                              boxstyle="round,pad=0.03",
                              linewidth=1.8, edgecolor=DASHED_BORDER,
                              facecolor=GRAY_LIGHT, linestyle='dashed', zorder=1)
    ax.add_patch(pipe_box)
    ax.text(pipe_x + 0.15, pipe_y + pipe_h - 0.1, 'Agent Pipeline',
            ha='left', va='top', fontsize=9, color=DASHED_BORDER, style='italic')

    # Row heights inside pipeline
    row1_y = 7.05   # Pre-market scan agents
    row2_y = 6.0    # ML + Pre-filter row
    row3_y = 4.95   # Claude agents
    row4_y = 3.85   # Portfolio + Intraday/EOD
    box_h  = 0.6

    # Row 1: Pre-market agents
    pre_agents = [
        (0.4,  2.5, 'Market Context\nAgent (VIX gate)'),
        (3.1,  2.5, 'Scanner\n(429 tickers, score ≥ 3)'),
        (5.8,  2.5, 'News Intel\nAgent'),
        (8.5,  2.5, 'Universe\nLoader'),
    ]
    for bx, bw, label in pre_agents:
        rounded_box(ax, bx, row1_y, bw, box_h,
                    fill=NAVY, edge=NAVY, text=label,
                    fontsize=7.5, text_color=WHITE, radius=0.015)

    for i in range(len(pre_agents) - 1):
        x1 = pre_agents[i][0] + pre_agents[i][1]
        x2 = pre_agents[i + 1][0]
        mid_y = row1_y + box_h / 2
        arrow(ax, x1, mid_y, x2, mid_y, color=NAVY)

    # Row 2: Pre-filter + ML Scorer
    ml_agents = [
        (0.4,  2.5, 'Step 1.75\nStrategy Pre-Filter (score ≥ 4)', NAVY),
        (3.1,  2.5, 'Step 1.76\nML Scorer (P(hit +2%))', PURPLE),
        (5.8,  2.5, 'Step 1.8\nLive Price Refresh (Alpaca)', NAVY),
    ]
    for bx, bw, label, col in ml_agents:
        rounded_box(ax, bx, row2_y, bw, box_h,
                    fill=col, edge=col, text=label,
                    fontsize=7.5, text_color=WHITE, radius=0.015)

    for i in range(len(ml_agents) - 1):
        x1 = ml_agents[i][0] + ml_agents[i][1]
        x2 = ml_agents[i + 1][0]
        mid_y = row2_y + box_h / 2
        arrow(ax, x1, mid_y, x2, mid_y, color=NAVY)

    # vertical arrow row1→row2
    arrow(ax, 1.65, row1_y, 1.65, row2_y + box_h, color=NAVY)

    # Row 3: Claude agents + Risk
    claude_agents = [
        (0.4,  2.5, 'Step 2: Strategy Agent\n(Claude, 2% target / 0.67% stop)', ORANGE),
        (3.1,  2.5, 'Step 3: Risk Agent\n(Claude, 3:1 R:R guard)', ORANGE),
        (5.8,  2.5, 'Sector Guard (3.5)\n+ Guardrails (3.75)', NAVY),
    ]
    for bx, bw, label, col in claude_agents:
        rounded_box(ax, bx, row3_y, bw, box_h,
                    fill=col, edge=col, text=label,
                    fontsize=7, text_color=WHITE, radius=0.015)

    for i in range(len(claude_agents) - 1):
        x1 = claude_agents[i][0] + claude_agents[i][1]
        x2 = claude_agents[i + 1][0]
        mid_y = row3_y + box_h / 2
        arrow(ax, x1, mid_y, x2, mid_y, color=NAVY)

    arrow(ax, 4.35, row2_y, 4.35, row3_y + box_h, color=NAVY)

    # Row 4: Portfolio + Intraday/EOD
    exec_agents = [
        (0.4,  2.5, 'Step 4: Portfolio Agent\n(limit orders)', GREEN),
        (3.1,  4.0, 'Intraday Agent\n(every 15 min, 9:45 AM–3:30 PM)', GREEN),
        (7.3,  4.0, 'EOD Agent\n(4:30 PM ET)', GREEN),
    ]
    for bx, bw, label, col in exec_agents:
        rounded_box(ax, bx, row4_y, bw, box_h,
                    fill=col, edge=col, text=label,
                    fontsize=7.5, text_color=WHITE, radius=0.015)

    arrow(ax, 7.3, row3_y, 7.3, row4_y + box_h, color=NAVY)
    arrow(ax, 2.9, row3_y, 2.9, row4_y + box_h, color=NAVY)
    arrow(ax, 1.65, row3_y, 1.65, row4_y + box_h, color=NAVY)

    # ── ML Model feedback loop ────────────────────────────────────────────────
    # Arrow from ML Retrain trigger to ML Model box (in right sidebar)
    arrow(ax, 13.0, trigger_y, 13.0, 6.25, color=PURPLE)

    # ── Bottom row: Data stores ───────────────────────────────────────────────
    ds_y = 2.0
    ds_h = 0.55
    data_stores = [
        (0.2,  4.5,  'Supabase\nPostgreSQL DB',   NAVY),
        (5.0,  4.5,  'Alpaca Paper\nTrading',      NAVY),
        (9.8,  4.5,  'Streamlit\nDashboard',       NAVY),
    ]
    for dx, dw, label, col in data_stores:
        rounded_box(ax, dx, ds_y, dw, ds_h,
                    fill=col, edge=col, text=label,
                    fontsize=8, text_color=WHITE, radius=0.015)

    arrow(ax, 2.45,  pipe_y, 2.45,  ds_y + ds_h,  color=NAVY)
    arrow(ax, 7.1,   pipe_y, 7.1,   ds_y + ds_h,  color=NAVY)
    arrow(ax, 11.75, pipe_y, 11.75, ds_y + ds_h,  color=NAVY)

    # ── Right sidebar: External services ─────────────────────────────────────
    ext_x = 12.15
    ext_w = 3.6
    ext_items = [
        (7.35, 'Anthropic Claude API', ORANGE),
        (6.62, 'ML Model (xgb_scorer.pkl)', PURPLE),
        (5.89, 'yfinance (15-min delay)', GRAY),
        (5.16, 'alternative.me\nFear & Greed', GRAY),
        (4.38, 'Alpaca API\nPaper Trading', GRAY),
    ]
    ax.text(ext_x + ext_w / 2, 7.85, 'External Services',
            ha='center', va='center', fontsize=9,
            color=NAVY, fontweight='bold')
    for ey, label, col in ext_items:
        rounded_box(ax, ext_x, ey, ext_w, 0.52,
                    fill=col, edge=col, text=label,
                    fontsize=7.5, text_color=WHITE, radius=0.015)

    # Retrain feedback arrow: ML model → ML scorer step
    ax.annotate('', xy=(8.5 + 2.5/2, row2_y + box_h),
                xytext=(ext_x + 0.5, 6.62 + 0.26),
                arrowprops=dict(arrowstyle='->', color=PURPLE, lw=1.2,
                                connectionstyle='arc3,rad=0.25', mutation_scale=10),
                zorder=4)
    ax.text(ext_x - 0.1, 6.45, 'monthly retrain', ha='right', va='center',
            fontsize=6.5, color=PURPLE, style='italic')

    # ── Legend ────────────────────────────────────────────────────────────────
    legend_y = 1.25
    legend_items = [
        (NAVY,   'Scan / Market Context'),
        (ORANGE, 'Claude AI Agent'),
        (GREEN,  'Portfolio / Trade Exec'),
        (PURPLE, 'ML Model'),
    ]
    lx = 0.3
    for col, label in legend_items:
        rounded_box(ax, lx, legend_y, 3.3, 0.4,
                    fill=col, edge=col, text=label,
                    fontsize=7.5, text_color=WHITE, radius=0.012)
        lx += 3.5

    # ── Footer ────────────────────────────────────────────────────────────────
    ax.text(8, 0.35, 'AI Trading Agent v5.6  ·  Amit Garg  ·  May 2026',
            ha='center', va='center', fontsize=8, color=GRAY, style='italic')

    plt.tight_layout(pad=0.3)
    out = f"{PROJECT_DIR}/architecture_high_level.png"
    fig.savefig(out, dpi=150, bbox_inches='tight', facecolor=WHITE)
    plt.close(fig)
    print(f"Saved: {out}")


# ─────────────────────────────────────────────────────────────────────────────
# Diagram 2: Low-Level Pipeline  (11 × 24 inches, vertical)
# Shows full v5.6 pipeline with all 13 steps + interdependencies
# ─────────────────────────────────────────────────────────────────────────────

def make_low_level():
    fig, ax = plt.subplots(figsize=(11, 24))
    ax.set_xlim(0, 11)
    ax.set_ylim(0, 24)
    ax.axis('off')
    fig.patch.set_facecolor(WHITE)

    # Title
    ax.text(5.5, 23.65, 'AI Trading Agent — Detailed Pipeline (Low-Level)  v5.6',
            ha='center', va='center', fontsize=14,
            color=NAVY, fontweight='bold')
    ax.text(5.5, 23.3, 'Premarket → Intraday → EOD  ·  All 13 Steps + Feedback Loops',
            ha='center', va='center', fontsize=9.5, color=GRAY, style='italic')

    # ── Right sidebar: Data stores ────────────────────────────────────────────
    sdb_x = 8.2
    sdb_w = 2.6

    def sidebar_box(y, h, label, fill=NAVY_LIGHT, edge=NAVY, fontsize=7.5, tc=NAVY):
        rounded_box(ax, sdb_x, y, sdb_w, h,
                    fill=fill, edge=edge, text=label,
                    fontsize=fontsize, text_color=tc, radius=0.015, zorder=2)

    sidebar_box(20.8, 0.65,
                'Supabase Tables:\ntrade_plans · planned_trades\npositions · daily_performance\nscan_results',
                fontsize=7)
    sidebar_box(15.6, 0.55,
                'ML Model\n(models/xgb_scorer.pkl)',
                fill=PURPLE_LIGHT, edge=PURPLE, tc=PURPLE)
    sidebar_box(12.1, 0.5,
                'Alpaca Paper\nTrading Account',
                fill=ORANGE_LIGHT, edge=ORANGE, tc=NAVY)
    sidebar_box(10.9, 0.5,
                'Supabase:\npositions (open)',
                fontsize=7.5)
    sidebar_box(9.6, 0.5,
                'Alpaca API:\nlive positions',
                fill=ORANGE_LIGHT, edge=ORANGE, tc=NAVY, fontsize=7.5)
    sidebar_box(8.3, 0.6,
                'Supabase:\npositions (closed)\ndaily_performance',
                fill=GREEN_LIGHT, edge=GREEN, tc=NAVY, fontsize=7.5)

    # ── Main pipeline ─────────────────────────────────────────────────────────
    main_x = 0.3
    main_w = 7.6
    box_h  = 0.85
    step_gap = 0.25  # gap between step boxes
    step_total = box_h + step_gap  # 1.1 per step

    def step_box(y, title, detail, fill=NAVY_LIGHT, edge=NAVY,
                 title_color=NAVY, detail_color=GRAY, bold_title=True):
        rounded_box(ax, main_x, y, main_w, box_h,
                    fill=fill, edge=edge, text='',
                    radius=0.018, zorder=2)
        ax.text(main_x + 0.18, y + box_h - 0.16, title,
                ha='left', va='top', fontsize=8.5,
                color=title_color, fontweight='bold' if bold_title else 'normal',
                zorder=3)
        ax.text(main_x + 0.18, y + 0.1, detail,
                ha='left', va='bottom', fontsize=7,
                color=detail_color, zorder=3, wrap=False)

    def arrow_down(x, y1, y2, color=NAVY, lw=1.5):
        ax.annotate('', xy=(x, y2), xytext=(x, y1),
                    arrowprops=dict(arrowstyle='->', color=color,
                                    lw=lw, mutation_scale=13),
                    zorder=4)

    cx = main_x + main_w / 2  # center x for arrows

    # ── Step 0 ────────────────────────────────────────────────────────────────
    s0 = 22.0
    step_box(s0,
             'Step 0 — Market Context Agent  (9:00 AM ET)',
             'VIX tiered gate: >45→2 pos · 30–45→3 · 25–30→5 · 20–25→10 · <20→15\n'
             'Futures gate: skip if avg < −1.5%  ·  Fear & Greed: confirming only\n'
             'Economic calendar: FOMC cap 8 · CPI/NFP cap 10  →  GO / CAUTION / SKIP + max_positions',
             fill=NAVY_LIGHT, edge=NAVY)
    arrow_down(cx, s0, s0 - step_gap)
    ax.annotate('', xy=(sdb_x, 21.1), xytext=(main_x + main_w, s0 + 0.4),
                arrowprops=dict(arrowstyle='->', color=NAVY, lw=1.1, mutation_scale=9), zorder=4)

    # ── Step 1 ────────────────────────────────────────────────────────────────
    s1 = s0 - step_total
    step_box(s1,
             'Step 1 — Dynamic Universe Loader + Scanner',
             'load_universe(): reads Supabase (≤7 days old) or falls back to static settings.py (429 tickers)\n'
             'yfinance + ta: RSI · MACD · Bollinger Bands · ATR · SMA 20/50  →  score each ticker −10→+10\n'
             'Returns candidates with score ≥ 3  ·  also computes: dist_sma20, dist_sma50, mom1, mom5',
             fill=NAVY_LIGHT, edge=NAVY)
    arrow_down(cx, s1, s1 - step_gap)

    # ── Step 1.5 ──────────────────────────────────────────────────────────────
    s15 = s1 - step_total
    step_box(s15,
             'Step 1.5 — News Intel Agent',
             'Earnings blackout: remove tickers with earnings today or day-before (binary event risk)\n'
             'News headlines: fetch 3 headlines per ticker from yfinance  ·  passed to Claude as context\n'
             'Logs every blocked ticker with reason for audit trail  ·  also checks pre-market gap (>+2% skip)',
             fill=NAVY_LIGHT, edge=NAVY)
    arrow_down(cx, s15, s15 - step_gap)

    # ── Step 1.75 ─────────────────────────────────────────────────────────────
    s175 = s15 - step_total
    step_box(s175,
             'Step 1.75 — Strategy Pre-Filter  (SHIPPED v5.3)',
             'Hard score gate: drops candidates with technical_score < 4  (was 5, tuned after 93→4 collapse)\n'
             'Sector concentration guard: max 2 candidates per sector  ·  respects max_positions from Step 0\n'
             'Log: "[ 1.75/4 ] Strategy pre-filter: X → Y candidates (score ≥ 4)"',
             fill=NAVY_LIGHT, edge=NAVY)
    arrow_down(cx, s175, s175 - step_gap)

    # ── Step 1.76 ─────────────────────────────────────────────────────────────
    s176 = s175 - step_total
    step_box(s176,
             'Step 1.76 — ML Scorer  (SHIPPED v5.6)',
             'HistGradientBoostingClassifier: P(next-day high ≥ close × 1.02)  ·  AUC 0.78 ± 0.04 (5-fold CV)\n'
             '13 features: rsi · macd_hist · bb_pct · vol_ratio · atr_pct · dist_sma20 · dist_sma50 · mom1\n'
             '             mom5 · range_52w_pct · dow · vix · technical_score  ·  top feature: atr_pct (0.165)\n'
             'Sorts candidates by ml_score descending before Claude call  ·  graceful fallback if model missing',
             fill=PURPLE_LIGHT, edge=PURPLE, title_color=PURPLE, detail_color=GRAY,
             bold_title=True)
    arrow_down(cx, s176, s176 - step_gap)
    ax.annotate('', xy=(sdb_x, 15.85), xytext=(main_x + main_w, s176 + 0.4),
                arrowprops=dict(arrowstyle='->', color=PURPLE, lw=1.1, mutation_scale=9), zorder=4)

    # ── Step 1.8 ──────────────────────────────────────────────────────────────
    s18 = s176 - step_total
    step_box(s18,
             'Step 1.8 — Live Price Refresh  (SHIPPED v5.3)',
             'Fetches real-time prices from Alpaca /v2/stocks/snapshots  ·  batch call for all candidates\n'
             'Updates candidate["current_price"] before passing to Claude  ·  replaces 15-min delayed yfinance\n'
             'Log: "[ 1.8/4 ] Live prices refreshed for X candidates"',
             fill=NAVY_LIGHT, edge=NAVY)
    arrow_down(cx, s18, s18 - step_gap)

    # ── Step 2 ────────────────────────────────────────────────────────────────
    s2 = s18 - step_total
    step_box(s2,
             'Step 2 — Strategy Agent  (Claude claude-sonnet-4-6)',
             'Input: candidates ranked by ml_score + market summary (VIX, futures, news, max_positions)\n'
             'Output JSON: ticker · action · entry · target (+2%) · stop (−0.67%) · confidence · reasoning\n'
             'Selects top max_positions setups  ·  can return zero trades if no high-conviction setups\n'
             'Prompt cache: system prompt cached (1,200+ tokens) — saves ~50% Claude cost on intraday calls',
             fill=ORANGE_LIGHT, edge=ORANGE, title_color=ORANGE, detail_color=GRAY)
    arrow_down(cx, s2, s2 - step_gap)

    # ── Step 3 ────────────────────────────────────────────────────────────────
    s3 = s2 - step_total
    step_box(s3,
             'Step 3 — Risk Agent  (Claude claude-sonnet-4-6)',
             'Hard rules: stop ≤ 0.67% · target = 2.0% · R:R ≥ 3.0 · position size $5K–$7K\n'
             'Confidence sizing: HIGH → $7K · MED → $6K · LOW → $5K  ·  break-even at 25% win rate (3:1 R:R)\n'
             'Float-safe checks: round(potential_loss, 4)+0.0001  ·  returns approved + rejected with reasons',
             fill=ORANGE_LIGHT, edge=ORANGE, title_color=ORANGE, detail_color=GRAY)
    arrow_down(cx, s3, s3 - step_gap)

    # ── Step 3.5 ──────────────────────────────────────────────────────────────
    s35 = s3 - step_total
    step_box(s35,
             'Step 3.5 — Sector Guard  (SHIPPED v5.3)',
             'Checks approved trades: max 2 positions per sector across entire portfolio\n'
             'Rejects the lower-confidence trade if sector already at cap  ·  prevents correlated drawdown\n'
             'Log: "[ 3.5/4 ] Sector guard: removed TICKER (SECTOR already at cap)"',
             fill=NAVY_LIGHT, edge=NAVY)
    arrow_down(cx, s35, s35 - step_gap)

    # ── Step 3.75 ─────────────────────────────────────────────────────────────
    s375 = s35 - step_total
    step_box(s375,
             'Step 3.75 — Guardrails Check  (SHIPPED v5.3)',
             'Daily loss limit: stop trading if daily P&L < −$1,000  ·  blocks new positions\n'
             'Concurrent run lock: prevents duplicate orchestrator runs from overlapping schedules\n'
             'Max daily trade cap: enforces max_positions from Step 0 as hard ceiling',
             fill=NAVY_LIGHT, edge=NAVY)
    arrow_down(cx, s375, s375 - step_gap)

    # ── Step 4 ────────────────────────────────────────────────────────────────
    s4 = s375 - step_total
    step_box(s4,
             'Step 4 — Portfolio Agent  (limit orders)',
             'simulation mode: writes positions to Supabase only  (yfinance prices)\n'
             'alpaca mode: submits bracket order — entry LIMIT (ask+0.1%) · take-profit LIMIT · stop-loss\n'
             '             stores alpaca_order_id in positions table → used for fill price tracking\n'
             '             trailing stop: manual high_watermark (15-min polling) — native OTO-OCO deferred',
             fill=GREEN_LIGHT, edge=GREEN, title_color=GREEN, detail_color=GRAY)
    arrow_down(cx, s4, s4 - step_gap)
    ax.annotate('', xy=(sdb_x, 12.35), xytext=(main_x + main_w, s4 + 0.4),
                arrowprops=dict(arrowstyle='->', color=GREEN, lw=1.1,
                                connectionstyle='arc3,rad=-0.15', mutation_scale=9), zorder=4)
    ax.annotate('', xy=(sdb_x, 11.15), xytext=(main_x + main_w, s4 + 0.25),
                arrowprops=dict(arrowstyle='->', color=NAVY, lw=1.1,
                                connectionstyle='arc3,rad=-0.2', mutation_scale=9), zorder=4)

    # ── Intraday ──────────────────────────────────────────────────────────────
    s5 = s4 - step_total
    step_box(s5,
             'Intraday Agent  (every 15 min, 9:45 AM – 3:30 PM ET)',
             'simulation mode: fetches yfinance prices · calculates unrealized P&L · checks trailing stop\n'
             'alpaca mode: calls get_position_data() from Alpaca API  ·  syncs price and P&L to Supabase\n'
             '            if position gone from Alpaca → fetches fill price from bracket order leg\n'
             'Trailing stop (simulation): high_watermark + TRAIL_PCT=0.012 check every 15 min',
             fill=GREEN_LIGHT, edge=GREEN, title_color=GREEN, detail_color=GRAY)
    arrow_down(cx, s5, s5 - step_gap)
    ax.annotate('', xy=(sdb_x, 11.15), xytext=(main_x + main_w, s5 + 0.5),
                arrowprops=dict(arrowstyle='->', color=NAVY, lw=1.1,
                                connectionstyle='arc3,rad=-0.15', mutation_scale=9), zorder=4)
    ax.annotate('', xy=(sdb_x, 9.85), xytext=(main_x + main_w, s5 + 0.3),
                arrowprops=dict(arrowstyle='->', color=ORANGE, lw=1.1,
                                connectionstyle='arc3,rad=-0.15', mutation_scale=9), zorder=4)

    # ── EOD ───────────────────────────────────────────────────────────────────
    s6 = s5 - step_total
    step_box(s6,
             'EOD Agent  (4:30 PM ET)',
             'Force-closes all remaining open positions\n'
             'simulation mode: uses yfinance close price  ·  alpaca mode: calls close_position() → actual fill\n'
             'Calculates total P&L · win rate · best/worst trade  →  writes daily_performance to Supabase\n'
             'eval.py (post-run): python3 eval.py --days N  →  filtered by trading dates only',
             fill=GREEN_LIGHT, edge=GREEN, title_color=GREEN, detail_color=GRAY)
    ax.annotate('', xy=(sdb_x, 8.65), xytext=(main_x + main_w, s6 + 0.4),
                arrowprops=dict(arrowstyle='->', color=GREEN, lw=1.1,
                                connectionstyle='arc3,rad=-0.15', mutation_scale=9), zorder=4)

    # ── Broker abstraction note ───────────────────────────────────────────────
    note_y = s6 - 1.5
    rounded_box(ax, main_x, note_y, main_w, 0.8,
                fill='#FDFEFE', edge=GRAY, text='',
                radius=0.015, linestyle='dashed', linewidth=1.2, zorder=2)
    ax.text(main_x + 0.18, note_y + 0.62,
            'Broker Abstraction  —  orchestrator.py --broker simulation|alpaca',
            ha='left', va='top', fontsize=8, color=NAVY, fontweight='bold', zorder=3)
    ax.text(main_x + 0.18, note_y + 0.1,
            'simulation (default local): yfinance prices, Supabase only  ·  '
            'alpaca (GitHub Actions default): live limit orders + Alpaca fills',
            ha='left', va='bottom', fontsize=7, color=GRAY, zorder=3)

    # ── cron-job.org schedule ─────────────────────────────────────────────────
    cron_y = note_y - 1.1
    rounded_box(ax, main_x, cron_y, main_w, 0.9,
                fill=NAVY_LIGHT, edge=NAVY, text='',
                radius=0.018, zorder=2)
    ax.text(main_x + 0.18, cron_y + 0.73, 'cron-job.org External Triggers + GitHub Actions Schedule',
            ha='left', va='top', fontsize=8, color=NAVY, fontweight='bold', zorder=3)
    schedule_lines = (
        'Premarket: 9:00 AM ET (13:00 UTC) Mon–Fri  ·  '
        'Intraday: every 15 min 9:45 AM–3:30 PM ET  ·  '
        'EOD: 4:30 PM ET\n'
        'Universe Refresh: Monday 8:30 AM ET  ·  '
        'ML Retrain: 1st of month 10:00 AM UTC (monthly, commits model back to repo)'
    )
    ax.text(main_x + 0.18, cron_y + 0.1, schedule_lines,
            ha='left', va='bottom', fontsize=7, color=GRAY, zorder=3)

    # ── ML Retrain feedback loop ──────────────────────────────────────────────
    ml_y = cron_y - 1.1
    rounded_box(ax, main_x, ml_y, main_w, 0.9,
                fill=PURPLE_LIGHT, edge=PURPLE, text='',
                radius=0.018, zorder=2)
    ax.text(main_x + 0.18, ml_y + 0.73,
            'ML Model Retraining Loop  (retrain_model.yml)',
            ha='left', va='top', fontsize=8, color=PURPLE, fontweight='bold', zorder=3)
    ax.text(main_x + 0.18, ml_y + 0.1,
            'GitHub Actions: triggers 1st of month · downloads 2y of price data for all 429 tickers · '
            'retrains HistGradientBoostingClassifier\n'
            'Commits updated xgb_scorer.pkl + feature_columns.json back to main branch · '
            'no manual step required · SUPABASE_URL/KEY via Secrets',
            ha='left', va='bottom', fontsize=7, color=GRAY, zorder=3)

    # ── Secrets ───────────────────────────────────────────────────────────────
    sec_y = ml_y - 1.1
    rounded_box(ax, main_x, sec_y, main_w, 0.8,
                fill=RED_LIGHT, edge=RED_EDGE, text='',
                radius=0.018, zorder=2)
    ax.text(main_x + 0.18, sec_y + 0.62,
            'Secrets  (GitHub Secrets + .env, never in code)',
            ha='left', va='top', fontsize=8, color=RED_EDGE, fontweight='bold', zorder=3)
    ax.text(main_x + 0.18, sec_y + 0.1,
            'ANTHROPIC_API_KEY  ·  SUPABASE_URL  ·  SUPABASE_KEY  ·  '
            'ALPACA_API_KEY  ·  ALPACA_SECRET_KEY  ·  DASHBOARD_PASSWORD  ·  PAT (repo+workflow)',
            ha='left', va='bottom', fontsize=7, color=GRAY, zorder=3)

    # ── Tech stack ────────────────────────────────────────────────────────────
    ts_y = sec_y - 1.0
    rounded_box(ax, main_x, ts_y, main_w, 0.8,
                fill=GRAY_LIGHT, edge=GRAY, text='',
                radius=0.018, zorder=2)
    ax.text(main_x + 0.18, ts_y + 0.62,
            'Tech Stack',
            ha='left', va='top', fontsize=8, color=NAVY, fontweight='bold', zorder=3)
    ax.text(main_x + 0.18, ts_y + 0.1,
            'Python 3.11  ·  Anthropic SDK (claude-sonnet-4-6)  ·  scikit-learn (HistGradientBoosting)  ·  '
            'yfinance  ·  ta  ·  alpaca-py ≥ 0.20  ·  Supabase  ·  Streamlit  ·  GitHub Actions  ·  joblib',
            ha='left', va='bottom', fontsize=7, color=GRAY, zorder=3)

    # ── Footer ────────────────────────────────────────────────────────────────
    ax.text(5.5, 0.3, 'AI Trading Agent v5.6  ·  Amit Garg  ·  May 2026',
            ha='center', va='center', fontsize=8, color=GRAY, style='italic')

    plt.tight_layout(pad=0.4)
    out = f"{PROJECT_DIR}/architecture_low_level.png"
    fig.savefig(out, dpi=150, bbox_inches='tight', facecolor=WHITE)
    plt.close(fig)
    print(f"Saved: {out}")


if __name__ == '__main__':
    make_high_level()
    make_low_level()
    print("Architecture diagrams generated successfully.")
