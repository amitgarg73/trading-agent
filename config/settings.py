import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SUPABASE_URL      = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY      = os.getenv("SUPABASE_KEY", "")  # service role key

# Dashboard
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "changeme")

# Capital
TOTAL_CAPITAL        = 50_000
DAILY_PROFIT_TARGET  = 500
MAX_POSITION_PCT     = 0.07       # max 7% of capital per position (fits 15 positions)
MIN_POSITION_PCT     = 0.05       # min 5% of capital per position
MAX_POSITIONS        = 15         # max concurrent positions
MAX_DAILY_ENTRIES    = 10         # hard cap: total new positions opened per calendar day across all scans
MAX_LOSS_PER_TRADE   = 0.0067     # stop loss: 0.67% of position size (maintains 3:1 R:R with 2% target)
ATR_STOP_MULTIPLIER  = 0.8        # 0.8× ATR for stop — allows 3-4% ATR stocks to clear 2:1 R:R
ATR_STOP_FLOOR       = 0.005      # P0: minimum 0.5% stop — never tighter than this
MAX_LOSS_DOLLARS     = 150        # P0: constant dollar risk per trade ($150)
ORB_ATR_FLOOR        = 0.5        # P0: ORB/ATR ratio below this → choppy open → halve shares
MIN_REWARD_RISK            = 2.0   # minimum 2:1 R:R — balances trade quality vs opportunity count
QUIET_DAY_MIN_REWARD_RISK  = 1.5   # relaxed R:R on quiet days (Fear&Greed < 35)
QUIET_DAY_FG_THRESHOLD     = 35    # Fear&Greed below this = quiet day
TARGET_PCT           = 0.08       # 8% ceiling — trail (1.5%) does the actual exit; ceiling is a safety net for strong rockets
MAX_PER_SECTOR       = 3          # V2d: max positions in any single sector
DAILY_LOSS_PCT       = 0.01       # 1% of capital — daily net loss limit (realized + unrealized)
DAILY_LOSS_LIMIT     = -(TOTAL_CAPITAL * DAILY_LOSS_PCT)  # -$500 at $50K capital
PRICE_SANITY_PCT     = 0.03       # reject if entry price is >3% from live market price (tightened from 5% — catches stale scanner data)
DAILY_LOCK_IN_TARGET = 716        # Tier 1: realized P&L floor — stop closing positions, let winners ride
DAILY_BONUS_TARGET   = 1_000     # Tier 2: realized+unrealized total — close everything, protect exceptional day
LOCK_IN_TRAIL_PCT    = 0.005     # Tighter 0.5% trail applied to open positions after Tier 1 (simulation only)
TRAIL_PCT                   = 0.015       # Trailing stop: close if price drops 1.5% from highest seen since entry (widened from 1% — 1% fired on normal chop before capturing the move)
INTRADAY_STOP_PCT           = 0.01        # Intraday entry stop: 1% below entry (replaces fixed 0.67% — survives normal chop on 2-4% ATR stocks)
ENTRY_BUFFER_PCT            = 0.003       # Limit order buffer above plan price: 0.3% (was hardcoded 0.2% — wider to cut 44% unfill rate)
PARTIAL_PROFIT_ENABLED      = True        # partial exit at 0.5% move locks in half the position early
PARTIAL_PROFIT_PCT          = 0.005       # 0.5% partial exit — captures gains before reversal (tightened from 1%)
INTRADAY_SCAN_UTC_START         = 14          # 10:00 AM ET (after premarket finishes)
INTRADAY_SCAN_UTC_END           = 20          # outer scheduling window end
INTRADAY_ENTRY_CUTOFF_UTC       = 19          # 3:00 PM ET hard entry cutoff; late entries are negative EV
INTRADAY_SCAN_MAX_RUNS          = 6           # hourly cron: up to 6 slots across 10:30 AM–2:30 PM ET
INTRADAY_SCAN_MIN_INTERVAL_MINS = 0           # temporarily 0 for manual trigger; restore to 50 after
INTRADAY_TARGET_PCT             = 0.02        # 2% target for intraday entries — gives 2:1 R:R with new 1% stop (raised from 1.5%)
MIN_INTRADAY_MOVE_PCT           = 2.5         # minimum % move today to qualify as momentum candidate (lowered from 4.0 — catch early movers)
MIN_INTRADAY_VOLUME_RATIO       = 0.3         # minimum volume ratio for intraday entries — blocks illiquid noise
MIN_SPY_MOVE_PCT                = 0.0         # SPY gate disabled — no minimum SPY move required for intraday entries
MAX_INTRADAY_RANGE_PCT          = 5.0         # block stocks where avg(H-L)/Open > 5% — too volatile for 0.67% stop
MAX_SPREAD_PCT                  = 0.005       # 0.5% max bid-ask spread — wider eats into the 0.67% stop
MAX_PREMARKET_GAP_PCT           = 0.15        # 15% hard cap — gap-and-go stocks (8-15%) qualify if above VWAP + volume confirms
GAP_AND_GO_VOLUME_MIN           = 1.5         # gap-and-go qualifier: volume ratio must be >= this (high conviction)
STRONG_SECTOR_THRESHOLD         = 2.0         # sector ETF up >= this % → neutralise overbought/extended penalties for stocks in that sector
WEAK_SECTOR_THRESHOLD           = -1.0        # sector ETF down >= 1% → apply -1 score penalty (avoids MPC/XLE-type picks on sector-down days)
MAX_ATR_PCT                     = 5.0         # skip stocks with ATR% > this — ATR sizer would produce R:R < 1
STRATEGY_TAG                    = "a"         # prefix on every Alpaca client_order_id — enables per-strategy order filtering
LARGE_CAP_AVG_VOLUME            = 15_000_000  # avg volume above which volume ratio threshold is relaxed
LARGE_CAP_VOLUME_RATIO          = 0.5         # relaxed volume ratio for mega-caps (vs MIN_VOLUME_RATIO=1.5)
USE_NATIVE_TRAILING_STOP    = True        # After bracket fill, submit standalone TrailingStopOrderRequest — Alpaca tracks peak server-side, fires on reversal in real-time
POSITION_SIZE_BY_CONFIDENCE = {           # Position size mapped to Claude confidence level
    "HIGH":   3_500,
    "MEDIUM": 3_000,
    "LOW":    2_500,
}

