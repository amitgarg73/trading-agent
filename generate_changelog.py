"""
Generates Trading_Agent_Changelog.docx — a living session-by-session record
of what was built, why, the impact, and confidence score at each version.

Update this file every sprint when new features ship. Run alongside the
other generate_*.py scripts when bumping the version.
"""
import os
from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()

style = doc.styles['Normal']
style.font.name = 'Calibri'
style.font.size = Pt(11)

NAVY   = RGBColor(0x1A, 0x3A, 0x6A)
ORANGE = RGBColor(0xF4, 0x7B, 0x20)
GRAY   = RGBColor(0x44, 0x44, 0x44)

def heading(text, level=1):
    p = doc.add_heading(text, level=level)
    p.runs[0].font.color.rgb = NAVY
    return p

def subheading(text):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.bold = True
    run.font.size = Pt(11)
    run.font.color.rgb = ORANGE
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after  = Pt(2)
    return p

def body(text):
    p = doc.add_paragraph(text)
    p.paragraph_format.space_after = Pt(4)
    return p

def bullet(text):
    p = doc.add_paragraph(text, style='List Bullet')
    p.paragraph_format.left_indent = Inches(0.25)
    p.paragraph_format.space_after = Pt(2)
    return p

def add_table(headers, rows):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = 'Table Grid'
    hdr_cells = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr_cells[i].text = h
        for run in hdr_cells[i].paragraphs[0].runs:
            run.font.bold = True
            run.font.color.rgb = NAVY
    for row_data in rows:
        row_cells = table.add_row().cells
        for i, val in enumerate(row_data):
            row_cells[i].text = val
    doc.add_paragraph()

def divider():
    p = doc.add_paragraph('─' * 80)
    p.runs[0].font.color.rgb = RGBColor(0xCC, 0xCC, 0xCC)
    p.runs[0].font.size = Pt(8)
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after  = Pt(4)


# ── Title ─────────────────────────────────────────────────────────────────────
title = doc.add_heading('AI Trading Agent — Session Changelog', 0)
title.alignment = WD_ALIGN_PARAGRAPH.CENTER
title.runs[0].font.color.rgb = NAVY

meta = doc.add_paragraph('Amit Garg  ·  May 2026  ·  v5.6  ·  Living document — update each sprint')
meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
meta.runs[0].font.size = Pt(11)
meta.runs[0].font.color.rgb = GRAY

doc.add_paragraph()
body(
    'This document is the authoritative session-by-session record of what was built, '
    'why it was built, the impact on the trading agent, and the real-money confidence score '
    'at each point. Update this file every time a new version ships. '
    'Run python3 generate_changelog.py to regenerate Trading_Agent_Changelog.docx.'
)


# ══════════════════════════════════════════════════════════════════════════════
# v5.6 — 2026-05-18
# ══════════════════════════════════════════════════════════════════════════════
doc.add_page_break()
heading('v5.6 — 2026-05-18')
body('Confidence score: 6/10 (was 5/10 entering this session)')
body('Theme: P0 real-money blocker resolved + automated validation infrastructure')

divider()
subheading('1. Native Alpaca Trailing Stop')
body(
    'What: StopLossRequest(trail_percent=TRAIL_PCT*100) wired into the bracket order '
    'stop-loss leg. USE_NATIVE_TRAILING_STOP feature flag (enabled as of 2026-05-18). '
    'native_trail_active boolean stored per position. refresh_positions() skips manual '
    'high-watermark check when flag is active. Dashboard shows "Trail 1% ↑ (native)".'
)
body(
    'Why: The manual trailing stop polls every 15 minutes. A stock that peaks and drops '
    '1.5% in 8 minutes isn\'t caught until the next cycle — that\'s real money left on the '
    'table on real capital. Alpaca\'s native trail fires the instant the reversal threshold '
    'is crossed, in real-time, with no polling gap.'
)
body(
    'Impact: Closes the most dangerous gap between paper and real money. Fast reversals — '
    'the hardest scenario for an intraday momentum strategy — are now handled by Alpaca\'s '
    'infrastructure at millisecond resolution. This was the explicit P0 blocker before going live.'
)

