#!/usr/bin/env python3
"""
Generate workflow_diagram.png for Trading Agent A using Graphviz.
Run: python3 generate_workflow.py
Deps: pip install graphviz  +  brew install graphviz
"""
from graphviz import Digraph

# ── Colour palette ───────────────────────────────────────────────────────────
C_AGENT     = '#2b5aa0'   # blue — agent / process boxes
C_AGENT_DRK = '#1a3a5c'   # dark navy — portfolio / orchestration
C_GATE      = '#b03020'   # red — decision diamonds
C_EOD       = '#1e7e42'   # green — EOD boxes
C_INFRA     = '#b55a1a'   # orange — infrastructure
C_DB        = '#2c3e50'   # dark — DB tables
C_WHITE     = 'white'
C_EDGE      = '#444444'

BG_PM   = '#dde8f5'   # section backgrounds
BG_ID   = '#ddf0e6'
BG_EOD  = '#fef9e7'
BG_INF  = '#fde9d9'


def lbl(title: str, sub: str = '', tsz: int = 11, ssz: int = 9) -> str:
    """Return a Graphviz HTML label (title bold + optional subtitle)."""
    if sub:
        return (f'<<FONT POINT-SIZE="{tsz}"><B>{title}</B></FONT>'
                f'<BR/><FONT POINT-SIZE="{ssz}">{sub}</FONT>>')
    return f'<<FONT POINT-SIZE="{tsz}"><B>{title}</B></FONT>>'


def gate(g: Digraph, node_id: str, title: str, sub: str = '') -> None:
    g.node(node_id, lbl(title, sub, 10, 8), shape='diamond',
           fillcolor=C_GATE, fontcolor=C_WHITE, width='1.6', height='1.05')


def agent(g: Digraph, node_id: str, title: str, sub: str = '',
          color: str = C_AGENT) -> None:
    g.node(node_id, lbl(title, sub), shape='box',
           style='rounded,filled', fillcolor=color, fontcolor=C_WHITE)


def skip_node(g: Digraph, node_id: str, label: str = 'skip') -> None:
    g.node(node_id, label, shape='ellipse', style='filled,dashed',
           fillcolor='#f8d7da', fontcolor='#721c24', color=C_GATE, fontsize='9')


def ok_node(g: Digraph, node_id: str, label: str) -> None:
    g.node(node_id, label, shape='ellipse', style='filled',
           fillcolor='#d4edda', fontcolor='#155724', fontsize='9')


def no_edge(g: Digraph, src: str, dst: str, label: str = '') -> None:
    """Dashed red 'NO / skip' edge."""
    g.edge(src, dst, label=label, style='dashed',
           color=C_GATE, fontcolor=C_GATE, fontsize='8')


# ── Build graph ──────────────────────────────────────────────────────────────
dot = Digraph('workflow', format='png')
dot.attr(
    rankdir='TB',
    size='22,48',
    dpi='120',
    fontname='Helvetica',
    nodesep='0.5',
    ranksep='0.65',
    bgcolor='white',
    pad='0.7',
)
dot.attr('node', fontname='Helvetica', style='filled',
         margin='0.22,0.13', fontsize='10')
dot.attr('edge', fontname='Helvetica', fontsize='8',
         color=C_EDGE, fontcolor='#333333', arrowsize='0.8')

# ── Page title ───────────────────────────────────────────────────────────────
dot.node('_title',
    lbl('Trading Agent A — Full Day Agent Workflow',
        'Broad Universe Strategy · All agents, gates, conditions, and data flow',
        18, 11),
    shape='rectangle', style='filled', fillcolor='white',
    fontcolor='#1a3a5c', color='white', width='12')