# Scanner thresholds
RSI_OVERSOLD         = 35
RSI_OVERBOUGHT       = 65
MIN_VOLUME_RATIO     = 0.7        # vs 20-day avg — 0.7 accommodates holiday/low-vol weeks
MIN_PRICE            = 5.0
MIN_AVG_VOLUME       = 1_000_000  # liquidity floor — raised from 500K to 1M to reduce spread/slippage on smaller tickers
SCORE_THRESHOLD      = 4          # minimum score to be a scanner candidate (absolute value)

# Strategy pre-filter — applied in orchestrator BEFORE the Claude API call.
#
# WHY THIS EXISTS:
#   The scanner uses SCORE_THRESHOLD=3 (absolute value) and passes 100-200+ candidates
#   per day — both bullish (+) and bearish (-). The strategy agent only selects BUY trades,
#   so bearish candidates are tokens Claude reads but never acts on. Weak bullish candidates
#   (score 3-4) are almost never selected either. Filtering here shrinks the candidates JSON
#   from ~20K tokens to ~3-5K tokens, cutting Claude API input cost by 60-70%.
#
# TRADEOFF TO REVISIT:
#   A score-4 ticker with one unusual signal combo (e.g., extreme RSI + volume spike on
#   a low-liquidity day) might get filtered out even if it would have been Claude's pick.
#   If you notice good setups getting missed, lower this to 4. If you want to cut costs
#   further, raise to 6 (top-tier signals only).
#
#   Score reference: 3 = scanner floor | 4-5 = moderate signal | 6-7 = strong | 8-10 = rare
#   Typical daily distribution: ~150 candidates at score≥3, ~50 at score≥5, ~20 at score≥7
#
# NOTE: This filters to score >= STRATEGY_MIN_SCORE (positive), not abs(score).
#   Bearish candidates (negative scores) are always excluded — correct for a BUY-only system.
STRATEGY_MIN_SCORE   = 5          # intraday pre-filter: score ≥ this (raised from 4 — 35.7% win rate at 4 was too loose)
PREMARKET_MIN_SCORE  = 5          # premarket pre-filter: matches STRATEGY_MIN_SCORE

