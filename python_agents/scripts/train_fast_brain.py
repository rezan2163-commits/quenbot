"""
Offline FastBrain Trainer
=========================
feature_store Parquet verisinden LightGBM binary classifier eğitir.

Etiket:
  - Eğer `triple_barrier` modülü import edilebilirse → triple barrier etiketleri
    (up barrier=+h bps, down=-h bps, time=horizon_min). Label=1 iff up_barrier.
  - Aksi halde: naive forward return label
    `ret(horizon_min) > threshold_bps/1e4` → 1, aksi 0.

Kalibrasyon: Platt sigmoid (a, b) — validation skorlarından LR ile fit.

Çıktı:
  - <output>.lgb          (LightGBM booster)
  - <output>.calib.json   (kalibrasyon: method=platt, a, b, feature_order)

Kullanım:
  python scripts/train_fast_brain.py \\
      --days 30 --horizon-min 60 --threshold-bps 50 \\
      --output python_agents/.models/fast_brain_latest
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train FastBrain LightGBM model")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--horizon-min", type=int, default=60)
    p.add_argument("--threshold-bps", type=float, default=50.0,
                   help="pozitif etiket eşiği (bps, 1bp=0.01%)")
    p.add_argument("--output", type=str, default="python_agents/.models/fast_brain_latest")
    p.add_argument("--num-leaves", type=int, default=31)
    p.add_argument("--learning-rate", type=float, default=0.05)
    p.add_argument("--num-rounds", type=int, default=300)
    p.add_argument("--early-stop", type=int, default=30)
    p.add_argument("--val-ratio", type=float, default=0.2)
    return p.parse_args()


def _load_feature_store(days: int) -> "pd.DataFrame":
    import pandas as pd
    from feature_store import get_feature_store
    fs = get_feature_store()
    since = time.time() - days * 86400
    df = fs.read_pit(since=since)  # type: ignore[arg-type]
    if df is None or len(df) == 0:
        raise SystemExit("feature_store boş — önce scripts/backfill_feature_store.py çalıştır.")
    logger.info("feature_store: %d satır, %d sembol",
                len(df), df["symbol"].nunique() if "symbol" in df else -1)
    return df


def _make_labels(df, horizon_min: int, threshold: float):
    """Her sembol için horizon sonrası forward return ile etiket üret."""
    import numpy as np
    import pandas as pd

    df = df.sort_values(["symbol", "ts"]).copy()
    horizon_sec = horizon_min * 60
    labels = np.zeros(len(df), dtype=np.int8)
    keep = np.zeros(len(df), dtype=bool)

    for sym, grp in df.groupby("symbol", sort=False):
        g = grp.reset_index()
        idx = g["index"].to_numpy()
        ts = g["ts"].to_numpy()
        if "mid_price" in g.columns:
            px = g["mid_price"].to_numpy()
        elif "price" in g.columns:
            px = g["price"].to_numpy()
        else:
            continue
        j = 0
        for i in range(len(g)):
            target_ts = ts[i] + horizon_sec
            while j < len(g) and ts[j] < target_ts:
                j += 1
            if j >= len(g):
                break
            if px[i] <= 0:
                continue
            fwd_ret = (px[j] - px[i]) / px[i]
            labels[idx[i]] = 1 if fwd_ret > threshold else 0
            keep[idx[i]] = True

    return df[keep], labels[keep]


def _fit_platt(scores, y) -> Tuple[float, float]:
    """Basit Platt sigmoid fit: gradient descent ile log loss minimize."""
    import numpy as np
    s = np.asarray(scores, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    a, b = 1.0, 0.0
    lr = 0.1
    for _ in range(2000):
        z = a * s + b
        p = 1.0 / (1.0 + np.exp(-z))
        p = np.clip(p, 1e-6, 1.0 - 1e-6)
        grad_a = float(((p - y) * s).mean())
        grad_b = float((p - y).mean())
        a -= lr * grad_a
        b -= lr * grad_b
    return a, b


def main() -> int:
    from fast_brain import DEFAULT_FEATURE_ORDER
    args = _parse_args()

    try:
        import lightgbm as lgb
        import numpy as np
        import pandas as pd
    except Exception as e:
        logger.error("lightgbm/numpy/pandas yok: %s", e)
        return 2

    df = _load_feature_store(args.days)
    threshold = args.threshold_bps / 1e4
    df, y = _make_labels(df, args.horizon_min, threshold)
    if len(df) < 1000:
        logger.error("yeterli satır yok (%d)", len(df))
        return 3

    feats = [c for c in DEFAULT_FEATURE_ORDER if c in df.columns]
    if len(feats) < 4:
        logger.error("feature_store içinde yeterli feature kolonu yok: %s", feats)
        return 4
    logger.info("kullanılacak %d feature: %s", len(feats), feats)

    X = df[feats].astype("float32").to_numpy()
    # fill NaN
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    n = len(X)
    split = int(n * (1.0 - args.val_ratio))
    X_tr, X_va = X[:split], X[split:]
    y_tr, y_va = y[:split], y[split:]

    pos = float(y_tr.mean())
    logger.info("train=%d val=%d pozitif_oran=%.3f", len(X_tr), len(X_va), pos)
    if pos < 0.01 or pos > 0.99:
        logger.warning("pozitif sınıf dengesiz (%.3f) — eşik/horizon ayarla", pos)

    train_set = lgb.Dataset(X_tr, label=y_tr, feature_name=feats)
    val_set = lgb.Dataset(X_va, label=y_va, feature_name=feats, reference=train_set)

    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "learning_rate": args.learning_rate,
        "num_leaves": args.num_leaves,
        "min_data_in_leaf": max(20, n // 1000),
        "feature_fraction": 0.9,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "verbose": -1,
    }
    booster = lgb.train(
        params,
        train_set,
        num_boost_round=args.num_rounds,
        valid_sets=[val_set],
        callbacks=[lgb.early_stopping(args.early_stop), lgb.log_evaluation(20)],
    )

    val_scores = booster.predict(X_va, num_iteration=booster.best_iteration)
    a, b = _fit_platt(val_scores, y_va)
    logger.info("Platt calibration: a=%.4f b=%.4f", a, b)

    out_base = Path(args.output)
    out_base.parent.mkdir(parents=True, exist_ok=True)
    model_path = str(out_base) + ".lgb"
    booster.save_model(model_path)
    calib_path = str(out_base) + ".calib.json"
    Path(calib_path).write_text(json.dumps({
        "method": "platt",
        "a": a,
        "b": b,
        "feature_order": feats,
        "trained_ts": time.time(),
        "train_rows": int(len(X_tr)),
        "val_rows": int(len(X_va)),
        "pos_rate": pos,
        "threshold_bps": args.threshold_bps,
        "horizon_min": args.horizon_min,
    }, indent=2), encoding="utf-8")
    logger.info("✅ kaydedildi: %s  +  %s", model_path, calib_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
