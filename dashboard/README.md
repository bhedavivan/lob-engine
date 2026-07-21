# Dashboard — order book, live-replayed

A single self-contained HTML page that visualizes a real captured session: an
animated order-book **depth ladder**, **mid / microprice** with trade prints,
the **spread**, and the **order-book imbalance** signal — with a cursor tying
the time series to the depth snapshot on screen. No server, no build step, no
external libraries, fonts, or network calls; all data is inlined, so it opens
straight from disk or GitHub Pages.

![dashboard preview](dashboard_preview.png)

*(Static poster above; `dashboard.html` is the interactive version — press Play
or drag the slider to scrub through the session.)*

## How it's built

The C++ engine emits three streams from one replay:

- `--emit-depth` — periodic top-N depth ladders (the animated book),
- `--emit` — the per-event feature series (mid, microprice, spread, imbalance),
- `--emit-events` — quote+trade stream (the trade markers).

`build_dashboard.py` reads those, downsamples the time series, inlines
everything as JSON, and writes `dashboard.html`. It's deliberately a *rendering*
step in Python with a *pure-C++* data path — the engine does the reconstruction,
the dashboard just draws it.

## Run it

```bash
# 1) capture, then emit all three streams in one replay:
python ../data/capture_feed.py --product BTC-USD --seconds 180 --out ../data/feed.csv
../engine/build/lob_engine ../data/feed.csv \
    --emit ../data/feat.csv --emit-events ../data/ev.csv \
    --emit-depth ../data/depth.csv --depth-every 400 --depth-levels 12

# 2) build the page (open the result in any browser):
python build_dashboard.py --depth ../data/depth.csv \
    --features ../data/feat.csv --events ../data/ev.csv --out dashboard.html

# static PNG poster (what the README shows):
python preview.py --depth ../data/depth.csv \
    --features ../data/feat.csv --events ../data/ev.csv --out dashboard_preview.png
```

`preview.py` needs `matplotlib`; `build_dashboard.py` is pure standard library.
A committed `dashboard.html` is included so the interactive view is viewable
without capturing anything, and `../data/depth_sample.csv` is a real depth
sample for quick experiments.