divider()
subheading('2. Exit Mechanism Tracking')
body(
    'What: exit_mechanism column added to positions (NATIVE_TRAIL, TARGET, MANUAL_TRAIL, '
    'STOP, EOD). Every close path in portfolio.py and alpaca_broker.get_order_fill() '
    'populates it. get_order_fill() now distinguishes trailing_stop leg type from fixed '
    'stop leg to return NATIVE_TRAIL vs STOP.'
)
body(
    'Why: Without per-position exit tracking there was no way to programmatically confirm '
    'the native trailing stop was firing correctly. Manual Alpaca log inspection doesn\'t scale.'
)
body(
    'Impact: Every closed position now has a complete exit story. '
    'Makes the 2-week validation automatic — no manual log-checking required.'
)

divider()
subheading('3. eval.py — Full Rebuild')
body('Four layers added on top of the existing eval:')
bullet(
    'VERDICT summary — plain-language What\'s working / Watch / Action required at the top. '
    'Synthesizes every metric into a 10-second read with specific action calls.'
)
bullet(
    'Annotated metrics — inline ✅/⚠️/❌ flags with benchmark targets on every number. '
    'Grade score broken into P&L (40pts) / win-day (30pts) / win-rate (30pts) components. '
    'Exit reason distribution with healthy mix interpretation.'
)
bullet(
    'Integrity checks — UNFILLED rate, orphaned open positions, duplicate ticker detection, '
    'missing exit_mechanism count, loss-limit day count, lock-in trigger count. '
    'All previously invisible.'
)
bullet(
    'Claude quality checks — R:R integrity per planned trade (guardrails don\'t enforce R:R), '
    'position size bounds, confidence cohort table: HIGH/MEDIUM/LOW win rate, avg P&L, '
    'total P&L with signal on whether HIGH actually outperforms LOW.'
)
bullet(
    'Trailing stop validation — native vs manual cohort comparison; delta on win rate and avg P&L; '
    'explicit confirmation when NATIVE_TRAIL exits are clean with no double-sells.'
)
body(
    'Why: The old eval answered "are we making money?" The new eval answers "is the system '
    'behaving correctly, is Claude respecting constraints, is the trailing stop working, '
    'and are we ready for real money?" — all in one run.'
)
body(
    'Impact: June 1 python3 eval.py --days 14 produces a complete pass/fail verdict on '
    'every real-money readiness dimension. No spreadsheets, no manual log-checking.'
)

divider()
subheading('4. 2-Week Paper Validation Gate')
body(
    'What: Native trailing stop enabled on paper 2026-05-18. Gate date: 2026-06-01. '
    'PLAN.md updated with pass criteria: win rate ≥80%, avg P&L ≥$500/day, '
    'NATIVE_TRAIL exits confirmed, no integrity flags.'
)
body(
    'Why: Enabling a new exit mechanism on real capital without validation is reckless. '
    'Two weeks of paper trades with automated exit tracking provides statistical confidence.'
)
body('Impact: Converts open-ended "validate before going live" into a concrete gated milestone.')

add_table(
    ['Item', 'Status'],
    [
        ('All 5 friction fixes live', '✅ Done'),
        ('ML scorer live (AUC 0.78)', '✅ Done'),
        ('Native trailing stop built and enabled', '✅ Done'),
        ('Trailing stop validated on paper', '⏳ In progress — gate: 2026-06-01'),
        ('Post-fix backtest rerun', '⬜ After June 1'),
        ('Real capital sizing decided', '⬜ After June 1'),
        ('Confidence assessment ≥7/10', '⬜ After June 1'),
    ]
)


# ══════════════════════════════════════════════════════════════════════════════
# v5.5 — 2026-05-18 (morning)
# ══════════════════════════════════════════════════════════════════════════════
doc.add_page_break()
heading('v5.5 — 2026-05-18 (morning)')
body('Confidence score: 5/10')
body('Theme: ML scoring layer + target tuning to make daily goal reachable')

