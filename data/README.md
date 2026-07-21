# Data — capture and CSV contract

`capture_feed.py` records a live feed from Coinbase's public market-data
WebSocket into a flat CSV that the C++ engine replays. It subscribes to two
channels (no API key required): `level2_batch` for the order book and `matches`
for trade prints.

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
python capture_feed.py --product BTC-USD --seconds 60 --out sample.csv
```

`sample_head.csv` (committed) is the first 400 rows of a real BTC-USD capture,
so the engine is runnable out of the box without a live connection. Full
captures are git-ignored — regenerate them locally.
