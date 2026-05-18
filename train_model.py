"""
Train XGBoost model to predict whether a stock will hit +2% intraday the next trading day.

Usage:
  python3 train_model.py              # train and save
  python3 train_model.py --eval       # train, save, and print feature importances

Output:
  models/xgb_scorer.pkl              — trained model
  models/feature_columns.json        — ordered feature list (must match inference)
"""
from __future__ import annotations
import argparse
import json
import os
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import yfinance as yf
import joblib
import ta
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import roc_auc_score

from config.settings import STOCK_UNIVERSE, ETF_UNIVERSE, TARGET_PCT

UNIVERSE    = STOCK_UNIVERSE + ETF_UNIVERSE
PERIOD      = "2y"
BATCH_SIZE  = 50      # tickers per yfinance download batch
MODELS_DIR  = os.path.join(os.path.dirname(__file__), "models")
MODEL_PATH  = os.path.join(MODELS_DIR, "xgb_scorer.pkl")
COLS_PATH   = os.path.join(MODELS_DIR, "feature_columns.json")

FEATURE_COLS = [
    "rsi", "macd_hist", "bb_pct", "vol_ratio", "atr_pct",
    "dist_sma20", "dist_sma50", "mom1", "mom5",
    "range_52w_pct", "dow", "vix", "technical_score",
]


# ── Feature computation ────────────────────────────────────────────────────────

def _technical_score(close: pd.Series, volume: pd.Series,
                     high: pd.Series, low: pd.Series) -> pd.Series:
    """Replicate scanner scoring logic vectorised over a full price history."""
    score = pd.Series(0.0, index=close.index)

    rsi = ta.momentum.RSIIndicator(close, 14).rsi()
    score += (rsi < 35).astype(float) * 2
    score -= (rsi > 65).astype(float) * 2

    macd_hist = ta.trend.MACD(close).macd_diff()
    score += (macd_hist > 0).astype(float) * 2
    score -= (macd_hist <= 0).astype(float) * 1

    bb_pct = ta.volatility.BollingerBands(close, 20, 2).bollinger_pband()
    score += (bb_pct < 0.2).astype(float) * 2
    score -= (bb_pct > 0.8).astype(float) * 1

    vol_ma20  = volume.rolling(20).mean()
    vol_ratio = volume / vol_ma20.replace(0, np.nan)
    score += (vol_ratio >= 1.5).astype(float) * 2

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    score += ((close > sma20) & (sma20 > sma50)).astype(float) * 1
    score -= ((close < sma20) & (sma20 < sma50)).astype(float) * 2

    return score.clip(-10, 10)