divider()
subheading('1. Profit Target Tuning (3% → 2%, Stop 1% → 0.67%)')
body(
    'What: TARGET_PCT lowered from 3% → 2%. MAX_LOSS_PER_TRADE lowered from 1% → 0.67%. '
    '3:1 reward:risk maintained. Break-even win rate drops from 25% to 25% (maintained). '
    'Strategy prompt uses values dynamically.'
)
body(
    'Why: A 3% intraday move is rare — most strong momentum stocks move 1.5–2.5% before '
    'reversing. Lowering to 2% dramatically increases the number of days the target is reachable.'
)
body('Impact: More trades hitting target each day. Wider reachable range without sacrificing R:R.')

divider()
subheading('2. ML Scorer (Step 1.76)')
body(
    'What: HistGradientBoostingClassifier trained on 2y price history for all 429 universe '
    'tickers. 13 features. AUC 0.78 ± 0.04 (5-fold TimeSeriesSplit). Step 1.76 in orchestrator '
    'sorts candidates by P(hit +2%) before Claude call. Top feature: atr_pct (0.165).'
)
body(
    'Why: The scanner produces 50–150 candidates daily. Claude has to pick 10–15. Without '
    'ranking, Claude sees weak and strong setups randomly. The ML scorer surfaces the '
    'highest-probability candidates first, improving selection quality.'
)
body(
    'Impact: Expected win rate improvement by filtering low-probability setups before they '
    'consume position slots. AUC 0.78 is a meaningful predictor above random (0.5).'
)

divider()
subheading('3. Monthly ML Retrain Workflow')
body(
    'What: .github/workflows/retrain_model.yml — fires 1st of each month at 10 AM UTC. '
    'Downloads 2y data, retrains model, commits updated pkl + feature_columns.json to main. '
    'No manual step required.'
)
body('Why: A model trained on 2-year-old data drifts as market regimes change.')
body('Impact: Model stays current automatically. No manual intervention required.')


# ══════════════════════════════════════════════════════════════════════════════
# v5.4 — 2026-05-18 (early morning)
# ══════════════════════════════════════════════════════════════════════════════
doc.add_page_break()
heading('v5.4 — 2026-05-18 (early morning)')
body('Confidence score: 4/10')
body('Theme: All 5 execution friction fixes + live run stabilization')

divider()
subheading('All 5 Execution Friction Fixes')
add_table(
    ['Fix', 'What', 'Why', 'Impact'],
    [
        ('Real-time price refresh',
         'Alpaca live ask prices replace 15-min stale yfinance before Claude call (step 1.8)',
         'Claude was setting entry/target/stop on prices 15 min old — target often already eaten',
         'Accurate entry prices; Claude decisions based on actual market state'),
        ('Limit order entries',
         'Market orders → limit at entry_price × 1.001; unfilled orders auto-detected',
         'Market orders pay full spread; limit orders reduce slippage on liquid stocks',
         'Better fill quality; UNFILLED positions tracked and excluded from P&L'),
        ('Skip first 15 min',
         'Premarket run 9:00 → 9:45 AM ET; trading.yml cron updated',
         '9:30–9:45 AM has widest spreads; market makers still finding price',
         'Cleaner entries; avoids gap-and-fade setups from opening auction noise'),
        ('15-min intraday checks',
         'Intraday cycle halved: 30 min → 15 min',
         'Trailing stop miss window too wide — fast reversals missed in 30-min gap',
         'Stop miss window cut in half; faster response to reversals'),
        ('cron-job.org scheduler',
         'External HTTP triggers replace GitHub-native cron (fires 5–15 min late)',
         'GitHub cron is not guaranteed to fire on time — premarket ran late every day',
         'Runs fire at exact scheduled times; no more late premarket entries'),
    ]
)

