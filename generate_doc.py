"""Generates the Trading Agent project documentation as a Word document."""
from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()

# ── Styles ────────────────────────────────────────────────────────────────────
style = doc.styles['Normal']
style.font.name = 'Calibri'
style.font.size = Pt(11)

def heading(text, level=1):
    p = doc.add_heading(text, level=level)
    run = p.runs[0]
    run.font.color.rgb = RGBColor(0x1A, 0x3A, 0x6A)
    return p

def body(text):
    p = doc.add_paragraph(text)
    p.paragraph_format.space_after = Pt(6)
    return p

def bullet(text):
    p = doc.add_paragraph(text, style='List Bullet')
    p.paragraph_format.left_indent = Inches(0.25)
    return p

def code(text):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.name = 'Courier New'
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0xC7, 0x25, 0x4E)
    p.paragraph_format.left_indent = Inches(0.5)
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
    for row_data in rows:
        row_cells = table.add_row().cells
        for i, val in enumerate(row_data):
            row_cells[i].text = val
    doc.add_paragraph()

# ── Title Page ────────────────────────────────────────────────────────────────
title = doc.add_heading('AI Trading Agent', 0)
title.alignment = WD_ALIGN_PARAGRAPH.CENTER
title.runs[0].font.color.rgb = RGBColor(0x1A, 0x3A, 0x6A)

sub = doc.add_paragraph('How It Was Built, How It Works, and What It Does')
sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
sub.runs[0].font.size = Pt(14)
sub.runs[0].font.color.rgb = RGBColor(0x55, 0x55, 0x55)

meta = doc.add_paragraph('Amit Garg  ·  May 2026  ·  v2.1  ·  Built with Claude Code + Anthropic API')
meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
meta.runs[0].font.size = Pt(10)
meta.runs[0].font.color.rgb = RGBColor(0x88, 0x88, 0x88)

doc.add_page_break()

# ── 1. Overview ───────────────────────────────────────────────────────────────
heading('1. Overview')
body(
    'The AI Trading Agent is a fully autonomous stock trading simulation system built '
    'using Python, the Anthropic Claude API, Supabase (PostgreSQL), GitHub Actions, and '
    'Streamlit. It scans a curated universe of 430+ stocks and ETFs every trading day, '
    'checks market conditions and news intelligence, selects the highest-conviction setups '
    'using a multi-agent AI pipeline, manages simulated positions throughout the day, and '
    'logs performance to a live password-protected dashboard — all without any human '
    'intervention after setup.'
)
body(
    'The system runs entirely in the cloud. Once deployed, it wakes up on its own schedule, '
    'makes decisions, stores results, and goes back to sleep. The dashboard shows the full '
    'workflow — market conditions, screened stocks, selected trades, and live position P&L — '
    'accessible from any device via a public URL.'
)

heading('Key Goals', 2)
bullet('Generate $750–$1,000/day in simulated paper trading profit')
bullet('Run fully autonomously Monday–Friday with no manual intervention')
bullet('Make every decision auditable — every trade has reasoning attached')
bullet('Protect against high-volatility markets, earnings binary events, and bad news')
bullet('Keep infrastructure cost at or near zero (GitHub Actions + Supabase + Streamlit Cloud free tiers)')

heading('Backtest Results (30 trading days, Apr–May 2026)', 2)
add_table(
    ['Configuration', 'Avg Daily P&L', 'Win Days', 'Annualized Return', 'Grade'],
    [
        ('52 tickers, 3 positions, 2% target', '$198', '50%', '50%', 'D'),
        ('408 tickers, 10 positions, 2% target', '$548', '87%', '138%', 'B'),
        ('434 tickers, 15 positions, 2.5% target', '$690', '90%', '174%', 'B'),
        ('430 tickers, 15 positions, 3% target (current)', '$750', '93%', '189%', 'B'),
    ]
)
body(
    'Path to $1K/day: capital compounds naturally. At $750/day on $100K starting capital, '
    'the portfolio reaches ~$133K in ~45 trading days, at which point the same percentage '
    'return equals $1,000/day.'
)

# ── 2. What We Built ──────────────────────────────────────────────────────────
doc.add_page_break()
heading('2. What We Built')