# ════════════════════════════════════════════════════════════════════════════
# ①  PREMARKET
# ════════════════════════════════════════════════════════════════════════════
with dot.subgraph(name='cluster_premarket') as s:
    s.attr(
        label='① PREMARKET — 10:00 AM ET  (delayed from 9:00 AM — spreads stabilize)',
        style='filled', fillcolor=BG_PM, color='#6a8ab0',
        fontsize='12', fontcolor='#2c4a6e', fontname='Helvetica-Bold',
        labeljust='l',
    )

    # 3 system gates — same rank (side-by-side)
    with s.subgraph() as r:
        r.attr(rank='same')
        gate(r, 'g_td', 'Trading Day?', 'weekend / holiday')
        gate(r, 'g_halt', 'Halted today?', 'manual / circuit breaker')
        gate(r, 'g_dupe', 'Dupe run?', 'scan_results already today')

    skip_node(s, 'exit_pm', 'exit premarket')

    s.edge('g_td',   'g_halt',   label='pass', fontsize='8')
    s.edge('g_halt', 'g_dupe',   label='not halted', fontsize='8')
    no_edge(s, 'g_td',   'exit_pm', 'NO → skip')
    no_edge(s, 'g_halt', 'exit_pm', 'YES → skip')
    no_edge(s, 'g_dupe', 'exit_pm', 'YES → skip')

    # Market Context
    agent(s, 'mkt_ctx', 'Market Context',
          'VIX · Fear &amp; Greed · US Futures · Intl Markets · Economic Calendar · 11 Sector ETFs')
    s.edge('g_dupe', 'mkt_ctx', label='not dupe', fontsize='8')

    # Futures gate
    gate(s, 'g_fut', 'Futures &lt; -1.5%?')
    s.edge('mkt_ctx', 'g_fut')
    no_edge(s, 'g_fut', 'exit_pm', 'YES → no trades today')

    # Scanner
    agent(s, 'scanner', 'Scanner — 600+ tickers',
          'RSI · MACD · Bollinger Bands · Vol Ratio · SMA20/50 trend'
          ' · Breakout freshness · ORB · VWAP (5-min bars)')
    s.edge('g_fut', 'scanner',
           label='NO ↓  capital gate: avail_capital caps max_positions', fontsize='8')

    # News Intel + Pre-filter — side by side
    with s.subgraph() as r:
        r.attr(rank='same')
        agent(r, 'news_intel', 'News Intel',
              'Earnings blackout (today + tomorrow) · news context')
        agent(r, 'prefilter', 'Strategy Pre-filter',
              'score ≥ PREMARKET_MIN_SCORE (5) · drops ~60% tokens')

    s.edge('scanner', 'news_intel')
    s.edge('scanner', 'prefilter')

    # ML Scorer
    agent(s, 'ml_scorer', 'ML Scorer  (if model available)',
          'Predicts if stock hits +2% intraday · re-ranks by ml_score · gracefully skipped if no model')
    s.edge('news_intel', 'ml_scorer')
    s.edge('prefilter',  'ml_scorer')

    # Live Price Refresh
    agent(s, 'live_price', 'Live Price Refresh + Intraday Signals  (Alpaca mode only)',
          'ask quotes · above_vwap · vwap · rs_vs_spy · today_pct_change · sector ETF signals')
    s.edge('ml_scorer', 'live_price')

    # Claude Strategy Agent (premarket)
    agent(s, 'claude_pm', 'Claude Strategy Agent  (claude-sonnet-4-6)',
          'Selects HIGH / MEDIUM / LOW confidence trades · sector conviction injected'
          ' · quiet_day flag → tighter targets',
          C_AGENT_DRK)
    s.edge('live_price', 'claude_pm')

    # 4-up agent row
    with s.subgraph() as r:
        r.attr(rank='same')
        agent(r, 'risk_pm',   'Risk Agent',
              'R:R ≥ 2.0 · size 2.5K–3.5K · loss limit · stop width OK')
        agent(r, 'sector_pm', 'Sector Guard',
              'MAX_PER_SECTOR cap · blocks concentration')
        agent(r, 'atr_pm',    'ATR Sizer',
              'stop = ATR×0.8 · shares ≤ $150/mk · drops trade if R:R &lt; 1')
        agent(r, 'grails_pm', 'Guardrails',
              'Duplicates · Price sanity · Loss limit · Capital cap')

    s.edge('claude_pm', 'risk_pm')
    s.edge('claude_pm', 'sector_pm')
    s.edge('claude_pm', 'atr_pm')
    s.edge('claude_pm', 'grails_pm')

    # Portfolio → Alpaca
    agent(s, 'portfolio_pm', 'Portfolio Agent → Alpaca Bracket Orders',
          'Leg A (+1% partial, 50% shares) · Leg B (+4% ceiling, 50% shares)'
          ' · ATR stop both legs · tagged strata_{ticker}_{ts}',
          C_AGENT_DRK)
    s.edge('risk_pm',   'portfolio_pm')
    s.edge('sector_pm', 'portfolio_pm')
    s.edge('atr_pm',    'portfolio_pm')
    s.edge('grails_pm', 'portfolio_pm')

    ok_node(s, 'pm_end', 'market open — positions active')
    s.edge('portfolio_pm', 'pm_end')