divider()
subheading('Live Run Bug Fixes')
add_table(
    ['Bug', 'Fix'],
    [
        ('STRATEGY_MIN_SCORE=5 collapsed 93→4 candidates', 'Lowered to 4'),
        ('Futures unavailable on Mondays (period="2d")', 'Changed to period="5d"'),
        ('Mode detection failed on late runs', 'Switched to time-window arithmetic'),
        ('eval.py pulling all-time positions regardless of --days', 'Scoped to eval_dates from perf_rows'),
    ]
)


# ══════════════════════════════════════════════════════════════════════════════
# v5.3 and earlier — summary
# ══════════════════════════════════════════════════════════════════════════════
doc.add_page_break()
heading('v5.0 – v5.3 — Earlier Versions (Summary)')
body('Detailed commit history in generate_doc.py version history table (commits 1–60).')

add_table(
    ['Version', 'Theme', 'Key Features'],
    [
        ('v5.3', 'Trailing stops + confidence sizing',
         'high_watermark trailing stop (1% trail); HIGH $7K / MEDIUM $6K / LOW $5K sizing; '
         'dashboard trail stop annotations; fmt_stop() helper'),
        ('v5.2', 'Capital safety',
         'No-margin cumulative capital check; daily profit lock-in at $716 (LOCK_IN); '
         'dashboard LOCK_IN display'),
        ('v5.1', 'Dashboard + eval automation',
         'Summary tab redesign (In Flight / Plan / Heatmap); automated EOD eval; '
         'Agent Scorecard; Alpaca position reconciliation (UNFILLED)'),
        ('v5.0', 'Guardrails',
         '6 safety checks: action whitelist, ticker whitelist, duplicate guard, '
         'price sanity (±5%), capital check, daily loss limit (-$300)'),
        ('v4.0', 'Alpaca paper trading',
         'Bracket orders (entry + take-profit + stop-loss); simulation mode retained; '
         'broker= parameter throughout'),
        ('v3.0', 'Supabase + dashboard',
         'PostgreSQL via Supabase; Streamlit Cloud dashboard; 5 tables; '
         'positions, planned_trades, daily_performance'),
        ('v2.0', 'Claude strategy agent',
         'Anthropic API integration; SYSTEM prompt with universe + risk constraints; '
         'structured JSON trade plan output'),
        ('v1.0', 'Scanner foundation',
         'yfinance-based technical scanner; RSI, MACD, Bollinger, volume ratio; '
         'score -10 to +10; configurable universe'),
    ]
)


# ── How to Update This Document ───────────────────────────────────────────────
doc.add_page_break()
heading('How to Update This Document')
body('At the end of every feature sprint or version bump:')
bullet('Add a new top-level section (vX.Y — YYYY-MM-DD) above the previous version')
bullet('Fill in: confidence score, theme, one subsection per major feature shipped')
bullet('Each subsection: What / Why / Impact')
bullet('Add a status table showing pre-real-money checklist items')
bullet('Run python3 generate_changelog.py to regenerate Trading_Agent_Changelog.docx')
bullet('Run all other generate_*.py scripts to keep docs in sync')
bullet('Commit: git add *.docx *.png && git commit -m "docs: update all to vX.Y"')
body('')
body('Documents to regenerate on every version bump:')
add_table(
    ['Script', 'Output'],
    [
        ('python3 generate_changelog.py',   'Trading_Agent_Changelog.docx'),
        ('python3 generate_doc.py',         'Trading_Agent_Documentation.docx'),
        ('python3 generate_prd.py',         'Trading_Agent_PRD.docx'),
        ('python3 generate_features.py',    'Trading_Agent_Features.docx'),
        ('python3 generate_architecture.py','architecture_high_level.png + architecture_low_level.png'),
    ]
)


# ── Save ──────────────────────────────────────────────────────────────────────
_project_dir = "/Users/amitgarg/Claude Projects/trading-agent"
out = os.path.join(_project_dir, "Trading_Agent_Changelog.docx")
doc.save(out)
print(f"Saved: {out}")
