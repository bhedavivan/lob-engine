"""Capture a live L2 order book feed from a public exchange WebSocket and write
it to the CSV contract the C++ engine replays (see data/README.md).

Supports multiple exchanges behind one CSV format, so the same engine, backtests
and dashboard work regardless of source — and you can capture the same asset
from two venues to cross-check prices. No API key required; these are all
unauthenticated public market-data feeds.

  --exchange coinbase | kraken     (default coinbase)
  --symbol   BTC-USD | ETH-USD | SOL-USD | XRP-USD | LTC-USD | DOGE-USD

Usage:
    python capture_feed.py --exchange kraken --symbol ETH-USD --seconds 60 --out eth.csv
    python capture_feed.py --exchange coinbase --stream | ../engine/build/lob_engine -
"""

import argparse
import csv
import json
import sys
import time

# Canonical symbol -> per-exchange symbol.
SYMBOLS = {
    "BTC-USD": {"coinbase": "BTC-USD", "kraken": "XBT/USD"},
    "ETH-USD": {"coinbase": "ETH-USD", "kraken": "ETH/USD"},
    "SOL-USD": {"coinbase": "SOL-USD", "kraken": "SOL/USD"},
    "XRP-USD": {"coinbase": "XRP-USD", "kraken": "XRP/USD"},
    "LTC-USD": {"coinbase": "LTC-USD", "kraken": "LTC/USD"},
    "DOGE-USD": {"coinbase": "DOGE-USD", "kraken": "XDG/USD"},
}


# Each adapter turns one exchange's messages into normalized CSV rows:
# (type, side, price, size) — the engine's contract. `type` is snapshot/update/
# trade; `side` on a book row is b/a, and on a trade row is the *consumed* book
# side. ts_ns is stamped by the caller.

class Coinbase:
    url = "wss://ws-feed.exchange.coinbase.com"

    @staticmethod
    def subscribe(sym):
        return [{"type": "subscribe", "product_ids": [sym],
                 "channels": ["level2_batch", "matches"]}]

    @staticmethod
    def rows(msg):
        t = msg.get("type")
        if t == "snapshot":
            out = [("snapshot", "b", p, s) for p, s in msg.get("bids", [])]
            out += [("snapshot", "a", p, s) for p, s in msg.get("asks", [])]
            return out, True
        if t == "l2update":
            return ([("update", "b" if side == "buy" else "a", p, s)
                     for side, p, s in msg.get("changes", [])], False)
        if t in ("match", "last_match"):
            # Coinbase `side` is the *maker* side => the book side consumed.
            consumed = "b" if msg.get("side") == "buy" else "a"
            return [("trade", consumed, msg.get("price"), msg.get("size"))], False
        if t == "error":
            print(f"feed error: {msg.get('message')}", file=sys.stderr)
        return [], False


class Kraken:
    url = "wss://ws.kraken.com"

    @staticmethod
    def subscribe(sym):
        return [
            {"event": "subscribe", "pair": [sym], "subscription": {"name": "book", "depth": 25}},
            {"event": "subscribe", "pair": [sym], "subscription": {"name": "trade"}},
        ]

    @staticmethod
    def rows(msg):
        # Data messages are arrays: [channelID, payload, channelName, pair].
        # Everything else (heartbeat, systemStatus, subscriptionStatus) is a dict.
        if not isinstance(msg, list):
            if isinstance(msg, dict) and msg.get("errorMessage"):
                print(f"feed error: {msg['errorMessage']}", file=sys.stderr)
            return [], False
        channel = msg[-2]
        if isinstance(channel, str) and channel.startswith("book"):
            out, is_snap = [], False
            # A combined bid+ask update can arrive as two separate payload dicts
            # between the channel id and the channel name, so merge them all
            # rather than reading only msg[1].
            for payload in msg[1:-2]:
                if not isinstance(payload, dict):
                    continue
                if "as" in payload or "bs" in payload:  # snapshot
                    is_snap = True
                    out += [("snapshot", "b", e[0], e[1]) for e in payload.get("bs", [])]
                    out += [("snapshot", "a", e[0], e[1]) for e in payload.get("as", [])]
                out += [("update", "b", e[0], e[1]) for e in payload.get("b", [])]
                out += [("update", "a", e[0], e[1]) for e in payload.get("a", [])]
            return out, is_snap
        if channel == "trade":
            # Kraken trade `side` is the *aggressor*: b => a buy lifted the ask
            # (ask consumed), s => a sell hit the bid (bid consumed).
            return ([("trade", "a" if t[3] == "b" else "b", t[0], t[1]) for t in msg[1]], False)
        return [], False


ADAPTERS = {"coinbase": Coinbase, "kraken": Kraken}


def capture(exchange: str, symbol: str, seconds: float, out_path: str, stream: bool) -> None:
    # Imported lazily so the pure adapter classes (Coinbase/Kraken) can be
    # imported and unit-tested without the network dependency installed.
    try:
        from websocket import create_connection  # websocket-client
    except ImportError:
        sys.exit("websocket-client is required: pip install websocket-client "
                 "(see data/requirements.txt)")

    adapter = ADAPTERS[exchange]
    ex_symbol = SYMBOLS[symbol][exchange]

    ws = create_connection(adapter.url, timeout=15)
    for sub in adapter.subscribe(ex_symbol):
        ws.send(json.dumps(sub))

    rows_written = trade_rows = 0
    snapshot_seen = False
    deadline = time.time() + seconds

    # --stream sends the CSV to stdout (for `| lob_engine -`); status → stderr.
    sink = sys.stdout if stream else open(out_path, "w", newline="")
    try:
        writer = csv.writer(sink)
        writer.writerow(["type", "side", "price", "size", "ts_ns"])
        while time.time() < deadline:
            try:
                raw = ws.recv()
            except Exception as exc:  # noqa: BLE001 - network read, log and stop
                print(f"recv error: {exc}", file=sys.stderr)
                break
            if not raw:
                continue
            rows, is_snap = adapter.rows(json.loads(raw))
            snapshot_seen = snapshot_seen or is_snap
            ts_ns = time.time_ns()
            for typ, side, price, size in rows:
                writer.writerow([typ, side, price, size, ts_ns])
                rows_written += 1
                if typ == "trade":
                    trade_rows += 1
            if stream:
                sink.flush()
    finally:
        if not stream:
            sink.close()

    ws.close()
    if not snapshot_seen:
        print("warning: no snapshot received", file=sys.stderr)
    dest = "stdout" if stream else out_path
    print(f"{exchange} {symbol}: wrote {rows_written} rows to {dest} ({trade_rows} trades)",
          file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange", default="coinbase", choices=sorted(ADAPTERS))
    parser.add_argument("--symbol", default="BTC-USD", choices=sorted(SYMBOLS),
                        help="canonical symbol; translated per exchange")
    parser.add_argument("--product", help="deprecated alias for --symbol")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--out", default="sample.csv")
    parser.add_argument("--stream", action="store_true",
                        help="write the feed to stdout for piping into `lob_engine -`")
    args = parser.parse_args()
    symbol = args.product or args.symbol
    capture(args.exchange, symbol, args.seconds, args.out, args.stream)


if __name__ == "__main__":
    main()