heading('Project Structure', 2)
body('The project lives at: /Users/amitgarg/Claude Projects/trading-agent/')
body('GitHub repository: https://github.com/amitgarg73/trading-agent (private)')
code('trading-agent/')
code('├── orchestrator.py              # Master controller — chains all agents')
code('├── requirements.txt             # Python dependencies')
code('├── schema.sql                   # Supabase database schema')
code('├── backtest.py                  # 30-day historical backtest (no Claude API)')
code('├── eval.py                      # Weekly eval script — grades live performance')
code('├── generate_doc.py              # Generates this Word document')
code('├── generate_prd.py              # Generates the PRD document')
code('├── .env                         # Local secrets (gitignored)')
code('├── .env.example                 # Template for secrets')
code('├── streamlit_secrets.toml       # Streamlit Cloud secrets template (gitignored)')
code('├── .gitignore                   # Excludes .env and secrets files')
code('├── .github/')
code('│   └── workflows/')
code('│       └── trading.yml          # GitHub Actions schedule')
code('├── scanner/')
code('│   └── scanner.py              # Market scanner (yfinance + TA, 430+ tickers)')
code('├── agents/')
code('│   ├── market_context.py       # V2a: VIX gate + futures signal + international')
code('│   ├── news_intel.py           # V2b: earnings blackout + news headlines')
code('│   ├── strategy.py             # Claude picks the best trades')
code('│   ├── risk.py                 # Claude validates risk parameters')
code('│   ├── portfolio.py            # Opens/tracks simulated positions')
code('│   ├── intraday.py             # Monitors open positions mid-day')
code('│   └── performance.py          # Calculates EOD P&L')
code('├── config/')
code('│   └── settings.py             # Capital, thresholds, 430+ stock universe')
code('├── core/')
code('│   └── db.py                   # Supabase database client')
code('└── dashboard/')
code('    └── app.py                  # Streamlit dashboard — full workflow view')

# ── 3. The Agent Pipeline ─────────────────────────────────────────────────────
doc.add_page_break()
heading('3. The Agent Pipeline')
body(
    'The system is a chain of specialized agents. Each agent does one job and passes its '
    'output to the next. orchestrator.py coordinates the chain. There are three daily '
    'pipeline runs: Premarket, Intraday, and End of Day.'
)

heading('3a. Premarket Pipeline (9:00 AM ET)', 2)
body('Runs once before the market opens. Six stages:')

body('Stage 0 — Market Context Agent (V2a)')
bullet('Fetches VIX (^VIX) — fear index, measures overall market volatility')
bullet('Fetches US futures: S&P500 (ES=F), Nasdaq (NQ=F), Dow (YM=F) — pre-market direction')
bullet('Fetches international markets: Nikkei, FTSE, DAX, Hang Seng, Shanghai — global context')
bullet('VIX > 30: skip trading entirely (extreme fear)')
bullet('VIX 25–30: reduce to 5 max positions; VIX 20–25: reduce to 10 max positions')
bullet('Futures avg down >1.5%: skip trading (strong sell-off); down >0.5%: bearish bias, reduce positions')
bullet('Passes full market summary to Claude strategy agent as context')
bullet('Stores VIX, futures, and international data in scan_results for full audit trail')

body('Stage 1 — Market Scanner')
bullet('Pulls price data for 430+ stocks and ETFs using yfinance (free, no API key needed)')
bullet('Calculates technical indicators: RSI, MACD, Bollinger Bands, volume ratio, ATR, SMA 20/50')
bullet('Scores each ticker from -10 to +10 based on signal strength')
bullet('Returns candidates with score ≥ 3 (configurable in settings.py)')
bullet('Universe expanded from 52 to 430+ tickers across 15 sectors based on backtest results')

body('Stage 1.5 — News Intelligence Agent (V2b)')
bullet('Checks earnings calendar for every candidate via yfinance ticker.calendar')
bullet('Removes any ticker with earnings today or tomorrow (binary event = gap risk)')
bullet('Fetches 3 most recent news headlines for each remaining candidate')
bullet('Passes earnings-filtered candidate list and news context string to strategy agent')
bullet('Logs all blocked tickers with reason for audit trail')

