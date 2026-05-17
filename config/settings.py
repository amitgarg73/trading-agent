import os
from dotenv import load_dotenv

load_dotenv()

def _get(key, default=""):
    try:
        import streamlit as st
        return st.secrets.get(key, os.getenv(key, default))
    except Exception:
        return os.getenv(key, default)

# API Keys
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
SUPABASE_URL      = _get("SUPABASE_URL")
SUPABASE_KEY      = _get("SUPABASE_KEY")

# Dashboard
DASHBOARD_PASSWORD = _get("DASHBOARD_PASSWORD", "changeme")

# Capital
TOTAL_CAPITAL        = 100_000
DAILY_PROFIT_TARGET  = 1_000
MAX_POSITION_PCT     = 0.20       # max 20% of capital per position ($20K)
MIN_POSITION_PCT     = 0.10       # min 10% of capital per position ($10K)
MAX_POSITIONS        = 5          # max concurrent positions
MAX_LOSS_PER_TRADE   = 0.01       # stop loss: 1% of position size
MIN_REWARD_RISK      = 2.0        # minimum reward:risk ratio

# Scanner thresholds
RSI_OVERSOLD         = 35
RSI_OVERBOUGHT       = 65
MIN_VOLUME_RATIO     = 1.5        # vs 20-day avg
MIN_PRICE            = 5.0
MIN_AVG_VOLUME       = 500_000    # liquidity floor
SCORE_THRESHOLD      = 3          # minimum score to be a candidate

# Stock + ETF universe
STOCK_UNIVERSE = [
    # Mega cap tech (liquid, high beta)
    "AAPL", "MSFT", "NVDA", "META", "GOOGL", "AMZN", "TSLA",
    # High-momentum / AI
    "PLTR", "CRWD", "SNOW", "NET", "DDOG", "MDB", "SMCI",
    # Fintech
    "SOFI", "HOOD", "COIN", "PYPL", "SQ",
    # Biotech / Health
    "LLY", "MRNA", "ABBV",
    # Energy
    "XOM", "CVX", "OXY",
    # Finance
    "JPM", "GS", "BAC",
    # Defense / Industrials
    "LMT", "RTX", "KTOS",
    # Consumer
    "AMZN", "COST", "HD",
]

ETF_UNIVERSE = [
    # Broad market
    "SPY", "QQQ", "IWM", "DIA",
    # Sector ETFs
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLC",
    # Thematic
    "ARKK", "SOXX", "GLD", "TLT",
    # Leveraged (higher risk/reward)
    "TQQQ", "SQQQ", "UPRO", "SPXU",
]

UNIVERSE = STOCK_UNIVERSE + ETF_UNIVERSE

# Scheduler (UTC times, assuming EDT = UTC-4)
PREMARKET_UTC  = "13:00"   # 9:00 AM ET
INTRADAY_UTC   = "*/30 13-20 * * 1-5"  # every 30min, Mon-Fri
EOD_UTC        = "20:30"   # 4:30 PM ET