# ════════════════════════════════════════════════════════════════════════════
# ②  INTRADAY
# ════════════════════════════════════════════════════════════════════════════
with dot.subgraph(name='cluster_intraday') as s:
    s.attr(
        label='② INTRADAY — every 15 min  (10:00 AM - 3:45 PM ET)',
        style='filled', fillcolor=BG_ID, color='#4a9e6a',
        fontsize='12', fontcolor='#1a5c32', fontname='Helvetica-Bold',
        labeljust='l',
    )

    # Reconcile + Stale Positions
    with s.subgraph() as r:
        r.attr(rank='same')
        agent(r, 'reconcile', 'Reconcile with Alpaca  (3 passes)',
              '① fill_backfill — NULL positions matched to filled buys<BR/>'
              '② stale pending orders &gt;5 min → cancel + mark UNFILLED<BR/>'
              '③ classify OPEN positions: TARGET / STOP / NATIVE_TRAIL / UNFILLED',
              '#2e7d32')
        agent(r, 'stale_pos', 'Stale Positions',
              'Breakeven lock — hard-stop safety net<BR/>'
              'LOCK_IN tiers: &gt;$716 realized → close all · &gt;$1,000 total → close all',
              '#2e7d32')

    # Guard diamonds — row 1
    with s.subgraph() as r:
        r.attr(rank='same')
        gate(r, 'g_maxruns', 'MAX_RUNS?',  '≤ 6 per day')
        gate(r, 'g_minint',  'MIN_INTV?',  '≥ 30 min since last run')
        gate(r, 'g_maxpos',  'MAX_POS?',   '&lt; 15 open positions')

    # Guard diamonds — row 2
    with s.subgraph() as r:
        r.attr(rank='same')
        gate(r, 'g_maxday',  'MAX_DAILY?',    '&lt; 12 trades today')
        gate(r, 'g_losslim', 'DAILY LOSS?',   'realized &gt; -$1,500')
        gate(r, 'g_bonus',   'BONUS TARGET?', 'realized &gt; $1,000')

    skip_node(s, 'exit_id', 'skip this cycle')

    s.edge('reconcile',  'g_maxruns')
    s.edge('stale_pos',  'g_maxruns')
    s.edge('g_maxruns',  'g_minint',  label='OK', fontsize='8')
    s.edge('g_minint',   'g_maxpos',  label='OK', fontsize='8')
    s.edge('g_maxpos',   'g_maxday',  label='OK', fontsize='8')
    s.edge('g_maxday',   'g_losslim', label='OK', fontsize='8')
    s.edge('g_losslim',  'g_bonus',   label='OK', fontsize='8')
    no_edge(s, 'g_maxruns',  'exit_id', 'exceeded')
    no_edge(s, 'g_minint',   'exit_id', 'too soon')
    no_edge(s, 'g_maxpos',   'exit_id', 'full')
    no_edge(s, 'g_maxday',   'exit_id', 'exceeded')
    no_edge(s, 'g_losslim',  'exit_id', 'halt')
    no_edge(s, 'g_bonus',    'exit_id', 'halt')

    # Intraday scanners — side by side
    with s.subgraph() as r:
        r.attr(rank='same')
        agent(r, 'id_scanner',
              'Intraday Scanner',
              'Same filters as premarket · momentum overlay added')
        agent(r, 'id_momentum',
              'Momentum Scanner',
              'Breakout velocity · VWAP reclaim · volume surge')

    s.edge('g_bonus', 'id_scanner',  label='continue', fontsize='8')
    s.edge('g_bonus', 'id_momentum', fontsize='8')

    # Claude Strategy Agent (intraday)
    agent(s, 'claude_id', 'Claude Strategy Agent  (claude-sonnet-4-6)',
          'Sector conviction injected · quiet_day → tighter targets · avoids re-entering closed tickers',
          C_AGENT_DRK)
    s.edge('id_scanner',  'claude_id')
    s.edge('id_momentum', 'claude_id')

    # Risk + Sector Guard — side by side
    with s.subgraph() as r:
        r.attr(rank='same')
        agent(r, 'risk_id',   'Risk Agent',
              'Same R:R + size rules · ATR sizer not re-run for intraday entries')
        agent(r, 'sector_id', 'Sector Guard',
              'MAX_PER_SECTOR cap still enforced')

    s.edge('claude_id', 'risk_id')
    s.edge('claude_id', 'sector_id')

    # Portfolio → Alpaca (intraday)
    agent(s, 'portfolio_id', 'Portfolio Agent → Alpaca Bracket Orders',
          'Same bracket structure · +1% Leg A cap enforced · time-adjusted targets',
          C_AGENT_DRK)
    s.edge('risk_id',   'portfolio_id')
    s.edge('sector_id', 'portfolio_id')

    ok_node(s, 'id_loop', 'wait 15 min → repeat')
    s.edge('portfolio_id', 'id_loop')


# Cross-cluster edges
dot.edge('pm_end', 'reconcile',
         label='market opens', style='dashed',
         color='#777777', fontcolor='#555555', fontsize='8')
dot.edge('id_loop', 'reconcile',
         label='next cycle', style='dashed',
         color='#27ae60', fontcolor='#27ae60', fontsize='8',
         constraint='false')
dot.edge('id_loop', 'g_eod_dupe',
         label='market closes (3:45 PM ET)', style='dashed',
         color='#777777', fontcolor='#555555', fontsize='8')