def compute_features(df: pd.DataFrame, vix_series: pd.Series) -> pd.DataFrame:
    """Compute all ML features for a single ticker's full price history."""
    close  = df["Close"]
    high   = df["High"]
    low    = df["Low"]
    volume = df["Volume"]

    rsi      = ta.momentum.RSIIndicator(close, 14).rsi()
    macd_h   = ta.trend.MACD(close).macd_diff()
    bb_pct   = ta.volatility.BollingerBands(close, 20, 2).bollinger_pband()
    atr      = ta.volatility.AverageTrueRange(high, low, close, 14).average_true_range()
    atr_pct  = (atr / close).replace([np.inf, -np.inf], np.nan)

    vol_ma20  = volume.rolling(20).mean()
    vol_ratio = (volume / vol_ma20.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)

    sma20     = close.rolling(20).mean()
    sma50     = close.rolling(50).mean()
    dist_sma20 = ((close - sma20) / sma20.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    dist_sma50 = ((close - sma50) / sma50.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)

    mom1 = close.pct_change(1)
    mom5 = close.pct_change(5)

    high52     = close.rolling(252, min_periods=20).max()
    low52      = close.rolling(252, min_periods=20).min()
    range_52w  = ((close - low52) / (high52 - low52).replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)

    dow = pd.Series(close.index.dayofweek, index=close.index, dtype=float)

    # Align VIX to this ticker's dates
    vix_aligned = vix_series.reindex(close.index, method="ffill")

    tech_score = _technical_score(close, volume, high, low)

    features = pd.DataFrame({
        "rsi":            rsi,
        "macd_hist":      macd_h,
        "bb_pct":         bb_pct,
        "vol_ratio":      vol_ratio,
        "atr_pct":        atr_pct,
        "dist_sma20":     dist_sma20,
        "dist_sma50":     dist_sma50,
        "mom1":           mom1,
        "mom5":           mom5,
        "range_52w_pct":  range_52w,
        "dow":            dow,
        "vix":            vix_aligned,
        "technical_score": tech_score,
    }, index=close.index)

    # Label: did NEXT day's high hit close × (1 + TARGET_PCT)?
    next_high = high.shift(-1)
    features["label"] = (next_high >= close * (1 + TARGET_PCT)).astype(int)

    return features


# ── Data download ──────────────────────────────────────────────────────────────

def download_vix() -> pd.Series:
    print("  Downloading VIX...")
    vix_df = yf.download("^VIX", period=PERIOD, progress=False, auto_adjust=True)
    if vix_df.empty:
        return pd.Series(dtype=float)
    close = vix_df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close.index = close.index.tz_localize(None)
    return close.rename("vix")


def download_batch(tickers: list[str]) -> dict[str, pd.DataFrame]:
    try:
        raw = yf.download(tickers, period=PERIOD, progress=False,
                          auto_adjust=True, group_by="ticker")
        if raw.empty:
            return {}
        result = {}
        if len(tickers) == 1:
            t = tickers[0]
            df = raw.copy()
            df.index = df.index.tz_localize(None)
            if not df.empty:
                result[t] = df
        else:
            for t in tickers:
                if t not in raw.columns.get_level_values(0):
                    continue
                df = raw[t].dropna(how="all")
                df.index = df.index.tz_localize(None)
                if len(df) >= 60:
                    result[t] = df
        return result
    except Exception as e:
        print(f"    Batch download error: {e}")
        return {}


# ── Training ──────────────────────────────────────────────────────────────────

def build_dataset(vix: pd.Series) -> pd.DataFrame:
    batches = [UNIVERSE[i:i+BATCH_SIZE] for i in range(0, len(UNIVERSE), BATCH_SIZE)]
    all_frames = []

    for i, batch in enumerate(batches):
        print(f"  Processing batch {i+1}/{len(batches)} ({len(batch)} tickers)...")
        price_data = download_batch(batch)
        for ticker, df in price_data.items():
            try:
                feat = compute_features(df, vix)
                feat["ticker"] = ticker
                feat["date"]   = feat.index
                # Drop last row (label is unknown — no next day yet)
                feat = feat.iloc[:-1]
                # Drop rows with too many NaNs
                feat = feat.dropna(subset=["rsi", "macd_hist", "bb_pct", "label"])
                if len(feat) >= 30:
                    all_frames.append(feat)
            except Exception as e:
                print(f"    Skipping {ticker}: {e}")

    if not all_frames:
        raise RuntimeError("No data collected — check internet connection and universe")

    combined = pd.concat(all_frames, ignore_index=True)
    print(f"\n  Dataset: {len(combined):,} rows, {combined['ticker'].nunique()} tickers")
    print(f"  Label balance: {combined['label'].mean()*100:.1f}% positives")
    return combined


def train(df: pd.DataFrame, eval_mode: bool = False):
    X = df[FEATURE_COLS].fillna(0)
    y = df["label"]

    # Time-series cross-validation
    tscv = TimeSeriesSplit(n_splits=5)
    auc_scores = []

    pos_weight = (y == 0).sum() / max((y == 1).sum(), 1)
    model = HistGradientBoostingClassifier(
        max_iter=400,
        max_depth=4,
        learning_rate=0.05,
        class_weight={0: 1.0, 1: float(pos_weight)},
        random_state=42,
    )

    print("\n  Cross-validation (TimeSeriesSplit, 5 folds)...")
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_tr, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_tr, y_val = y.iloc[train_idx], y.iloc[val_idx]
        model.fit(X_tr, y_tr)
        prob = model.predict_proba(X_val)[:, 1]
        auc = roc_auc_score(y_val, prob)
        auc_scores.append(auc)
        print(f"    Fold {fold+1}: AUC = {auc:.4f}")

    print(f"\n  Mean AUC: {np.mean(auc_scores):.4f} ± {np.std(auc_scores):.4f}")

    # Final fit on all data
    print("  Training final model on full dataset...")
    model.fit(X, y)

    return model, X, y


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval", action="store_true", help="Print feature importances")
    args = parser.parse_args()

    os.makedirs(MODELS_DIR, exist_ok=True)
    print(f"\n{'='*60}")
    print(f"  TRAINING ML SCORER  (target: +{TARGET_PCT*100:.0f}% intraday)")
    print(f"  Universe: {len(UNIVERSE)} tickers  |  Period: {PERIOD}")
    print(f"{'='*60}\n")

    vix = download_vix()
    dataset = build_dataset(vix)
    model, X, y = train(dataset)

    joblib.dump(model, MODEL_PATH)
    with open(COLS_PATH, "w") as f:
        json.dump(FEATURE_COLS, f)
    print(f"\n  ✓ Model saved  → {MODEL_PATH}")
    print(f"  ✓ Feature cols → {COLS_PATH}")

    if args.eval:
        try:
            from sklearn.inspection import permutation_importance
            print("\n  Computing feature importances (permutation, 5 repeats)...")
            perm = permutation_importance(model, X, y, n_repeats=5, random_state=42, n_jobs=-1)
            print("  Feature importances:")
            for feat, imp in sorted(zip(FEATURE_COLS, perm.importances_mean), key=lambda x: -x[1]):
                bar = "█" * max(0, int(imp * 500))
                print(f"    {feat:20s}: {imp:.4f}  {bar}")
        except Exception as e:
            print(f"  (Feature importances unavailable: {e})")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