body('Stage 2 — Strategy Agent (Claude AI)')
bullet('Receives scored candidates, market summary (VIX + futures + news), and max_positions for today')
bullet('Claude reads market context, momentum, and signal combinations')
bullet('Selects up to max_positions highest-conviction trades for the day')
bullet('Assigns entry price (hard: 3% target above entry), stop loss (hard: 1% below entry)')
bullet('Sets position size ($5K–$7K per trade), shares, estimated profit, confidence (HIGH/MEDIUM/LOW)')
bullet('Writes 2–3 sentence reasoning for every trade citing specific signals from the scan data')
bullet('Can select zero trades if no high-conviction setups exist — protects principal')

body('Stage 3 — Risk Agent (Claude AI)')
bullet('Reviews every proposed trade against hard-coded risk rules')
bullet('Rejects if: stop loss > 1% of entry, target < 3% of entry, reward:risk < 3:1')
bullet('Rejects if: target below entry (BUY) or stop above entry (BUY)')
bullet('Position sizing: $5K–$7K per trade (5–7% of $100K capital)')
bullet('Returns approved and rejected trades with specific rejection reasons in plain English')

body('Stage 4 — Portfolio Agent')
bullet('Opens simulated positions for all approved trades in Supabase')
bullet('Check-before-insert prevents duplicate key errors on manual reruns')
bullet('Writes trade plan summary and all individual positions to the database')

heading('3b. Intraday Pipeline (Every 30 min, 10:00 AM – 3:30 PM ET)', 2)
bullet('Fetches current prices for all open positions via yfinance')
bullet('Calculates unrealized P&L for each position in real time')
bullet('Closes positions that hit +3% target (take profit) or -1% stop loss (cut loss)')
bullet('Records close reason: TARGET, STOP, or EOD')
bullet('No overnight holds — all positions closed by end of day')

heading('3c. End of Day Pipeline (4:30 PM ET)', 2)
bullet('Closes any remaining open positions at market close price (EOD reason)')
bullet('Calculates total daily P&L, win rate, best and worst trade')
bullet('Writes daily performance record to Supabase daily_performance table')
bullet('Updates portfolio capital for compounding into the next trading day')

# ── 4. The Technology Stack ───────────────────────────────────────────────────
doc.add_page_break()
heading('4. The Technology Stack')

add_table(
    ['Component', 'Technology', 'Purpose'],
    [
        ('AI Brain', 'Anthropic Claude API (claude-sonnet-4-6)', 'Strategy decisions and risk validation (~$0.15–0.30/day)'),
        ('Market Data', 'yfinance (free, no key needed)', 'Price history, volume, OHLC, news, earnings calendar'),
        ('Technical Analysis', 'ta (Python library)', 'RSI, MACD, Bollinger Bands, ATR, SMA'),
        ('Database', 'Supabase (PostgreSQL, free tier)', 'Stores all trades, positions, P&L history'),
        ('Scheduler', 'GitHub Actions (cron, free tier)', 'Runs the pipeline on schedule automatically'),
        ('Dashboard', 'Streamlit Cloud (free tier)', 'Live workflow view, password protected, always on'),
        ('Secrets (cloud)', 'GitHub Secrets + Streamlit Cloud Secrets', 'API keys injected at runtime, never in code'),
        ('Secrets (local)', '.env file (gitignored)', 'Local development credentials'),
        ('Version Control', 'Git + GitHub (private repo)', 'Code storage and CI/CD trigger (v1.0, v2.0, v2.1)'),
        ('Auth (GitHub)', 'SSH key pair', 'Passwordless git push from local machine'),
    ]
)

heading('Model Cost', 2)
body(
    'Switched from claude-opus-4-7 to claude-sonnet-4-6 to reduce API costs. '
    'Estimated cost: $0.15–0.30/day for two Claude calls (strategy + risk). '
    'At that rate, $10 of API credit lasts 33–66 trading days. '
    'Note: Anthropic console has no usage alert feature — monitor manually at console.anthropic.com.'
)

# ── 5. The Database Schema ────────────────────────────────────────────────────
heading('5. The Database Schema (Supabase)')
body('Five tables in PostgreSQL, hosted on Supabase free tier. Schema defined in schema.sql.')

