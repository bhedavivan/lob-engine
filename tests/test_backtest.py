"""Correctness tests for the taker backtester's math."""

import numpy as np
import pandas as pd
from _helpers import load

bt = load("backtest_mod", "backtest/backtest.py")


def test_signal_quality_perfect_predictor():
    # Alternating up/down mid, with imb1 whose sign matches the next move
    # exactly -> directional accuracy must be 1.0 over a horizon of 1.
    mid = [10.0, 11.0] * 20
    imb1 = [1.0 if mid[i + 1] > mid[i] else -1.0 for i in range(len(mid) - 1)] + [1.0]
    df = pd.DataFrame({"mid": mid, "imb1": imb1})
    q = bt.signal_quality(df, "imb1", horizon=1)
    assert q["accuracy"] == 1.0
    assert q["edge"] > 0.0                    # beats the 0.5 majority baseline
    assert q["correlation"] > 0.0


def test_run_strategy_pnl_is_hand_computable():
    # 3 events: go long at the ask, hold, then flip short (closing the long at
    # the bid). With zero fees the closed long breaks even and the mark-to-mid
    # of the fresh short is -0.5, so final PnL is exactly -0.5 points.
    df = pd.DataFrame({
        "best_bid": [100.0, 101.0, 101.0],
        "best_ask": [101.0, 102.0, 102.0],
        "mid":      [100.5, 101.5, 101.5],
        "imb1":     [1.0, 1.0, -1.0],
    })
    res = bt.run_strategy(df, "imb1", threshold=0.5, fee_bps=0.0)
    assert res["n_trades"] == 1               # only the long round-trip closed
    assert np.isclose(res["final_pnl_pts"], -0.5)


def test_load_features_collapses_flat_runs(tmp_path):
    # Rows where mid and all imbalances are unchanged carry no new info and are
    # collapsed; genuinely new rows are kept.
    csv = tmp_path / "f.csv"
    csv.write_text(
        "event_idx,ts_ns,best_bid,best_ask,bid_size,ask_size,mid,microprice,spread,imb1,imb5,imb10\n"
        "0,0,100,101,1,1,100.5,100.5,1,0.1,0.1,0.1\n"
        "1,1,100,101,1,1,100.5,100.5,1,0.1,0.1,0.1\n"   # identical -> dropped
        "2,2,101,102,1,1,101.5,101.5,1,0.2,0.2,0.2\n"   # changed -> kept
    )
    df = bt.load_features(str(csv))
    assert len(df) == 2
