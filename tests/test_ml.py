"""Correctness tests for the ML layer's evaluation protocol.

The headline claim of this layer is honest, leakage-free evaluation, so the
purge gap between train and test is exactly what's worth pinning down.
"""

import numpy as np
import pandas as pd
from _helpers import load

ml = load("train_mod", "ml/train.py")


def test_walk_forward_folds_are_time_ordered_with_purge_gap():
    horizon = 10
    folds = list(ml.walk_forward_folds(n=600, horizon=horizon, n_folds=5))
    assert len(folds) > 0
    for train, test in folds:
        # Train is strictly before test...
        assert train[-1] < test[0]
        # ...and exactly `horizon` indices are purged in between, so no training
        # label (which peeks `horizon` ahead) can overlap the test window.
        assert test[0] - train[-1] - 1 == horizon


def test_target_is_forward_return_sign_with_ties_dropped():
    n = 11
    mid = np.arange(100.0, 100.0 + n)          # strictly increasing
    df = pd.DataFrame({
        "event_idx": range(n), "ts_ns": range(n),
        "best_bid": mid - 0.5, "best_ask": mid + 0.5,
        "bid_size": 1.0, "ask_size": 1.0,
        "mid": mid, "microprice": mid, "spread": 1.0,
        "imb1": np.linspace(-0.5, 0.5, n),     # varying so nothing collapses
        "imb5": np.linspace(-0.4, 0.4, n),
        "imb10": np.linspace(-0.3, 0.3, n),
    })
    path = "_tmp_ml.csv"
    df.to_csv(path, index=False)
    try:
        out = ml.load_and_engineer(path, horizon=2)
    finally:
        import os
        os.remove(path)
    # Strictly rising mid => every forward return is positive => all target 1,
    # and the last `horizon` rows (no label) are dropped.
    assert len(out) == n - 2
    assert (out["target"] == 1).all()
    assert "microprice_tilt" in out.columns and "mom" in out.columns