# ════════════════════════════════════════════════════════════════════════════
# ③  EOD
# ════════════════════════════════════════════════════════════════════════════
with dot.subgraph(name='cluster_eod') as s:
    s.attr(
        label='③ EOD — 4:10 PM ET',
        style='filled', fillcolor=BG_EOD, color='#c0a020',
        fontsize='12', fontcolor='#5c3d00', fontname='Helvetica-Bold',
        labeljust='l',
    )

    gate(s, 'g_eod_dupe', 'Already ran today?')
    skip_node(s, 'exit_eod', 'skip EOD')
    no_edge(s, 'g_eod_dupe', 'exit_eod', 'YES')

    with s.subgraph() as r:
        r.attr(rank='same')
        agent(r, 'close_pos', 'Close All Positions',
              'Market sell for any remaining OPEN · close_reason = EOD', C_EOD)
        agent(r, 'phantom_stop', 'Phantom STOP Cleanup',
              'Cancel stale bracket legs · mark CLEANUP · excluded from P&amp;L calcs', C_EOD)

    s.edge('g_eod_dupe', 'close_pos',    label='NO', fontsize='8')
    s.edge('g_eod_dupe', 'phantom_stop', fontsize='8')

    with s.subgraph() as r:
        r.attr(rank='same')
        agent(r, 'daily_perf', 'Daily Performance',
              'realized P&amp;L · win rate · trade count · avg hold time · benchmark delta', C_EOD)
        agent(r, 'rolling_eval', '30-day Rolling Eval',
              'Sharpe ratio · max drawdown · avg daily P&amp;L · regime notes', C_EOD)

    s.edge('close_pos',    'daily_perf')
    s.edge('phantom_stop', 'daily_perf')
    s.edge('close_pos',    'rolling_eval')
    s.edge('phantom_stop', 'rolling_eval')

    agent(s, 'daily_summary', 'Daily Summary + Alerts',
          'Gmail: P&amp;L · trades · win rate · any integrity warnings', C_EOD)
    s.edge('daily_perf',   'daily_summary')
    s.edge('rolling_eval', 'daily_summary')

    s.node('june8_gate',
        lbl('JUNE 8 EVAL GATE',
            'python3 eval.py --days 14'
            '<BR/>win rate ≥ 70% · avg daily P&amp;L ≥ $500 · NATIVE_TRAIL confirmed'
            '<BR/>no integrity flags · confidence ≥ 7/10'),
        shape='box', style='rounded,filled', fillcolor='#8b0000', fontcolor=C_WHITE)
    s.edge('daily_summary', 'june8_gate',
           style='dashed', color='#8b0000', fontcolor='#8b0000')


# ════════════════════════════════════════════════════════════════════════════
# ④  INFRASTRUCTURE
# ════════════════════════════════════════════════════════════════════════════
with dot.subgraph(name='cluster_infra') as s:
    s.attr(
        label='④ SUPABASE + INFRASTRUCTURE',
        style='filled', fillcolor=BG_INF, color='#c0622c',
        fontsize='12', fontcolor='#5c1a00', fontname='Helvetica-Bold',
        labeljust='l',
    )

    with s.subgraph() as r:
        r.attr(rank='same')
        agent(r, 'db_pos',  'positions',
              'entry/exit · P&amp;L · status · alpaca_order_id', C_DB)
        agent(r, 'db_scan', 'scan_results',
              'candidates · scores · halt_reasons', C_DB)
        agent(r, 'db_perf', 'daily_performance',
              'P&amp;L · win rate · trades · eval_score', C_DB)
        agent(r, 'db_runs', 'daily_runs',
              'timestamps · dupe guard', C_DB)
        agent(r, 'db_ml',   'ML model',
              'ml_results · feature store', C_DB)

    with s.subgraph() as r:
        r.attr(rank='same')
        agent(r, 'dashboard', 'StreamDriven Dashboard',
              'Live P&amp;L · positions · daily summary · alerts', C_INFRA)
        agent(r, 'github_actions', 'GitHub Actions + cron-job.org',
              'Premarket 10:00 AM · Intraday every 15 min · EOD 4:10 PM', C_INFRA)
        agent(r, 'alpaca', 'Alpaca Paper Trading',
              'Bracket orders · fills · account equity · open positions', C_INFRA)


# ── Invisible anchors — enforce top-to-bottom section order ─────────────────
dot.edge('_title',      'g_td',       style='invis')   # title above premarket
dot.edge('june8_gate',  'db_pos',     style='invis')   # infra below EOD
dot.edge('june8_gate',  'dashboard',  style='invis')   # infra below EOD

# ── Render ───────────────────────────────────────────────────────────────────
dot.render('workflow_diagram', cleanup=True, view=False)
print('Saved: workflow_diagram.png')