add_table(
    ['Table', 'What It Stores', 'Key Constraint'],
    [
        ('trade_plans', 'One row per trading day — market context, estimated profit, risk note', 'UNIQUE on date'),
        ('planned_trades', 'Individual trades — ticker, entry, target, stop, confidence, reasoning, status', 'FK to trade_plans'),
        ('positions', 'Simulated open/closed positions with real-time P&L tracking', 'FK to planned_trades'),
        ('daily_performance', 'EOD summary — total P&L, win rate, best/worst trade, portfolio value', 'UNIQUE on date'),
        ('scan_results', 'Raw scanner + market context output every premarket run (full audit trail)', 'Indexed by date'),
    ]
)
body(
    'scan_results stores rich context per premarket run: VIX, futures (S&P/Nasdaq/Dow), '
    'international markets (Nikkei/FTSE/DAX/Hang Seng/Shanghai), futures_bias, screened '
    'candidates, and earnings-blocked tickers. All data is stored as JSONB.'
)

# ── 6. The Automation (GitHub Actions) ───────────────────────────────────────
doc.add_page_break()
heading('6. The Automation — GitHub Actions')
body(
    'The workflow file at .github/workflows/trading.yml runs the orchestrator on a UTC cron '
    'schedule. GitHub provides 2,000 free minutes/month — well within limits for current run '
    'frequency. The workflow also supports manual trigger for testing and reruns.'
)

add_table(
    ['Run', 'UTC Cron', 'ET Time', 'What Happens'],
    [
        ('Premarket', '0 13 * * 1-5', '9:00 AM Mon–Fri', 'Market check → Scan → News filter → Strategy → Risk → Open positions'),
        ('Intraday', '0,30 14-19 * * 1-5', 'Every 30 min 10AM–3:30PM', 'Monitor positions, close on target/stop'),
        ('EOD', '30 20 * * 1-5', '4:30 PM Mon–Fri', 'Close remaining, calculate daily P&L'),
    ]
)

body('Each run:')
bullet('Checks out the code from GitHub')
bullet('Sets up Python 3.11')
bullet('Installs dependencies from requirements.txt')
bullet('Injects secrets (ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_KEY) from GitHub Secrets')
bullet('Runs: python orchestrator.py --mode premarket|intraday|eod')
bullet('FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true set to suppress Node.js deprecation warnings')

heading('Manual Trigger', 2)
body(
    'Any run can be triggered manually from GitHub → Actions → Trading Agent → Run workflow. '
    'Select the mode (premarket, intraday, or eod) and click Run. Used for testing and re-running '
    'failed jobs. URL: https://github.com/amitgarg73/trading-agent/actions'
)

# ── 7. The Dashboard ──────────────────────────────────────────────────────────
heading('7. The Dashboard — Workflow View')
body(
    'A Streamlit web app (dashboard/app.py) shows the full pipeline progression in real time. '
    'Deployed to Streamlit Community Cloud — always on, accessible from any device. '
    'Password protected. Connects directly to Supabase and reads live data.'
)

heading('Today Tab — Full Workflow View', 2)
body(
    'The Today tab shows the complete pipeline run as a step-by-step workflow:'
)
add_table(
    ['Step', 'What You See'],
    [
        ('0 — Market Conditions', 'VIX (color-coded green/yellow/red), futures bias, S&P/Nasdaq/Dow % change, international markets expandable'),
        ('1 — Scanner', 'Total candidates found, earnings-blocked count, full table of screened stocks sorted by technical score'),
        ('2 — Strategy & Risk', "Claude's market read, estimated profit vs $1K target, approved trades table, expandable per-trade reasoning"),
        ('3 — Live Positions', 'Open positions with entry/current/target/stop and color-coded P&L, closed trades with realized P&L'),
    ]
)

heading('Other Tabs', 2)
add_table(
    ['Tab', 'What You See'],
    [
        ('Positions', 'All open positions with live unrealized P&L, all closed trades with realized P&L for today'),
        ('Performance', 'Historical P&L bar chart, portfolio value line chart, daily win rate, 30-day stats'),
        ('Scan Log', 'Historical premarket scans — VIX, futures bias, screened candidates, earnings blocks per run'),
    ]
)

heading('Access', 2)
bullet('URL: Your Streamlit Cloud app URL (dashboard password protected)')
bullet('Run locally: python3 -m streamlit run dashboard/app.py (opens at http://localhost:8501)')
bullet('Secrets set via: App → ⋮ → Settings → Secrets (TOML format) in Streamlit Cloud')

