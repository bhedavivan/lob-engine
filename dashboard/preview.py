"""Render a static PNG poster of the dashboard from the same emitted streams.

The interactive view is `dashboard.html`; this is a still image of the same real
data for the README, so the project shows a visual without the reader running
anything. Same inputs as build_dashboard.py.

Usage:
    python preview.py --depth ../data/dash_depth.csv \
        --features ../data/dash_features.csv --events ../data/dash_events.csv \
        --out dashboard_preview.png
"""

import argparse
import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def read_depth(path):
    snaps = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            s = int(r["snap"])
            d = snaps.setdefault(s, {"b": [], "a": []})
            d[r["side"]].append((float(r["price"]), float(r["size"])))
    return [snaps[k] for k in sorted(snaps)]


def read_cols(path, cols):
    out = {c: [] for c in cols}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            for c in cols:
                out[c].append(float(r[c]))
    return out


def read_trades(path):
    xs, ys, cs = [], [], []
    try:
        with open(path, newline="") as f:
            for i, r in enumerate(csv.DictReader(f)):
                if r["event_type"] == "trade":
                    xs.append(i)
                    ys.append(float(r["trade_price"]))
                    cs.append("#f85149" if r["trade_side"] == "a" else "#3fb950")
    except FileNotFoundError:
        pass
    return xs, ys, cs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--depth", required=True)
    p.add_argument("--features", required=True)
    p.add_argument("--events", default=None)
    p.add_argument("--out", default="dashboard_preview.png")
    args = p.parse_args()

    depth = read_depth(args.depth)
    snap = depth[len(depth) // 2]                 # a mid-session ladder
    feats = read_cols(args.features, ["mid", "microprice", "spread", "imb1", "imb5", "imb10"])
    n = len(feats["mid"])
    x = list(range(n))

    plt.rcParams.update({
        "figure.facecolor": "#0d1117", "axes.facecolor": "#161b22",
        "axes.edgecolor": "#30363d", "text.color": "#e6edf3",
        "axes.labelcolor": "#8b949e", "xtick.color": "#8b949e",
        "ytick.color": "#8b949e", "axes.titlecolor": "#e6edf3", "font.size": 9,
    })
    fig = plt.figure(figsize=(12, 5.2))
    gs = fig.add_gridspec(3, 2, width_ratios=[1, 1.7], hspace=0.55, wspace=0.2)

    # Depth ladder
    axd = fig.add_subplot(gs[:, 0])
    bp = [l[0] for l in snap["b"]]; bs = [l[1] for l in snap["b"]]
    ap = [l[0] for l in snap["a"]]; as_ = [l[1] for l in snap["a"]]
    axd.barh(bp, [-v for v in bs], color="#3fb950", alpha=0.8, height=0.9,
             label="bids")
    axd.barh(ap, as_, color="#f85149", alpha=0.8, height=0.9, label="asks")
    axd.axhline((bp[0] + ap[0]) / 2, color="#8b949e", lw=0.6, ls="--")
    axd.set_title("Order book depth (mid-session snapshot)")
    axd.set_xlabel("size  (bids ◂ | ▸ asks)"); axd.legend(loc="upper right", framealpha=0.2)

    # Price + trades
    axp = fig.add_subplot(gs[0, 1])
    axp.plot(x, feats["mid"], color="#58a6ff", lw=1.1, label="mid")
    axp.plot(x, feats["microprice"], color="#d29922", lw=0.9, label="microprice")
    tx, ty, tc = read_trades(args.events) if args.events else ([], [], [])
    if tx:
        sc = max(1, n // 1)  # trades indexed by their own row; rescale to feature x
        txr = [t * n / (max(tx) + 1) for t in tx]
        axp.scatter(txr, ty, s=3, c=tc, alpha=0.5, zorder=3)
    axp.set_title("Mid & microprice  (· trades)"); axp.legend(loc="upper left", framealpha=0.2)
    axp.margins(x=0)

    # Imbalance
    axi = fig.add_subplot(gs[1, 1])
    axi.axhline(0, color="#30363d", lw=1)
    axi.plot(x, feats["imb10"], color="#484f58", lw=0.8, label="imb10")
    axi.plot(x, feats["imb5"], color="#6e7681", lw=0.8, label="imb5")
    axi.plot(x, feats["imb1"], color="#58a6ff", lw=0.9, label="imb1")
    axi.set_title("Order-book imbalance (depth 1 / 5 / 10)")
    axi.set_ylim(-1, 1); axi.legend(loc="upper right", ncol=3, framealpha=0.2); axi.margins(x=0)

    # Spread
    axs = fig.add_subplot(gs[2, 1])
    axs.fill_between(x, feats["spread"], color="#a371f7", alpha=0.5)
    axs.set_title("Spread"); axs.set_xlabel("event"); axs.margins(x=0)

    fig.suptitle("lob-engine — order book dashboard (real BTC-USD data)",
                 x=0.5, y=0.99, fontsize=12, color="#e6edf3")
    fig.savefig(args.out, dpi=120, bbox_inches="tight", facecolor="#0d1117")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
