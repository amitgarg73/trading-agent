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
TOTAL_CAPITAL        = 100_000
DAILY_PROFIT_TARGET  = 1_000
MAX_POSITION_PCT     = 0.07       # max 7% of capital per position (fits 15 positions)
MIN_POSITION_PCT     = 0.05       # min 5% of capital per position
MAX_POSITIONS        = 15         # max concurrent positions
MAX_LOSS_PER_TRADE   = 0.0067     # stop loss: 0.67% of position size (maintains 3:1 R:R with 2% target)
MIN_REWARD_RISK      = 3.0        # minimum reward:risk ratio (2% target / 0.67% stop = 3:1)
TARGET_PCT           = 0.02       # 2% profit target per trade (lowered from 3% — more achievable intraday move)
MAX_PER_SECTOR       = 3          # V2d: max positions in any single sector
DAILY_LOSS_LIMIT     = -300       # V5: stop trading if today's realized P&L drops below this
PRICE_SANITY_PCT     = 0.05       # V5: reject if entry price is >5% from current market price
DAILY_LOCK_IN_TARGET = 716        # Tier 1: realized P&L floor — stop closing positions, let winners ride
DAILY_BONUS_TARGET   = 1_000     # Tier 2: realized+unrealized total — close everything, protect exceptional day
LOCK_IN_TRAIL_PCT    = 0.005     # Tighter 0.5% trail applied to open positions after Tier 1 (simulation only)
TRAIL_PCT                   = 0.01        # Trailing stop: close if price drops 1% from highest seen since entry
USE_NATIVE_TRAILING_STOP    = False       # trail_percent not supported in StopLossRequest bracket leg; use manual high_watermark trail
                                          # When False: manual high_watermark check every 15 min (safe default, paper OK)
                                          # Enable after 2-week paper A/B validation — P0 before real money
POSITION_SIZE_BY_CONFIDENCE = {           # Position size mapped to Claude confidence level
    "HIGH":   7_000,
    "MEDIUM": 6_000,
    "LOW":    5_000,
}

