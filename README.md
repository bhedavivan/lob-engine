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

- **v1 — order book core** (done): real-data capture + replay, unit-tested.
  Reconstructs the L2 book and reports live top-of-book, spread, and imbalance;
  processed a 48k-event BTC-USD capture at ~40k events/sec.
- **v2 — signal backtester** (done): the C++ engine emits a per-event
  microstructure feature stream that a Python research layer backtests. First
  result below.

See the [Roadmap](#roadmap) for what these versions deliberately do *not* do yet.

## First result: does order-book imbalance predict price?

Short answer — **yes as a signal, no as a naive trade.** On a real ~90-second
BTC-USD capture, imbalance predicts the next mid move well above baseline, and
the edge is **strongest at the touch and decays with depth**:

| Signal | Directional accuracy | Baseline | Edge |
|---|---|---|---|
| `imb1` (touch) | **73.6%** | 59.9% | **+13.7 pts** |
| `imb5` | 69.1% | 59.9% | +9.2 pts |
| `imb10` | 64.9% | 59.9% | +5.0 pts |

But a taker rule that crosses the spread on every signal barely breaks even
gross of fees and loses badly once any realistic fee applies — the cost of
liquidity eats the edge. That gap (predictive ≠ tradable) is the point, and it
motivates the passive market-making build next. Full method, equity curve, and
honest caveats: [backtest/README.md](backtest/README.md).

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
  (bid_depth + ask_depth)` — is the headline signal, because it is the standard
  short-horizon microstructure predictor and it's the feature the ML layer will
  build on.
- **Fast C++ feature generation, Python research layer.** The hot path (book
  reconstruction + microstructure features like the size-weighted microprice)
  is C++; the backtest, metrics, and plotting are Python. That split mirrors a
  real quant stack and keeps each side in its best tool — and the feature CSV
  between them makes the research fully reproducible.
- **Signal quality is measured apart from tradability.** The backtester reports
  cost-free predictive accuracy separately from post-cost PnL, because
  conflating them is how backtests lie to you.

## Layout

```
engine/     C++ order book, replay CLI, feature emit, unit tests (CMake)
data/       Python live-feed capture + CSV contract + real samples
backtest/   Python imbalance-signal backtester + metrics + equity curve
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

Backtest the imbalance signal on the committed feature sample:

```bash
cd backtest
pip install -r requirements.txt
python backtest.py ../data/features_sample.csv --signal imb1 --horizon 50 --threshold 0.30
```

Full pipeline on a fresh capture:

```bash
python data/capture_feed.py --product BTC-USD --seconds 90 --out data/feed.csv
./engine/build/lob_engine data/feed.csv --emit data/features.csv   # feature stream
python backtest/backtest.py data/features.csv --signal imb1 --plot equity.png
```

## Roadmap

Done: the reconstruction core (v1) and the imbalance signal backtester (v2).
Next, in order:

- **Passive market-making backtest** — the direct follow-on from the v2 result:
  earn the spread by quoting passively instead of paying it by crossing. Needs
  the capturer to also record trade prints (Coinbase `matches` channel) so
  fills can be modelled honestly against real trades.
- **ML signal** — a short-horizon mid-price-direction classifier on the same
  emitted features (gradient-boosted trees / logistic regression, not deep
  learning — honest baselines first), evaluated walk-forward so the ML result
  is comparable to the threshold baseline already measured.
- **Live dashboard** — a thin web view of the reconstructed book: depth chart,
  spread, and per-event processing latency.
- **Latency work** — measure per-event update cost, then attack it
  (arena-allocated price levels, a flat array for the dense near-touch region).

## Known limitations

- Single product, single feed source (Coinbase). No cross-exchange view.
- Replay is single-threaded and reads a full CSV; there's no live in-process
  socket→book path yet (capture and replay are separate steps).
- No sequence-gap handling — a real production feed needs to detect and
  recover from dropped messages; replay of a clean capture doesn't exercise
  that.
- The backtest is single-session and uses overlapping return windows; see
  [backtest/README.md](backtest/README.md#honest-caveats) for why the accuracy
  figures are descriptive rather than statistically significant.

## License

MIT — see [LICENSE](LICENSE).
