"""Generates two architecture diagram PNGs for the AI Trading Agent project."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import matplotlib.patheffects as pe

# Color palette
NAVY       = '#1A3A6A'
ORANGE     = '#F47B20'
GREEN      = '#27AE60'
NAVY_LIGHT = '#D6E4F7'
ORANGE_LIGHT = '#FDEBD0'
GREEN_LIGHT  = '#D5F5E3'
GRAY       = '#6C7A89'
GRAY_LIGHT = '#F0F3F4'
WHITE      = '#FFFFFF'
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
# Diagram 1: High-Level Architecture  (16 × 9 inches)
# ─────────────────────────────────────────────────────────────────────────────

def make_high_level():
    fig, ax = plt.subplots(figsize=(16, 9))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 9)
    ax.axis('off')
    fig.patch.set_facecolor(WHITE)

    # ── Title ─────────────────────────────────────────────────────────────────
    ax.text(8, 8.7, 'AI Trading Agent — High-Level Architecture',
            ha='center', va='center', fontsize=16,
            color=NAVY, fontweight='bold')

    # ── Top row: Triggers ─────────────────────────────────────────────────────
    trigger_y = 8.0
    trigger_h = 0.45
    triggers = [
        (0.3,  4.5,  'GitHub Actions\nCron Schedule'),
        (5.2,  4.5,  'GitHub Actions\nManual Dispatch'),
        (10.1, 4.5,  'Universe Refresh\nMonday 8:30 AM ET'),
    ]
    for tx, tw, label in triggers:
        rounded_box(ax, tx, trigger_y, tw, trigger_h,
                    fill=NAVY_LIGHT, edge=NAVY, text=label,
                    fontsize=8, text_color=NAVY, radius=0.015)

    # Arrows from triggers to orchestrator
    orch_top = 7.25
    arrow(ax, 2.55,  trigger_y,         2.55,  orch_top + 0.35, color=NAVY)
    arrow(ax, 7.45,  trigger_y,         7.45,  orch_top + 0.35, color=NAVY)
    arrow(ax, 12.35, trigger_y,         12.35, orch_top + 0.35, color=NAVY)

    # ── Orchestrator ──────────────────────────────────────────────────────────
    orch_x, orch_y, orch_w, orch_h = 0.3, 6.85, 14.6, 0.55
    rounded_box(ax, orch_x, orch_y, orch_w, orch_h,
                fill=ORANGE, edge=ORANGE, text='orchestrator.py   —   Master Controller',
                fontsize=11, text_color=WHITE, bold=True, radius=0.02)

    # Arrow from orchestrator to pipeline
    arrow(ax, 7.6, orch_y, 7.6, 6.65, color=NAVY)

    # ── Agent Pipeline dashed container ──────────────────────────────────────
    pipe_x, pipe_y, pipe_w, pipe_h = 0.3, 2.85, 11.5, 3.7
    pipe_box = FancyBboxPatch((pipe_x, pipe_y), pipe_w, pipe_h,
                              boxstyle="round,pad=0.03",
                              linewidth=1.8, edgecolor=DASHED_BORDER,
                              facecolor=GRAY_LIGHT, linestyle='dashed', zorder=1)
    ax.add_patch(pipe_box)
    ax.text(pipe_x + 0.15, pipe_y + pipe_h - 0.12, 'Agent Pipeline',
            ha='left', va='top', fontsize=9, color=DASHED_BORDER, style='italic')

    # Row heights inside pipeline
    row1_y = 5.9   # Pre-market agents
    row2_y = 4.75  # Claude agents + Portfolio
    row3_y = 3.55  # Intraday / EOD

    box_h = 0.55

    # Row 1: Pre-market
    pre_agents = [
        (0.55,  2.4, 'Market Context\nAgent'),
        (3.15,  2.4, 'Scanner\n(458 tickers)'),
        (5.75,  2.4, 'News Intel\nAgent'),
        (8.35,  2.4, 'Universe\nLoader'),
    ]
    for bx, bw, label in pre_agents:
        rounded_box(ax, bx, row1_y, bw, box_h,
                    fill=NAVY, edge=NAVY, text=label,
                    fontsize=8, text_color=WHITE, radius=0.015)

    # Arrows between pre-market agents
    for i in range(len(pre_agents) - 1):
        x1 = pre_agents[i][0] + pre_agents[i][1]
        x2 = pre_agents[i + 1][0]
        mid_y = row1_y + box_h / 2
        arrow(ax, x1, mid_y, x2, mid_y, color=NAVY)

    # Row 2: Claude + Portfolio
    claude_agents = [
        (0.55,  2.65, 'Strategy Agent\n(Claude)', ORANGE),
        (3.4,   2.65, 'Risk Agent\n(Claude)',     ORANGE),
        (6.25,  3.5,  'Portfolio Agent',           GREEN),
    ]
    for bx, bw, label, col in claude_agents:
        fc = col
        tc = WHITE
        rounded_box(ax, bx, row2_y, bw, box_h,
                    fill=fc, edge=fc, text=label,
                    fontsize=8, text_color=tc, radius=0.015)

    for i in range(len(claude_agents) - 1):
        x1 = claude_agents[i][0] + claude_agents[i][1]
        x2 = claude_agents[i + 1][0]
        mid_y = row2_y + box_h / 2
        arrow(ax, x1, mid_y, x2, mid_y, color=NAVY)

    # Row 3: Intraday / EOD
    exec_agents = [
        (0.55,  4.5, 'Intraday Agent\n(every 30 min)', GREEN),
        (5.25,  4.5, 'EOD Agent\n(4:30 PM ET)',         GREEN),
    ]
    for bx, bw, label, col in exec_agents:
        rounded_box(ax, bx, row3_y, bw, box_h,
                    fill=col, edge=col, text=label,
                    fontsize=8, text_color=WHITE, radius=0.015)

    # Vertical arrow from row1 to row2 (via market context start)
    arrow(ax, 1.75, row1_y, 1.75, row2_y + box_h, color=NAVY)
    # Vertical arrow from row2 portfolio to row3 intraday
    arrow(ax, 2.8, row2_y, 2.8, row3_y + box_h, color=NAVY)

    # ── Bottom row: Data stores ───────────────────────────────────────────────
    ds_y = 1.8
    ds_h = 0.55
    data_stores = [
        (0.3,  4.3,  'Supabase\nPostgreSQL DB',    NAVY),
        (4.85, 4.3,  'Alpaca Paper\nTrading',       NAVY),
        (9.4,  4.3,  'Streamlit\nDashboard',        NAVY),
    ]
    for dx, dw, label, col in data_stores:
        rounded_box(ax, dx, ds_y, dw, ds_h,
                    fill=col, edge=col, text=label,
                    fontsize=8, text_color=WHITE, radius=0.015)

    # Arrows from pipeline bottom to data stores
    arrow(ax, 2.45,  pipe_y, 2.45,  ds_y + ds_h,   color=NAVY)
    arrow(ax, 7.0,   pipe_y, 7.05,  ds_y + ds_h,   color=NAVY)
    arrow(ax, 11.55, pipe_y, 11.55, ds_y + ds_h,   color=NAVY)

    # ── Right sidebar: External services ─────────────────────────────────────
    ext_x  = 12.15
    ext_w  = 3.5
    ext_h  = 0.5
    ext_items = [
        (6.55, 'Anthropic Claude API', ORANGE),
        (5.85, 'yfinance (free)',       GRAY),
        (5.15, 'alternative.me\nFear & Greed', GRAY),
        (4.4,  'Alpaca API\nPaper Trading',    GRAY),
    ]
    ax.text(ext_x + ext_w / 2, 7.1, 'External Services',
            ha='center', va='center', fontsize=9,
            color=NAVY, fontweight='bold')
    for ey, label, col in ext_items:
        fc = col
        tc = WHITE if col != GRAY else WHITE
        rounded_box(ax, ext_x, ey, ext_w, ext_h,
                    fill=fc, edge=fc, text=label,
                    fontsize=8, text_color=WHITE, radius=0.015)

    # ── Footer ────────────────────────────────────────────────────────────────
    ax.text(8, 0.15, 'AI Trading Agent v4.0  ·  Amit Garg  ·  May 2026',
            ha='center', va='center', fontsize=8, color=GRAY, style='italic')

    plt.tight_layout(pad=0.3)
    out = f"{PROJECT_DIR}/architecture_high_level.png"
    fig.savefig(out, dpi=150, bbox_inches='tight', facecolor=WHITE)
    plt.close(fig)
    print(f"Saved: {out}")


# ─────────────────────────────────────────────────────────────────────────────
# Diagram 2: Low-Level Pipeline  (11 × 18 inches, vertical)
# ─────────────────────────────────────────────────────────────────────────────

def make_low_level():
    fig, ax = plt.subplots(figsize=(11, 18))
    ax.set_xlim(0, 11)
    ax.set_ylim(0, 18)
    ax.axis('off')
    fig.patch.set_facecolor(WHITE)

    # Title
    ax.text(5.5, 17.6, 'AI Trading Agent — Detailed Pipeline (Low-Level)',
            ha='center', va='center', fontsize=15,
            color=NAVY, fontweight='bold')
    ax.text(5.5, 17.25, 'Premarket → Intraday → EOD',
            ha='center', va='center', fontsize=10, color=GRAY, style='italic')

    # ── Right sidebar: Data stores ────────────────────────────────────────────
    sdb_x  = 8.1
    sdb_w  = 2.7

    def sidebar_box(y, h, label, fill=NAVY_LIGHT, edge=NAVY, fontsize=8, tc=NAVY):
        rounded_box(ax, sdb_x, y, sdb_w, h,
                    fill=fill, edge=edge, text=label,
                    fontsize=fontsize, text_color=tc, radius=0.015, zorder=2)

    sidebar_box(15.3, 0.55, 'Supabase Tables:\ntrade_plans, planned_trades\npositions, daily_performance\nscan_results',
                fontsize=7.5)
    sidebar_box(13.8, 0.45, 'Alpaca Paper\nTrading Account',
                fill=ORANGE_LIGHT, edge=ORANGE, tc=NAVY)
    sidebar_box(11.5, 0.45, 'Supabase:\npositions (open)',
                fontsize=7.5)
    sidebar_box(10.1, 0.45, 'Alpaca API:\nlive positions',
                fill=ORANGE_LIGHT, edge=ORANGE, tc=NAVY, fontsize=7.5)
    sidebar_box(8.5, 0.45, 'Supabase:\npositions (closed)\ndaily_performance',
                fill=GREEN_LIGHT, edge=GREEN, tc=NAVY, fontsize=7.5)

    # ── Main pipeline boxes ───────────────────────────────────────────────────
    main_x = 0.3
    main_w = 7.5
    box_h  = 0.95

    def step_box(y, title, detail, fill=NAVY_LIGHT, edge=NAVY,
                 title_color=NAVY, detail_color=GRAY, bold_title=True):
        rounded_box(ax, main_x, y, main_w, box_h,
                    fill=fill, edge=edge, text='',
                    radius=0.018, zorder=2)
        # Title
        ax.text(main_x + 0.18, y + box_h - 0.18, title,
                ha='left', va='top', fontsize=9,
                color=title_color, fontweight='bold' if bold_title else 'normal',
                zorder=3)
        # Detail (smaller)
        ax.text(main_x + 0.18, y + 0.12, detail,
                ha='left', va='bottom', fontsize=7.5,
                color=detail_color, zorder=3, wrap=False)

    def arrow_down(x, y1, y2, color=NAVY, lw=1.5):
        ax.annotate('', xy=(x, y2), xytext=(x, y1),
                    arrowprops=dict(arrowstyle='->', color=color,
                                    lw=lw, mutation_scale=13),
                    zorder=4)

    # ── Step 0 ────────────────────────────────────────────────────────────────
    s0_y = 15.9
    step_box(s0_y,
             'Step 0 — Market Context Agent  (9:00 AM ET)',
             'VIX tiered gate: >45→2 pos · 30-45→3 pos · 25-30→5 pos · 20-25→10 pos · <20→15 pos\n'
             'Futures gate: skip if avg < -1.5%  ·  Fear & Greed: confirming only\n'
             'Economic calendar: FOMC cap 8 · CPI/NFP cap 10  →  returns GO/CAUTION/SKIP + max_positions',
             fill=NAVY_LIGHT, edge=NAVY)
    arrow_down(4.05, s0_y, s0_y - 0.35)
    # Sidebar arrow
    ax.annotate('', xy=(sdb_x, 15.55), xytext=(main_x + main_w, 15.7),
                arrowprops=dict(arrowstyle='->', color=NAVY, lw=1.2, mutation_scale=10), zorder=4)

    # ── Step 1 ────────────────────────────────────────────────────────────────
    s1_y = 14.6
    step_box(s1_y,
             'Step 1 — Dynamic Universe Loader + Scanner',
             'load_universe(): reads Supabase (≤7 days old) or falls back to static settings.py\n'
             'yfinance + ta: RSI · MACD · Bollinger Bands · ATR · SMA 20/50  —  score each ticker -10→+10\n'
             'Returns candidates with score ≥ 3  (458 active universe tickers)',
             fill=NAVY_LIGHT, edge=NAVY)
    arrow_down(4.05, s1_y, s1_y - 0.35)

    # ── Step 1.5 ──────────────────────────────────────────────────────────────
    s15_y = 13.3
    step_box(s15_y,
             'Step 1.5 — News Intel Agent',
             'Earnings blackout: remove tickers with earnings today or day-before (binary event risk)\n'
             'News headlines: fetch 3 headlines per remaining ticker from yfinance · passed to Claude\n'
             'Logs blocked tickers with reason for audit trail',
             fill=NAVY_LIGHT, edge=NAVY)
    arrow_down(4.05, s15_y, s15_y - 0.35)

    # ── Step 2 ────────────────────────────────────────────────────────────────
    s2_y = 12.0
    step_box(s2_y,
             'Step 2 — Strategy Agent  (Claude claude-sonnet-4-6)',
             'Input: scored candidates + market summary (VIX, futures, news, max_positions)\n'
             'Output JSON: ticker · action · entry · target (+3%) · stop (-1%) · confidence · reasoning\n'
             'Selects top max_positions setups; can return zero trades if no high-conviction setups',
             fill=ORANGE_LIGHT, edge=ORANGE, title_color=ORANGE, detail_color=GRAY)
    arrow_down(4.05, s2_y, s2_y - 0.35)

    # ── Step 3 ────────────────────────────────────────────────────────────────
    s3_y = 10.7
    step_box(s3_y,
             'Step 3 — Risk Agent  (Claude claude-sonnet-4-6)',
             'Hard rules: stop ≤ 1% · target = 3% · R:R ≥ 3.0 · position $5K–$7K\n'
             'Float-safe checks: round(potential_loss, 4)+0.0001 · round(rr, 2) for R:R\n'
             'Returns approved trades + rejected trades with 2dp rejection reasons',
             fill=ORANGE_LIGHT, edge=ORANGE, title_color=ORANGE, detail_color=GRAY)
    arrow_down(4.05, s3_y, s3_y - 0.35)

    # ── Step 4 ────────────────────────────────────────────────────────────────
    s4_y = 9.4
    step_box(s4_y,
             'Step 4 — Portfolio Agent',
             'simulation mode: writes positions to Supabase only  (yfinance prices)\n'
             'alpaca mode: submits bracket order (entry market + take-profit limit + stop-loss) to Alpaca\n'
             '            stores alpaca_order_id in positions table → used for fill price tracking',
             fill=GREEN_LIGHT, edge=GREEN, title_color=GREEN, detail_color=GRAY)
    arrow_down(4.05, s4_y, s4_y - 0.35)
    # Sidebar arrow
    ax.annotate('', xy=(sdb_x, 13.85), xytext=(main_x + main_w, 9.9),
                arrowprops=dict(arrowstyle='->', color=GREEN, lw=1.2,
                                connectionstyle='arc3,rad=-0.15', mutation_scale=10), zorder=4)

    # ── Intraday ──────────────────────────────────────────────────────────────
    s5_y = 8.1
    step_box(s5_y,
             'Intraday Agent  (every 30 min, 10:00 AM – 3:30 PM ET)',
             'simulation mode: fetches yfinance prices · calculates unrealized P&L\n'
             'alpaca mode: calls get_position_data() from Alpaca API · syncs price and P&L to Supabase\n'
             '            if position gone from Alpaca → fetches fill price from bracket order leg',
             fill=GREEN_LIGHT, edge=GREEN, title_color=GREEN, detail_color=GRAY)
    arrow_down(4.05, s5_y, s5_y - 0.35)
    # Sidebar arrows
    ax.annotate('', xy=(sdb_x, 11.6), xytext=(main_x + main_w, 8.55),
                arrowprops=dict(arrowstyle='->', color=NAVY, lw=1.2,
                                connectionstyle='arc3,rad=-0.15', mutation_scale=10), zorder=4)
    ax.annotate('', xy=(sdb_x, 10.25), xytext=(main_x + main_w, 8.35),
                arrowprops=dict(arrowstyle='->', color=ORANGE, lw=1.2,
                                connectionstyle='arc3,rad=-0.15', mutation_scale=10), zorder=4)

    # ── EOD ───────────────────────────────────────────────────────────────────
    s6_y = 6.8
    step_box(s6_y,
             'EOD Agent  (4:30 PM ET)',
             'Force-closes remaining open positions\n'
             'simulation mode: uses yfinance close price · alpaca mode: calls close_position() → actual fill price\n'
             'Calculates total P&L, win rate, best/worst trade → writes daily_performance to Supabase',
             fill=GREEN_LIGHT, edge=GREEN, title_color=GREEN, detail_color=GRAY)
    # Sidebar arrow
    ax.annotate('', xy=(sdb_x, 8.65), xytext=(main_x + main_w, 7.25),
                arrowprops=dict(arrowstyle='->', color=GREEN, lw=1.2,
                                connectionstyle='arc3,rad=-0.15', mutation_scale=10), zorder=4)

    # ── Broker abstraction note ───────────────────────────────────────────────
    note_y = 5.6
    rounded_box(ax, main_x, note_y, main_w, 0.9,
                fill='#FDFEFE', edge=GRAY, text='',
                radius=0.015, linestyle='dashed', linewidth=1.2, zorder=2)
    ax.text(main_x + 0.18, note_y + 0.7,
            'Broker Abstraction  —  orchestrator.py --broker simulation|alpaca',
            ha='left', va='top', fontsize=8.5, color=NAVY, fontweight='bold', zorder=3)
    ax.text(main_x + 0.18, note_y + 0.15,
            'simulation (default local): yfinance prices, Supabase only  ·  '
            'alpaca (default GitHub Actions): real bracket orders + Alpaca fills',
            ha='left', va='bottom', fontsize=7.5, color=GRAY, zorder=3)

    # ── GitHub Actions schedule ───────────────────────────────────────────────
    ga_y = 4.5
    rounded_box(ax, main_x, ga_y, main_w, 0.9,
                fill=NAVY_LIGHT, edge=NAVY, text='',
                radius=0.018, zorder=2)
    ax.text(main_x + 0.18, ga_y + 0.72, 'GitHub Actions Schedule',
            ha='left', va='top', fontsize=8.5, color=NAVY, fontweight='bold', zorder=3)
    schedule_lines = (
        'Universe Refresh: Monday 8:30 AM ET (12:30 UTC)  ·  '
        'Premarket: 9:00 AM Mon–Fri (13:00 UTC)\n'
        'Intraday: every 30 min 10AM–3:30PM (14:00–19:30 UTC)  ·  '
        'EOD: 4:30 PM Mon–Fri (20:30 UTC)'
    )
    ax.text(main_x + 0.18, ga_y + 0.12, schedule_lines,
            ha='left', va='bottom', fontsize=7.5, color=GRAY, zorder=3)

    # ── Secrets ───────────────────────────────────────────────────────────────
    sec_y = 3.3
    rounded_box(ax, main_x, sec_y, main_w, 0.9,
                fill='#F9EBEA', edge='#C0392B', text='',
                radius=0.018, zorder=2)
    ax.text(main_x + 0.18, sec_y + 0.72,
            'Secrets  (GitHub Secrets + .env, never in code)',
            ha='left', va='top', fontsize=8.5, color='#C0392B', fontweight='bold', zorder=3)
    ax.text(main_x + 0.18, sec_y + 0.12,
            'ANTHROPIC_API_KEY  ·  SUPABASE_URL  ·  SUPABASE_KEY  ·  '
            'ALPACA_API_KEY  ·  ALPACA_SECRET_KEY  ·  DASHBOARD_PASSWORD',
            ha='left', va='bottom', fontsize=7.5, color=GRAY, zorder=3)

    # ── Tech stack ────────────────────────────────────────────────────────────
    ts_y = 2.1
    rounded_box(ax, main_x, ts_y, main_w, 0.9,
                fill=GRAY_LIGHT, edge=GRAY, text='',
                radius=0.018, zorder=2)
    ax.text(main_x + 0.18, ts_y + 0.72,
            'Tech Stack',
            ha='left', va='top', fontsize=8.5, color=NAVY, fontweight='bold', zorder=3)
    ax.text(main_x + 0.18, ts_y + 0.12,
            'Python 3.11  ·  Anthropic SDK (claude-sonnet-4-6)  ·  yfinance  ·  ta  ·  '
            'alpaca-py ≥ 0.20  ·  Supabase  ·  Streamlit  ·  GitHub Actions',
            ha='left', va='bottom', fontsize=7.5, color=GRAY, zorder=3)

    # ── Footer ────────────────────────────────────────────────────────────────
    ax.text(5.5, 0.25, 'AI Trading Agent v4.0  ·  Amit Garg  ·  May 2026',
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
