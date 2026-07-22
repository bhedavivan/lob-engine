"""Correctness tests for the exchange feed adapters.

The subtle, easy-to-invert logic here is the trade-side mapping: each exchange
reports a different "side", and the engine's CSV wants the *consumed* book side.
Coinbase reports the maker side; Kraken reports the aggressor side — so the two
adapters must map them oppositely to agree.
"""

from _helpers import load

cf = load("capture_feed_mod", "data/capture_feed.py")
Coinbase, Kraken = cf.Coinbase, cf.Kraken


# ---- Coinbase ----

def test_coinbase_snapshot():
    rows, is_snap = Coinbase.rows(
        {"type": "snapshot", "bids": [["100.0", "1.5"]], "asks": [["101.0", "2.0"]]})
    assert is_snap is True
    assert ("snapshot", "b", "100.0", "1.5") in rows
    assert ("snapshot", "a", "101.0", "2.0") in rows


def test_coinbase_l2update_sides_and_removal():
    rows, is_snap = Coinbase.rows(
        {"type": "l2update", "changes": [["buy", "100.0", "0.5"], ["sell", "101.0", "0"]]})
    assert is_snap is False
    assert rows == [("update", "b", "100.0", "0.5"), ("update", "a", "101.0", "0")]


def test_coinbase_match_maker_side_is_consumed_side():
    # Coinbase `side` is the maker side => the book side consumed.
    buy_rows, _ = Coinbase.rows({"type": "match", "side": "buy", "price": "100", "size": "0.1"})
    sell_rows, _ = Coinbase.rows({"type": "match", "side": "sell", "price": "101", "size": "0.2"})
    assert buy_rows == [("trade", "b", "100", "0.1")]   # maker bid hit -> bid consumed
    assert sell_rows == [("trade", "a", "101", "0.2")]  # maker ask lifted -> ask consumed


# ---- Kraken ----

def test_kraken_ignores_status_dicts():
    assert Kraken.rows({"event": "heartbeat"}) == ([], False)
    assert Kraken.rows({"event": "systemStatus"}) == ([], False)


def test_kraken_snapshot():
    msg = [42, {"as": [["101.0", "2.0", "t"]], "bs": [["100.0", "1.0", "t"]]},
           "book-10", "XBT/USD"]
    rows, is_snap = Kraken.rows(msg)
    assert is_snap is True
    assert ("snapshot", "b", "100.0", "1.0") in rows
    assert ("snapshot", "a", "101.0", "2.0") in rows


def test_kraken_combined_bid_ask_update_across_two_dicts():
    # Kraken can split a bid+ask update into two payload dicts; both sides must
    # survive (the earlier single-msg[1] read dropped the second dict).
    msg = [42, {"a": [["101.0", "3.0", "t"]]}, {"b": [["100.0", "0.0", "t"]]},
           "book-10", "XBT/USD"]
    rows, is_snap = Kraken.rows(msg)
    assert is_snap is False
    assert ("update", "a", "101.0", "3.0") in rows
    assert ("update", "b", "100.0", "0.0") in rows   # the removal must not be lost


def test_kraken_trade_aggressor_maps_to_opposite_consumed_side():
    # Kraken `side` is the aggressor: b (buy lifts ask) -> ask consumed;
    # s (sell hits bid) -> bid consumed. Opposite of Coinbase's maker convention.
    buy_agg = [42, [["101.0", "0.5", "t", "b", "l", ""]], "trade", "XBT/USD"]
    sell_agg = [42, [["100.0", "0.3", "t", "s", "l", ""]], "trade", "XBT/USD"]
    assert Kraken.rows(buy_agg) == ([("trade", "a", "101.0", "0.5")], False)
    assert Kraken.rows(sell_agg) == ([("trade", "b", "100.0", "0.3")], False)
