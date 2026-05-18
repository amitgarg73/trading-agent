"""
ML scoring inference module.
Loads the trained XGBoost model and scores scanner candidates.

Returns ml_score (0.0–1.0): probability the stock hits TARGET_PCT intraday tomorrow.
Falls back gracefully if model not found — scanner continues unaffected.
"""
from __future__ import annotations
import json
import os
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "xgb_scorer.pkl")
_COLS_PATH  = os.path.join(os.path.dirname(__file__), "..", "models", "feature_columns.json")

_model = None
_feature_cols: list[str] = []
_model_loaded = False


def _load():
    global _model, _feature_cols, _model_loaded
    if _model_loaded:
        return
    _model_loaded = True
    if not os.path.exists(_MODEL_PATH):
        return
    try:
        import joblib
        _model = joblib.load(_MODEL_PATH)
        with open(_COLS_PATH) as f:
            _feature_cols = json.load(f)
    except Exception as e:
        print(f"  ⚠️  ML scorer failed to load: {e}")
        _model = None


def score_candidates(candidates: list[dict], vix: float | None = None) -> list[dict]:
    """
    Add ml_score (0–1) to each candidate dict.
    Candidates without enough features get ml_score = 0.5 (neutral).
    Returns the same list with ml_score added in place.
    """
    _load()
    if _model is None:
        for c in candidates:
            c["ml_score"] = None
        return candidates

    rows = []
    for c in candidates:
        rows.append({
            "rsi":            c.get("rsi") or 50.0,
            "macd_hist":      c.get("macd_hist") or 0.0,
            "bb_pct":         c.get("bb_pct") or 0.5,
            "vol_ratio":      c.get("volume_ratio") or 1.0,
            "atr_pct":        c.get("atr_pct") or 2.0,
            "dist_sma20":     c.get("dist_sma20") or 0.0,
            "dist_sma50":     c.get("dist_sma50") or 0.0,
            "mom1":           c.get("mom1") or 0.0,
            "mom5":           c.get("mom5") or 0.0,
            "range_52w_pct":  c.get("range_52w_pct") or 0.5,
            "dow":            pd.Timestamp.now().dayofweek,
            "vix":            vix if vix is not None else 20.0,
            "technical_score": c.get("technical_score") or 0,
        })

    X = pd.DataFrame(rows, columns=_feature_cols).fillna(0)
    probs = _model.predict_proba(X)[:, 1]

    for c, prob in zip(candidates, probs):
        c["ml_score"] = round(float(prob), 4)

    return candidates


def is_available() -> bool:
    _load()
    return _model is not None