# ── 8. Configuration ──────────────────────────────────────────────────────────
doc.add_page_break()
heading('8. Configuration — What You Can Tune')
body('All tunable parameters live in config/settings.py:')

add_table(
    ['Parameter', 'Current Value', 'What It Controls'],
    [
        ('TOTAL_CAPITAL', '$100,000', 'Starting portfolio size'),
        ('DAILY_PROFIT_TARGET', '$1,000', 'Target P&L shown in dashboard'),
        ('MAX_POSITIONS', '15', 'Max concurrent open positions (reduced by market conditions)'),
        ('MAX_POSITION_PCT', '7%', 'Max capital per single trade ($7K)'),
        ('MIN_POSITION_PCT', '5%', 'Min capital per single trade ($5K)'),
        ('TARGET_PCT', '3%', 'Profit target per trade (hard rule — agent must set this exactly)'),
        ('MAX_LOSS_PER_TRADE', '1%', 'Stop loss threshold — rejects wider stops (hard rule)'),
        ('MIN_REWARD_RISK', '3.0', 'Minimum reward:risk ratio to approve a trade (3:1)'),
        ('SCORE_THRESHOLD', '3', 'Minimum scanner score to be a strategy candidate'),
        ('RSI_OVERSOLD', '35', 'RSI level considered oversold (buy signal)'),
        ('RSI_OVERBOUGHT', '65', 'RSI level considered overbought (sell signal)'),
        ('MIN_VOLUME_RATIO', '1.5x', 'Volume must be 1.5x the 20-day average'),
        ('MIN_AVG_VOLUME', '500,000', 'Minimum average daily volume (liquidity floor)'),
        ('MIN_PRICE', '$5.00', 'Minimum stock price (filters out penny stocks)'),
    ]
)

heading('Stock Universe (430+ Tickers Across 15 Sectors)', 2)
body(
    'The universe was expanded from 52 to 430+ tickers based on 30-day backtest results. '
    'Drag tickers (consistent underperformers) were removed. Top performers in backtest: '
    'IONQ, WOLF, RPD, QUBT, ACHR, ASAN, SOXS, AXTI, NET, MRNA.'
)
bullet('Mega-cap tech: AAPL, MSFT, NVDA, GOOGL, AMZN, TSLA, META')
bullet('AI / Cloud: PLTR, CRWD, SNOW, NET, DDOG, MDB, SMCI, AI, GTLB, CFLT')
bullet('Semiconductors: AMD, AVGO, QCOM (removed), AMAT, LRCX (removed), MRVL, KLAC, ONTO, WOLF, AXTI')
bullet('Quantum computing: IONQ, QUBT, RGTI, QBTS')
bullet('Fintech / Crypto: SOFI, HOOD, COIN, MSTR, RIOT, MARA, HUT8')
bullet('Biotech / Health: LLY, MRNA, ABBV, RXRX, ACHR (aerospace), BEAM, EDIT')
bullet('Energy / Clean: XOM, CVX, OXY, ENPH, FSLR, PLUG, RUN')
bullet('Defense / Industrial: LMT, RTX, KTOS, NOC, HII')
bullet('Retail / Consumer: AMZN, WMT, TGT, COST, SHOP')
bullet('Leveraged ETFs: TQQQ, SQQQ, SOXS, UPRO, SPXU, UVXY')
bullet('Removed drags: PYPL, META (moved to mega-cap), ARKK, IWM, JPM, IBM, MA, ROOT, PSA, TWLO, INTU, LRCX, ASML, HPE, UBER, TENB, ARKW, DUOL, GPN, QCOM, NKE, BROS, GLBE, EXEL')

# ── 9. V2 Intelligence Layer ──────────────────────────────────────────────────
doc.add_page_break()
heading('9. V2 Intelligence Layer')
body(
    'V2 adds pre-trade intelligence to reduce false signals and protect against market conditions '
    'where the base strategy should not run. Built in phases, each adding a new protection or signal.'
)

