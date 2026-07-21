"""Capture a live L2 order book feed from Coinbase's public market-data
WebSocket and write it to the CSV contract the C++ engine replays.

No API key required: the `level2_batch` channel on the public feed is
unauthenticated market data. See data/README.md for the CSV schema.

Usage:
    python capture_feed.py --product BTC-USD --seconds 60 --out sample.csv
"""

import argparse
import csv
import json
import sys
import time

try:
    from websocket import create_connection  # websocket-client
except ImportError:
    sys.exit(
        "websocket-client is required: pip install websocket-client\n"
        "(see data/requirements.txt)"
    )

FEED_URL = "wss://ws-feed.exchange.coinbase.com"


def side_code(coinbase_side: str) -> str:
    return "b" if coinbase_side == "buy" else "a"


def capture(product: str, seconds: float, out_path: str) -> None:
    ws = create_connection(FEED_URL, timeout=15)
    subscribe = {
        "type": "subscribe",
        "product_ids": [product],
        # level2_batch = the order book; matches = trade prints, needed to
        # model passive fills honestly in the market-making backtest.
        "channels": ["level2_batch", "matches"],
    }
    ws.send(json.dumps(subscribe))

    rows_written = 0
    trade_rows = 0
    snapshot_seen = False
    deadline = time.time() + seconds

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["type", "side", "price", "size", "ts_ns"])

        while time.time() < deadline:
            try:
                raw = ws.recv()
            except Exception as exc:  # noqa: BLE001 - network read, log and stop
                print(f"recv error: {exc}", file=sys.stderr)
                break
            if not raw:
                continue

            msg = json.loads(raw)
            mtype = msg.get("type")
            ts_ns = time.time_ns()

            if mtype == "snapshot":
                snapshot_seen = True
                for price, size in msg.get("bids", []):
                    writer.writerow(["snapshot", "b", price, size, ts_ns])
                    rows_written += 1
                for price, size in msg.get("asks", []):
                    writer.writerow(["snapshot", "a", price, size, ts_ns])
                    rows_written += 1
            elif mtype == "l2update":
                for change in msg.get("changes", []):
                    cb_side, price, size = change
                    writer.writerow(
                        ["update", side_code(cb_side), price, size, ts_ns]
                    )
                    rows_written += 1
            elif mtype in ("match", "last_match"):
                # A trade. Coinbase's `side` is the *maker* side, so it names
                # the book side that was consumed: maker "buy" => a resting bid
                # was hit (bid liquidity consumed); maker "sell" => a resting
                # ask was lifted. We store that consumed side so the MM sim can
                # tell which of its quotes would have filled.
                consumed = "b" if msg.get("side") == "buy" else "a"
                writer.writerow(
                    ["trade", consumed, msg.get("price"), msg.get("size"), ts_ns]
                )
                rows_written += 1
                trade_rows += 1
            elif mtype == "error":
                print(f"feed error: {msg.get('message')}", file=sys.stderr)
                break

    ws.close()
    if not snapshot_seen:
        print("warning: no snapshot received", file=sys.stderr)
    print(f"wrote {rows_written} rows to {out_path} ({trade_rows} trades)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", default="BTC-USD")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--out", default="sample.csv")
    args = parser.parse_args()
    capture(args.product, args.seconds, args.out)


if __name__ == "__main__":
    main()
