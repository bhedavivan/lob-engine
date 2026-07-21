# Data — capture and CSV contract

`capture_feed.py` records a live feed from a public exchange WebSocket into a
flat CSV that the C++ engine replays. No API key required. Two exchanges are
supported behind the one CSV format via `--exchange`:

- **`coinbase`** (default) — `level2_batch` book + `matches` trades.
- **`kraken`** — `book` (depth 25) + `trade`.

`--symbol` takes a canonical name (`BTC-USD`, `ETH-USD`, `SOL-USD`, `XRP-USD`,
`LTC-USD`, `DOGE-USD`) and translates it per exchange (e.g. Kraken's `XBT/USD`).
Capturing the same symbol from both venues is a quick way to cross-check prices.
The live browser dashboard ([`dashboard/live.html`](../dashboard/)) adds
Binance.US as a third source; Binance's partial-depth stream suits the live
viewer but not the incremental C++ pipeline, so the capturer sticks to the two
venues that stream true snapshot+delta books.

## CSV schema

One row per event. Header row required.

| Column | Meaning |
|---|---|
| `type` | `snapshot` (initial book level), `update` (incremental book change), or `trade` (a print) |
| `side` | For book rows: `b` = bid, `a` = ask. For `trade` rows: the book side the trade **consumed** (`b` = a resting bid was hit, `a` = a resting ask was lifted). |
| `price` | Price level (book rows) or trade price (`trade` rows) |
| `size` | Resting size (book rows; **`0` on an `update` removes the level**) or traded size (`trade` rows) |
| `ts_ns` | Local receive timestamp, nanoseconds since epoch |

A capture opens with a burst of `snapshot` rows (the full book at connect
time), then a stream of interleaved `update` and `trade` rows in receive order.
Replaying the file rebuilds the exact book the engine saw live; `trade` rows do
not mutate the book (the matching level changes arrive as their own `update`
rows) — they exist so the market-making backtest can model passive fills
against real trades.

## Capture a sample

```bash
pip install -r requirements.txt
python capture_feed.py --exchange coinbase --symbol BTC-USD --seconds 60 --out sample.csv
python capture_feed.py --exchange kraken   --symbol ETH-USD --seconds 60 --out eth.csv
```

`sample_head.csv` (committed) is the first 400 rows of a real BTC-USD capture,
so the engine is runnable out of the box without a live connection. Full
captures are git-ignored — regenerate them locally.

## Live streaming

With `--stream`, the capturer writes the feed to stdout (status text goes to
stderr) so it can be piped straight into the engine, which reads the feed from
stdin when given `-` as the path:

```bash
python capture_feed.py --stream | ../engine/build/lob_engine - --every 500
```

The book is then reconstructed live from the exchange with no intermediate file.
