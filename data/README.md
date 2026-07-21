# Data — capture and CSV contract

`capture_feed.py` records a live L2 order book feed from Coinbase's public
market-data WebSocket (`level2_batch` channel, no API key required) into a
flat CSV that the C++ engine replays.

## CSV schema

One row per book event. Header row required.

| Column | Meaning |
|---|---|
| `type` | `snapshot` (initial book level) or `update` (incremental change) |
| `side` | `b` = bid, `a` = ask |
| `price` | Price level |
| `size` | Resting size at that price. **`0` on an `update` removes the level.** |
| `ts_ns` | Local receive timestamp, nanoseconds since epoch |

A capture opens with a burst of `snapshot` rows (the full book at connect
time), followed by a stream of `update` rows. Replaying the file rebuilds the
exact book state the engine saw live.

## Capture a sample

```bash
pip install -r requirements.txt
python capture_feed.py --product BTC-USD --seconds 60 --out sample.csv
```

`sample_head.csv` (committed) is the first 400 rows of a real BTC-USD capture,
so the engine is runnable out of the box without a live connection. Full
captures are git-ignored — regenerate them locally.