# Scanner thresholds
RSI_OVERSOLD         = 35
RSI_OVERBOUGHT       = 65
MIN_VOLUME_RATIO     = 1.5        # vs 20-day avg
MIN_PRICE            = 5.0
MIN_AVG_VOLUME       = 1_000_000  # liquidity floor — raised from 500K to 1M to reduce spread/slippage on smaller tickers
SCORE_THRESHOLD      = 3          # minimum score to be a scanner candidate (absolute value)

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
STRATEGY_MIN_SCORE   = 3          # pre-filter before Claude call: only bullish candidates with score ≥ this

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
    "MU", "AMAT", "KLAC", "MCHP", "MPWR", "ON",
    "STX", "WDC", "MRVL", "ADI", "SWKS", "QRVO", "WOLF",
    "SMCI", "ACLS", "COHU", "FORM", "AMBA", "CEVA", "SLAB",
    "DIOD", "ALGM", "AXTI", "SITM", "ALAB", "ONTO",
    "ENTG", "UCTT", "ICHR", "KLIC", "RMBS", "CRUS",

    # ── Software / Cloud / AI ─────────────────────────────────────
    "PLTR", "CRWD", "SNOW", "NET", "DDOG", "MDB", "HUBS",
    "ZS", "OKTA", "PANW", "FTNT", "RPD",
    "S", "ASAN", "BILL", "CFLT", "GTLB",
    "ESTC", "DT", "DOCN", "FSLY", "CWAN", "FOUR",
    "PATH", "AI", "BBAI", "SOUN", "BRZE", "FRSH", "DOMO",
    "APP", "APLD", "IONQ", "RGTI", "QUBT", "QBTS",
    "TTD", "MGNI", "PUBM", "CXAI", "GFAI",
    "RBLX", "U", "SHOP", "MELI", "SE", "GRAB",
    "PCVX", "DAVE", "MQ",

    # ── Fintech ───────────────────────────────────────────────────
    "SOFI", "HOOD", "COIN", "MSTR", "AFRM", "UPST",
    "LMND", "HIMS", "WEX", "GPN", "FIS", "FISV",
    "V", "AXP", "SYF", "COF", "ALLY", "OPEN",

    # ── Crypto-adjacent ───────────────────────────────────────────
    "MARA", "RIOT", "HUT", "CLSK", "IREN", "BITF", "CIFR",
    "BTBT", "WULF", "CORZ",

    # ── Biotech / Pharma / Health ─────────────────────────────────
    "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR",
    "BMY", "AMGN", "GILD", "BIIB", "REGN", "VRTX", "ISRG",
    "BSX", "SYK", "MDT", "BDX", "RMD", "IQV", "MRNA",
    "BNTX", "NVAX", "SRPT", "BMRN", "ALNY", "RARE", "PTCT",
    "FOLD", "TGTX", "PRAX", "IMVT", "ARWR", "BEAM", "EDIT",
    "NTLA", "CRSP", "KYMR", "KROS", "ABCL", "VKTX",
    "GPCR", "RXRX", "ACAD", "HALO", "CELH",
    "INVA", "RVMD", "PTGX", "AGIO",

    # ── Consumer Discretionary ────────────────────────────────────
    "HD", "LOW", "MCD", "SBUX", "TJX", "ROST",
    "BKNG", "MAR", "HLT", "CMG", "YUM", "DRI", "DKNG",
    "ETSY", "EBAY", "ABNB", "LVS", "MGM", "WYNN", "PENN",
    "F", "GM", "RIVN", "LCID",
    "CAVA", "SHAK", "TXRH", "WING",
    "ONON", "CROX", "FND", "RH",
    "LYFT",

    # ── Consumer Staples ──────────────────────────────────────────
    "PG", "KO", "PEP", "COST", "WMT", "TGT", "MDLZ",
    "GIS", "CPB", "HSY", "MKC", "CLX", "CL", "EL",

    # ── Energy ────────────────────────────────────────────────────
    "XOM", "CVX", "COP", "EOG", "OXY", "MPC", "VLO",
    "PSX", "DVN", "APA", "HAL", "SLB", "BKR",
    "NOV", "FANG", "CTRA", "SM", "MTDR", "CRGY",
    "CHRD", "PR", "ROCC", "TALO",

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
    "JOBY", "ACHR", "EVTL", "SPCE",

    # ── Materials ─────────────────────────────────────────────────
    "LIN", "APD", "DD", "DOW", "PPG", "SHW", "NEM",
    "FCX", "AA", "CLF", "NUE", "STLD", "RS", "VMC",
    "MLM", "FMC", "MOS", "CF", "MP",

    # ── Utilities ─────────────────────────────────────────────────
    "NEE", "DUK", "SO", "D", "AEP", "EXC", "PCG",
    "XEL", "WEC", "ES", "CMS", "AWK", "SRE", "ETR",

    # ── Communication / Media ─────────────────────────────────────
    "T", "VZ", "TMUS", "DIS", "NFLX", "CMCSA", "CHTR",
    "FOXA", "WBD", "EA", "TTWO", "MTCH",
    "ZM", "SNAP", "PINS", "ROKU", "SPOT",

    # ── Real Estate / REITs ───────────────────────────────────────
    "AMT", "PLD", "EQIX", "CCI", "SPG", "O", "AVB",
    "EQR", "DLR", "WELL", "VTR",

    # ── Space / Defense-adjacent ──────────────────────────────────
    "ASTS", "OKLO", "LUNR", "RKLB", "IRDM", "SPIR",

    # ── Meme / High-beta retail ───────────────────────────────────
    "GME", "AMC", "SPCE", "ARQQ",
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
    # Leveraged
    "TQQQ", "SQQQ", "UPRO", "SPXU", "SOXL", "SOXS",
    "UVXY", "SVXY", "LABU", "LABD",
]

UNIVERSE = STOCK_UNIVERSE + ETF_UNIVERSE

# Scheduler (UTC times, assuming EDT = UTC-4)
PREMARKET_UTC  = "14:00"   # 10:00 AM ET (delayed from 9:00 — Alpaca spreads stabilize ~30 min after open)
INTRADAY_UTC   = "*/30 13-20 * * 1-5"  # every 30min, Mon-Fri
EOD_UTC        = "20:30"   # 4:30 PM ET