# Stock + ETF universe
# Removed drags: PYPL, META, ARKK, IWM, JPM, IBM, MA, ROOT, PSA, TWLO
# Removed delisted/no-data: CYBR, SMAR, ELASTIC, NEWR, SPLK, SUMO, ALTR, JAMF,
#   NVEI, SQ, BNPL, DFS, K, HES, PXD, CMA, COOP, L3H, NOVG, PARA, NKLA, GOEV, LILM
STOCK_UNIVERSE = [
    # ── Mega-cap tech ─────────────────────────────────────────────
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "TSLA",
    "AVGO", "ORCL", "CRM", "AMD", "INTC", "CSCO", "ADBE",
    "NOW", "TXN", "HPQ", "DELL", "ARM",

    # ── Semiconductors ────────────────────────────────────────────
    "MU", "AMAT", "KLAC", "LRCX", "MCHP", "MPWR", "ON",
    "STX", "WDC", "MRVL", "ADI", "SWKS", "QRVO", "WOLF",
    "SMCI", "ACLS", "COHU", "FORM", "AMBA", "CEVA", "SLAB",
    "DIOD", "ALGM", "AXTI", "SITM", "ALAB", "ONTO", "CRDO",
    "ENTG", "UCTT", "ICHR", "KLIC", "RMBS", "CRUS",

    # ── Software / Cloud / AI ─────────────────────────────────────
    "PLTR", "CRWD", "SNOW", "NET", "DDOG", "MDB", "HUBS",
    "ZS", "OKTA", "PANW", "FTNT", "RPD",
    "S", "ASAN", "BILL", "GTLB",
    "ESTC", "DT", "DOCN", "FSLY", "CWAN", "FOUR",
    "PATH", "AI", "BBAI", "SOUN", "BRZE", "FRSH", "DOMO",
    "APP", "APLD", "IONQ",
    "TTD", "MGNI", "PUBM", "CXAI", "GFAI",
    "RBLX", "U", "SHOP", "MELI", "SE", "GRAB",
    "PCVX", "DAVE", "MQ",

    # ── Fintech ───────────────────────────────────────────────────
    "SOFI", "HOOD", "COIN", "MSTR", "AFRM", "UPST",
    "LMND", "HIMS", "WEX", "GPN", "FIS", "FISV",
    "V", "AXP", "SYF", "COF", "ALLY", "OPEN",

    # ── Biotech / Pharma / Health ─────────────────────────────────
    "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR",
    "BMY", "AMGN", "GILD", "BIIB", "REGN", "VRTX", "ISRG",
    "BSX", "SYK", "MDT", "BDX", "RMD", "IQV", "MRNA",
    "BNTX", "NVAX", "SRPT", "BMRN", "ALNY", "RXRX", "ACAD", "HALO", "CELH",

    # ── Consumer Discretionary ────────────────────────────────────
    "HD", "LOW", "MCD", "SBUX", "TJX", "ROST",
    "BKNG", "MAR", "HLT", "CMG", "YUM", "DRI", "DKNG",
    "ETSY", "EBAY", "ABNB", "LVS", "MGM", "WYNN", "PENN",
    "F", "GM", "RIVN", "LCID",
    "CAVA", "SHAK", "TXRH", "WING",
    "ONON", "CROX", "FND", "RH",
    "LYFT", "UAL", "DAL", "AAL", "LUV", "ALK",
    "CCL", "NCLH",

    # ── Consumer Staples ──────────────────────────────────────────
    "PG", "KO", "PEP", "COST", "WMT", "TGT", "MDLZ",
    "GIS", "CPB", "HSY", "MKC", "CLX", "CL", "EL",

    # ── Energy ────────────────────────────────────────────────────
    "XOM", "CVX", "COP", "EOG", "OXY", "MPC", "VLO",
    "PSX", "DVN", "APA", "HAL", "SLB", "BKR",
    "NOV", "FANG", "CTRA", "SM", "MTDR", "CRGY",
    "CHRD", "PR", "TALO",

    # ── Financials ────────────────────────────────────────────────
    "GS", "MS", "BAC", "WFC", "C", "BLK", "SCHW",
    "CME", "ICE", "CBOE", "NTRS", "STT", "BK",
    "USB", "PNC", "RF", "CFG", "HBAN", "KEY", "TFC", "MTB",
    "ZION", "OFG", "UWMC", "RKT", "MKTX", "FIS", "FISV",

    # ── Industrials / Defense ─────────────────────────────────────
    "LMT", "RTX", "BA", "NOC", "GD", "TDG", "GE",
    "HON", "MMM", "EMR", "ETN", "PH", "ROK", "AME",
    "GNRC", "OTIS", "CARR", "ITW", "XYL", "KTOS", "AXON",
    "LDOS", "SAIC", "BAH", "CACI", "HII",
    "JOBY", "ACHR", "EVTL", "BE",

    # ── Materials ─────────────────────────────────────────────────
    "LIN", "APD", "DD", "DOW", "PPG", "SHW", "NEM",
    "FCX", "AA", "CLF", "NUE", "STLD", "RS", "VMC",
    "MLM", "FMC", "MOS", "CF", "MP",

    # ── Utilities / Power ─────────────────────────────────────────
    "NEE", "DUK", "SO", "D", "AEP", "EXC", "PCG",
    "XEL", "WEC", "ES", "CMS", "AWK", "SRE", "ETR",
    "CEG", "VST", "ENPH",

    # ── Communication / Media ─────────────────────────────────────
    "T", "VZ", "TMUS", "DIS", "NFLX", "CMCSA", "CHTR",
    "FOXA", "WBD", "EA", "TTWO", "MTCH",
    "ZM", "SNAP", "PINS", "ROKU", "SPOT",

    # ── Real Estate / REITs ───────────────────────────────────────
    "AMT", "PLD", "EQIX", "CCI", "SPG", "O", "AVB",
    "EQR", "DLR", "WELL", "VTR",

    # ── Space / Defense-adjacent ──────────────────────────────────
    "ASTS", "OKLO", "RKLB", "IRDM",

]

ETF_UNIVERSE = [
    # Broad market
    "SPY", "QQQ", "DIA", "VTI", "VOO", "IVV",
    # Mid / small cap
    "MDY", "IJH", "IJR", "VB", "IWO", "IWN",
    # Sector ETFs
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLC",
    "XLY", "XLP", "XLB", "XLRE", "XLU",
    # Thematic
    "SOXX", "SMH", "HACK", "CIBR", "WCLD", "BUG",
    "ARKG", "ARKF",
    "GLD", "SLV", "GDX", "GDXJ", "USO", "UNG",
    "TLT", "HYG", "LQD", "TIP",
]

UNIVERSE = STOCK_UNIVERSE + ETF_UNIVERSE

# Scheduler (UTC times, assuming EDT = UTC-4)
PREMARKET_UTC  = "14:00"   # 10:00 AM ET (delayed from 9:00 — Alpaca spreads stabilize ~30 min after open)
INTRADAY_UTC   = "*/15 14-19 * * 1-5"  # every 15min, Mon-Fri
EOD_UTC        = "20:30"   # 4:30 PM ET
