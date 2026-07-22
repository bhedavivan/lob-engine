"""Correctness tests for the passive market-maker's queue-fill accounting."""

import numpy as np
import pandas as pd
from _helpers import load

mm = load("market_maker_mod", "backtest/market_maker.py")


def test_resting_bid_fills_and_captures_half_spread():
    # Quote at a touch with an empty bid queue (queue_ahead = 0, so the MM is
    # first in line), then a sell trade of size 2 hits the bid. The MM buys its
    # full unit at 100 while the mid is 100.5, so it ends long 1 with +0.5 PnL.
    df = pd.DataFrame({
        "event_type": ["quote", "trade"],
        "best_bid":   [100.0, 100.0],
        "best_ask":   [101.0, 101.0],
        "bid_size":   [0.0, 0.0],
        "ask_size":   [5.0, 5.0],
        "trade_price": [0.0, 100.0],
        "trade_size":  [0.0, 2.0],
        "trade_side":  ["", "b"],       # a bid-consuming trade
    })
    r = mm.run(df, qty=1.0, inv_cap=5.0, maker_fee_bps=0.0)
    assert r["bid_fills"] == 1
    assert r["ask_fills"] == 0
    assert np.isclose(r["final_inventory"], 1.0)
    assert np.isclose(r["total_pnl_pts"], 0.5)      # half-spread captured
    assert r["spread_pnl_bps"] > 0.0


def test_queue_ahead_blocks_the_fill():
    # Same trade, but now 10 units rest ahead of the MM at the bid. A size-2
    # trade only eats into that queue and never reaches the MM -> no fill.
    df = pd.DataFrame({
        "event_type": ["quote", "trade"],
        "best_bid":   [100.0, 100.0],
        "best_ask":   [101.0, 101.0],
        "bid_size":   [10.0, 10.0],
        "ask_size":   [5.0, 5.0],
        "trade_price": [0.0, 100.0],
        "trade_size":  [0.0, 2.0],
        "trade_side":  ["", "b"],
    })
    r = mm.run(df, qty=1.0, inv_cap=5.0, maker_fee_bps=0.0)
    assert r["bid_fills"] == 0
    assert np.isclose(r["final_inventory"], 0.0)