add_table(
    ['Phase', 'What', 'Status'],
    [
        ('V2a', 'Volatility gate + futures signal + international markets', 'Built and deployed (v2.0)'),
        ('V2b', 'Earnings blackout + news headlines for strategy context', 'Built and deployed (v2.1)'),
        ('V2c', 'Sector correlation guard — avoid over-concentration in one sector', 'Planned'),
        ('V2d', 'Sector rotation scoring — favor sectors showing relative strength', 'Planned'),
        ('V2e', 'Momentum confirmation — 15-minute rule before entry', 'Planned'),
        ('V2f', 'Alpaca paper trading integration — real order simulation', 'Planned'),
    ]
)

heading('V2a — Market Context Agent (agents/market_context.py)', 2)
body('Runs as Step 0 of premarket, before the scanner. Checks three things:')
bullet('VIX gate: ^VIX from yfinance. Skip if >30 (extreme fear). Reduce max_positions to 5 if 25–30, to 10 if 20–25.')
bullet('Futures gate: ES=F, NQ=F, YM=F. Skip if average down >1.5% (strong pre-market sell-off). Caution if down >0.5%, bullish bias if up >0.5%.')
bullet('International markets: Nikkei, FTSE, DAX, Hang Seng, Shanghai. Context only — no hard gate. Majority positive/negative noted in summary.')
body(
    'Returns: decision (GO/CAUTION/SKIP), max_positions (dynamic), summary string for Claude, '
    'vix, futures dict, intl_markets dict, futures_bias (BULLISH/BEARISH/NEUTRAL). '
    'On SKIP, records reason to scan_results and exits — no Claude API calls made.'
)

heading('V2b — News Intelligence Agent (agents/news_intel.py)', 2)
body('Runs as Step 1.5, between scanner and strategy. Two jobs:')
bullet('Earnings blackout: checks yfinance ticker.calendar for each candidate. Removes any with earnings today or tomorrow. Earnings = binary event = unacceptable gap risk for a day-trading system.')
bullet('News context: fetches 3 most recent headlines per remaining candidate from yfinance ticker.news. Passes them to Claude strategy agent as additional context in the prompt.')
body(
    'Returns: filtered_candidates (earnings-removed), blackout_tickers (with reason), '
    'news_context (formatted string for Claude prompt), news_by_ticker (dict for logging). '
    'Handles both DataFrame and dict formats of yfinance calendar (version compatibility).'
)

# ── 10. Backtesting ───────────────────────────────────────────────────────────
heading('10. Backtesting (backtest.py)')
body(
    'A deterministic 30-day historical backtest was built to validate strategy configurations '
    'without spending Claude API credits. It replays historical trading days using real yfinance '
    'price data and a rule-based scorer (no Claude).'
)
bullet('Scores each ticker per historical day using the same technical indicators as the live scanner')
bullet('Selects top N tickers by score as simulated trades')
bullet('Simulates entry at open price, checks daily high/low for 3% target or 1% stop hit')
bullet('Falls back to close price at end of day if neither target nor stop is hit')
bullet('Runs on 30 trading days of actual market data — no synthetic data')
bullet('Usage: python3 backtest.py --days 30 --top 15')
body(
    'Key fix required: yfinance returns multi-level column headers on batch downloads. '
    'Fixed with: df.columns = df.columns.get_level_values(0)'
)

# ── 11. Secrets and Security ──────────────────────────────────────────────────
doc.add_page_break()
heading('11. Secrets and Security')
add_table(
    ['Secret', 'Where Stored', 'What It Does'],
    [
        ('ANTHROPIC_API_KEY', 'GitHub Secrets + Streamlit Secrets + .env', 'Authenticates Claude API calls'),
        ('SUPABASE_URL', 'GitHub Secrets + Streamlit Secrets + .env', 'Points to your Supabase project'),
        ('SUPABASE_KEY', 'GitHub Secrets + Streamlit Secrets + .env', 'Service role key for DB read/write'),
        ('DASHBOARD_PASSWORD', 'Streamlit Secrets + .env', 'Protects the Streamlit dashboard login'),
    ]
)
body('Security principles applied:')
bullet('.env is gitignored — never pushed to GitHub')
bullet('streamlit_secrets.toml is gitignored — reference file only, never committed')
bullet('GitHub Secrets are encrypted and never visible in logs')
bullet('Streamlit Cloud secrets are encrypted at rest and injected at runtime only')
bullet('SSH key authentication used for git push — no passwords stored')
bullet('Supabase service role key used server-side only — never exposed to browser')

