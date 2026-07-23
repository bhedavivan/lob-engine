"""Generate `_hero.html`: the live dashboard (`live.html`) seeded with a real
captured order-book snapshot and the WebSocket disabled, so it renders fully
populated for a static screenshot (the README / site cover image).

Uses the committed samples, so the cover is reproducible from the repo:

    python capture_cover.py
    # then screenshot the rendered page with any headless Chromium, e.g.:
    #   msedge  --headless=new --screenshot=live_dashboard.png \
    #           --window-size=1280,720 --force-device-scale-factor=2 _hero.html
    #   chrome  --headless=new --screenshot=live_dashboard.png _hero.html

The sparkline series is derived from the depth snapshots themselves, so book,
trades, and price line all come from one session and stay consistent.
"""

import csv
import json
import pathlib
import time
from collections import defaultdict

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "data"


def load_depth(path):
    snaps = defaultdict(lambda: {"b": [], "a": []})
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            snaps[int(r["snap"])][r["side"]].append([float(r["price"]), float(r["size"])])
    return [snaps[k] for k in sorted(snaps)]


def load_trades(path, n=18):
    out = []
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("event_type") == "trade":
                    side = "buy" if r["trade_side"] == "a" else "sell"
                    out.append({"px": float(r["trade_price"]), "side": side, "t": 0})
    except FileNotFoundError:
        pass
    out = out[-n:][::-1]
    base = int(time.time() * 1000)
    for i, t in enumerate(out):
        t["t"] = base - i * 900
    return out


def main():
    depth = load_depth(DATA / "depth_sample.csv")
    snap = depth[len(depth) * 2 // 3]                # a lively mid-session frame
    bids, asks = snap["b"], snap["a"]

    # Mid per snapshot -> a consistent price line for the sparkline.
    mids = []
    for s in depth:
        if s["b"] and s["a"]:
            mids.append((max(p for p, _ in s["b"]) + min(p for p, _ in s["a"])) / 2)
    step = max(1, len(mids) // 140)
    spark = mids[::step][:140]

    trades = load_trades(DATA / "events_sample.csv")

    seed = f"""
/* --- static hero seed (no WebSocket); see capture_cover.py --- */
try{{ if(ws){{ ws.onopen=ws.onmessage=ws.onerror=ws.onclose=null; ws.close(); }} }}catch(e){{}}
ws = null; connected = false;
connect = function(){{}};
book = {{bids:new Map({json.dumps(bids)}), asks:new Map({json.dumps(asks)})}};
trades = {json.dumps(trades)};
spark = {json.dumps(spark)};
render();
setStatus("on","Coinbase · BTC live");
document.getElementById("venue").textContent = "Coinbase · BTC";
"""

    html = (HERE / "live.html").read_text(encoding="utf-8")
    anchor = "connect();  // auto-connect to the default (Coinbase · BTC) on load"
    html = html.replace(anchor, anchor + "\n" + seed)
    (HERE / "_hero.html").write_text(html, encoding="utf-8")
    print(f"wrote _hero.html  (bids {len(bids)}, asks {len(asks)}, "
          f"spark {len(spark)}, trades {len(trades)})")
    print("screenshot it with a headless Chromium to make live_dashboard.png")


if __name__ == "__main__":
    main()
