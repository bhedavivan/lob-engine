# lob-engine

A limit order book reconstruction engine in C++, fed by real market data from
a live crypto exchange. It ingests an L2 feed, rebuilds the full book in
memory, and exposes top-of-book, depth, and order-flow-imbalance signals — the
substrate a backtester or market-making strategy runs on top of.

Built as a systems project: the interesting problems here are correctness
under a high-rate incremental feed and doing the per-event book maintenance
fast enough to keep up.

```
[ Coinbase L2 WebSocket ] --> capture_feed.py --> feed.csv --> [ C++ OrderBook ] --> top-of-book / depth / imbalance
```

## Status

**v1 (current):** order book core + real-data capture + replay, tested.
Replays a captured BTC-USD feed and reports live top-of-book, spread, and
order-book imbalance. On a recent run it processed a 48k-event capture at
~40k events/sec.

See the [Roadmap](#roadmap) for what v1 deliberately does *not* do yet.

## Design decisions

- **Two ordered maps, comparator-flipped.** Bids live in a
  `std::map<double,double,std::greater<>>` and asks in a plain `std::map`, so
  best-bid and best-ask are both `begin()` — O(1) top-of-book, O(log n) level
  updates. The alternative (one hash map + a re-scan for the top) makes the
  hottest read path the slowest; a book is read at top far more than it's
  updated deep, so the tree's ordering earns its cost.
- **`size == 0` means delete.** The exchange encodes level removal as a
  zero-size update rather than a separate message type; the engine follows
  that contract so replay reconstructs the exact live book.
- **Capture and compute are separated.** Python owns the messy, I/O-bound
  network read; C++ owns the hot path. The CSV between them is a documented
  contract ([data/README.md](data/README.md)), which also makes replays
  deterministic and the engine testable without a live socket.
- **Order-flow imbalance over the top N levels** — `(bid_depth - ask_depth) /
  (bid_depth + ask_depth)` — is the one signal computed in v1, because it is
  the standard short-horizon microstructure predictor and it's the feature the
  planned ML layer will build on.

## Layout

```
engine/     C++ order book, replay CLI, unit tests (CMake)
data/       Python live-feed capture + CSV contract + a real sample
backtest/   (roadmap) strategy replay + PnL/metrics
ml/         (roadmap) order-book-imbalance mid-price direction classifier
dashboard/  (roadmap) live depth + spread + latency view
```

## Build and run

Requires a C++17 compiler and CMake. On Windows, the MSVC Build Tools
toolchain works; on Linux/macOS, g++/clang.

```bash
cd engine
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure     # unit tests

# Replay the committed real-data sample:
./build/lob_engine ../data/sample_head.csv --depth 10 --every 100
```

To replay a fresh, larger capture:

```bash
cd data
pip install -r requirements.txt
python capture_feed.py --product BTC-USD --seconds 60 --out sample.csv
cd ../engine
./build/lob_engine ../data/sample.csv --depth 10 --every 5000
```

## Roadmap

v1 is the reconstruction core. The next versions add the layers a quant
project is ultimately judged on:

- **Backtester** — replay the book against a simple market-making / momentum
  strategy, report PnL, spread capture, and inventory over time.
- **ML signal** — a short-horizon mid-price-direction classifier from
  order-book-imbalance features (gradient-boosted trees / logistic regression,
  not deep learning — honest baselines first). Trained and evaluated on
  captured book state, walk-forward.
- **Live dashboard** — a thin web view of the reconstructed book: depth chart,
  spread, and per-event processing latency.
- **Latency work** — measure per-event update cost, then attack it
  (arena-allocated price levels, a flat array for the dense near-touch region).

## Known limitations (v1)

- Single product, single feed source (Coinbase). No cross-exchange view.
- Replay is single-threaded and reads a full CSV; there's no live in-process
  socket→book path yet (capture and replay are separate steps).
- No sequence-gap handling — a real production feed needs to detect and
  recover from dropped messages; replay of a clean capture doesn't exercise
  that.

## License

MIT — see [LICENSE](LICENSE).