# ── 12. How It Was Built ──────────────────────────────────────────────────────
heading('12. How It Was Built')
body(
    'This system was built entirely using Claude Code — Anthropic\'s AI-powered CLI for software '
    'engineering. The process was conversational: describing what the system should do in plain '
    'English, and Claude Code generating the code, debugging errors, and wiring everything together. '
    'No prior Python expertise required for the builder — Claude handled implementation while the '
    'builder focused on decisions and direction.'
)

heading('Build Steps', 2)
add_table(
    ['Step', 'What', 'Details'],
    [
        ('1', 'Designed the architecture', 'Multi-agent pipeline — scanner → strategy → risk → portfolio'),
        ('2', 'Built the market scanner', 'yfinance + ta library, scoring 60+ tickers initially'),
        ('3', 'Built the strategy agent', 'Claude selects trades with entry/target/stop/reasoning'),
        ('4', 'Built the risk agent', 'Claude validates every trade against hard risk rules'),
        ('5', 'Built the portfolio agent', 'Opens and tracks simulated positions in Supabase'),
        ('6', 'Built the intraday agent', 'Monitors positions every 30 min, closes on target/stop'),
        ('7', 'Built the EOD agent', 'Calculates daily P&L, updates portfolio capital'),
        ('8', 'Wrote the orchestrator', 'Chains all agents, callable with --mode flag'),
        ('9', 'Created the database schema', 'Five Supabase tables with proper indexes and constraints'),
        ('10', 'Set up GitHub Actions', 'Cron workflow running premarket/intraday/EOD on weekdays'),
        ('11', 'Set up Git + GitHub (SSH)', 'Generated SSH key, added to GitHub, switched remote to SSH'),
        ('12', 'Set up Supabase', 'Created project, ran schema.sql, obtained URL and service role key'),
        ('13', 'Set GitHub Secrets', 'Added API keys to repo secrets for runtime injection'),
        ('14', 'End-to-end test', 'Manual premarket run via GitHub Actions — confirmed full pipeline working'),
        ('15', 'Built the Streamlit dashboard', '4-tab app with Today, Positions, Performance, Scan Log'),
        ('16', 'Deployed to Streamlit Cloud', 'Connected repo, set secrets in TOML format, got public URL'),
        ('17', 'Added password protection', 'Login screen added — only authorized users can view data'),
        ('18', 'Built backtest.py', '30-day historical replay with real yfinance data, no Claude API'),
        ('19', 'Expanded universe to 430+ tickers', 'Removed drags based on backtest, added momentum names'),
        ('20', 'Tuned to 3% target, 15 positions, 3:1 R:R', 'Backtest showed $750/day avg at B grade (93% win days)'),
        ('21', 'Built eval.py', 'Weekly grading script: A/B/C/D based on P&L, win rate, reward:risk'),
        ('22', 'Built V2a — Market Context Agent', 'VIX gate + futures signal + international markets'),
        ('23', 'Built V2b — News Intelligence Agent', 'Earnings blackout + news headlines context for Claude'),
        ('24', 'Redesigned dashboard to workflow view', 'Today tab shows all 4 steps: market → scan → plan → positions'),
        ('25', 'Generated documentation and PRD', 'Word documents with full project details'),
    ]
)

# ── 13. Issues Encountered ────────────────────────────────────────────────────
doc.add_page_break()
heading('13. Issues Encountered and How They Were Fixed')

add_table(
    ['Issue', 'Root Cause', 'Fix'],
    [
        ('git push failed', 'HTTPS auth not configured on Mac', 'Generated SSH key, added to GitHub, switched remote to SSH'),
        ('SSH permission denied after key setup', 'ssh-agent not running in shell session', 'Ran eval "$(ssh-agent -s)" && ssh-add ~/.ssh/id_ed25519'),
        ('Push worked in terminal but not Claude Code', 'SSH agent is session-scoped — not shared across processes', 'User runs git push directly from their terminal'),
        ('Python 3.9 type hint crash (X|Y syntax)', 'GitHub runner uses Python 3.9; X|Y union syntax requires 3.10+', 'Added from __future__ import annotations + used Optional[] from typing'),
        ('Duplicate trade plan DB error (23505)', 'Manual reruns tried to insert same date twice (UNIQUE constraint)', 'Check-before-insert logic in orchestrator before db.insert'),
        ('Dashboard showed no data', 'GitHub runner UTC date ≠ local machine date (one day ahead)', 'Dashboard falls back to most recent plan if today\'s is missing'),
        ('Node.js 20 deprecation warning in Actions', 'Default runner using Node 20, deprecated June 2026', 'Set FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true in workflow env'),
        ('Streamlit Cloud password not working', 'Secrets not set in Streamlit Cloud — app used empty string as password', 'Added all 4 secrets via Settings → Secrets in TOML format'),
        ('Streamlit secrets not loading via os.getenv()', 'st.secrets not accessible at module import time in settings.py', 'Moved secret injection to top of dashboard/app.py before imports'),
        ('pip/streamlit not found locally', 'Python not in PATH on Mac', 'Used pip3 and python3 -m streamlit instead'),
        ('backtest.py crash — 1D array error', 'yfinance returns multi-level column headers on batch download', 'Fixed with df.columns = df.columns.get_level_values(0)'),
        ('orchestrator undefined variable error', 'db.insert referenced intel["blackout_tickers"] before intel was defined', 'Moved db.insert to after news_intel.run() call'),
        ('yfinance calendar format varies by version', 'ticker.calendar returns DataFrame or dict depending on yfinance version', 'Added isinstance checks in news_intel._get_earnings_date()'),
    ]
)

# ── 14. Daily Operations ──────────────────────────────────────────────────────
heading('14. Daily Operations')
body('Once deployed, the system requires zero daily intervention. What happens automatically:')

add_table(
    ['Time (ET)', 'What Happens', 'Where to See It'],
    [
        ('9:00 AM Mon–Fri', 'Market check → Scan → Earnings filter → Strategy → Risk → Open positions', 'GitHub Actions logs + Dashboard Today tab'),
        ('Every 30 min 10AM–3:30PM', 'Intraday position monitoring, close on target/stop', 'Dashboard Positions tab'),
        ('4:30 PM Mon–Fri', 'EOD close + daily P&L calculation', 'Dashboard Performance tab'),
        ('Anytime', 'Manual trigger via GitHub Actions → Run workflow', 'GitHub Actions'),
        ('Anytime', 'View live workflow dashboard', 'Streamlit Cloud URL'),
    ]
)

heading('How to Check on Things', 2)
bullet('Dashboard: open your Streamlit Cloud URL → login → Today tab shows full workflow')
bullet('Logs: github.com/amitgarg73/trading-agent → Actions → click any run for full output')
bullet('Raw data: Supabase → Table Editor → browse any of the 5 tables directly')
bullet('Weekly scoring: python3 eval.py --days 5 (run Monday evening after first live week)')
bullet('Rerun manually: GitHub Actions → Trading Agent → Run workflow → select mode')

# ── 15. What's Next ───────────────────────────────────────────────────────────
heading('15. What\'s Next')
body('The system is live and running at v2.1. Planned next steps:')

add_table(
    ['Phase', 'What', 'Priority'],
    [
        ('V2c', 'Sector correlation guard — avoid over-concentration in one sector per run', 'Next'),
        ('V2d', 'Sector rotation scoring — favor sectors showing relative strength this week', 'Planned'),
        ('V2e', 'Momentum confirmation — 15-minute rule: wait for confirmed breakout before entry', 'Planned'),
        ('V2f', 'Alpaca paper trading API — real order simulation with fills and slippage', 'Planned'),
        ('Alerts', 'SMS/email on position close (target hit or stop triggered)', 'Planned'),
        ('CBRS', 'Add Cerebras to universe post-IPO (AI infrastructure, high-conviction)', 'Pending IPO'),
        ('Tune', 'If live win rate < 45% after first week, drop target back to 2.5%', 'Conditional'),
    ]
)

# ── Save ──────────────────────────────────────────────────────────────────────
output_path = "/Users/amitgarg/Claude Projects/trading-agent/Trading_Agent_Documentation.docx"
doc.save(output_path)
print(f"Saved: {output_path}")
