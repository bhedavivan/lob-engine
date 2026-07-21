"""Build a self-contained HTML dashboard from the engine's emitted streams.

Reads the depth-ladder snapshots (`--emit-depth`), the per-event feature stream
(`--emit`), and the quote+trade stream (`--emit-events`), and writes a single
`dashboard.html` with all data inlined -- no server, no build step, no external
libraries or fonts. Open the file (or host it on GitHub Pages) and it renders:

  - an animated order-book depth ladder (scrub or play through the session),
  - mid / microprice, spread, and order-book imbalance over time, and
  - trade prints marked on the price line,

with a cursor that ties the time series to the depth snapshot on screen.

Usage:
    python build_dashboard.py --depth ../data/dash_depth.csv \
        --features ../data/dash_features.csv --events ../data/dash_events.csv \
        --out dashboard.html
"""

import argparse
import csv
import json
import sys


def read_depth(path):
    snaps = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            s = int(row["snap"])
            snap = snaps.setdefault(s, {"ts": int(row["ts_ns"]), "b": [], "a": []})
            snap[row["side"]].append([float(row["price"]), float(row["size"])])
    return [snaps[k] for k in sorted(snaps)]


def downsample(rows, target):
    if len(rows) <= target:
        return rows
    stride = len(rows) / target
    return [rows[int(i * stride)] for i in range(target)]


def read_features(path, target=1500):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows.append({
                "t": int(r["ts_ns"]),
                "mid": float(r["mid"]),
                "micro": float(r["microprice"]),
                "spread": float(r["spread"]),
                "imb1": float(r["imb1"]),
                "imb5": float(r["imb5"]),
                "imb10": float(r["imb10"]),
            })
    return downsample(rows, target)


def read_trades(path, target=400):
    trades = []
    try:
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                if r["event_type"] == "trade":
                    trades.append({"t": int(r["ts_ns"]),
                                   "p": float(r["trade_price"]),
                                   "side": r["trade_side"]})
    except FileNotFoundError:
        return []
    return downsample(trades, target)


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>lob-engine — order book dashboard</title>
<style>
  :root {
    --bg:#0d1117; --panel:#161b22; --line:#30363d; --text:#e6edf3;
    --muted:#8b949e; --bid:#3fb950; --ask:#f85149; --mid:#58a6ff; --micro:#d29922;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
    font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { padding:18px 22px; border-bottom:1px solid var(--line); }
  h1 { margin:0; font-size:18px; letter-spacing:.3px; }
  .sub { color:var(--muted); font-size:13px; margin-top:4px; }
  .wrap { display:grid; grid-template-columns:minmax(320px,1fr) minmax(360px,1.6fr);
    gap:16px; padding:16px; max-width:1200px; margin:0 auto; }
  @media (max-width:820px){ .wrap{ grid-template-columns:1fr; } }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }
  .panel h2 { margin:0 0 10px; font-size:12px; text-transform:uppercase;
    letter-spacing:.8px; color:var(--muted); font-weight:600; }
  .stack > .panel { margin-bottom:16px; }
  .controls { display:flex; align-items:center; gap:12px; padding:6px 16px 16px; max-width:1200px; margin:0 auto; }
  button { background:#21262d; color:var(--text); border:1px solid var(--line);
    border-radius:6px; padding:6px 14px; cursor:pointer; font:inherit; }
  button:hover { border-color:var(--mid); }
  input[type=range]{ flex:1; accent-color:var(--mid); }
  .readout { font-size:12px; color:var(--muted); }
  .readout b { color:var(--text); }
  .legend { font-size:11px; color:var(--muted); margin-top:6px; }
  .legend i { font-style:normal; padding-right:12px; }
  .sw { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:4px; vertical-align:middle; }
  text { fill:var(--muted); font-size:10px; }
</style>
</head>
<body>
<header>
  <h1>lob-engine — live order book dashboard</h1>
  <div class="sub" id="sub"></div>
</header>

<div class="controls">
  <button id="play">▶ Play</button>
  <input type="range" id="scrub" min="0" value="0">
  <div class="readout" id="clock"></div>
</div>

<div class="wrap">
  <div class="panel">
    <h2>Order book depth</h2>
    <svg id="depth" viewBox="0 0 360 420" width="100%"></svg>
    <div class="legend"><i><span class="sw" style="background:var(--bid)"></span>bids</i>
      <i><span class="sw" style="background:var(--ask)"></span>asks</i></div>
  </div>
  <div class="stack">
    <div class="panel"><h2>Mid &amp; microprice (· trades)</h2>
      <svg id="price" viewBox="0 0 640 200" width="100%"></svg>
      <div class="legend"><i><span class="sw" style="background:var(--mid)"></span>mid</i>
        <i><span class="sw" style="background:var(--micro)"></span>microprice</i></div>
    </div>
    <div class="panel"><h2>Spread</h2>
      <svg id="spread" viewBox="0 0 640 120" width="100%"></svg></div>
    <div class="panel"><h2>Order-book imbalance (depth 1 / 5 / 10)</h2>
      <svg id="imb" viewBox="0 0 640 140" width="100%"></svg></div>
  </div>
</div>

<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById("data").textContent);
const SVGNS = "http://www.w3.org/2000/svg";
const el = (t,a)=>{const e=document.createElementNS(SVGNS,t);for(const k in a)e.setAttribute(k,a[k]);return e;};
const ext = (arr,f)=>{let lo=Infinity,hi=-Infinity;for(const v of arr){const x=f(v);if(x<lo)lo=x;if(x>hi)hi=x;}return[lo,hi];};

document.getElementById("sub").textContent =
  `${D.product} · real Coinbase L2 + trades · ${D.n_events.toLocaleString()} book events · `
  + `${D.depth.length} depth snapshots · ${D.trades.length} trades shown`;

// ---- time-series charts (drawn once) ----
const F = D.features;
const tExt = ext(F, d=>d.t);
const tx = t => 40 + (t - tExt[0]) / (tExt[1]-tExt[0] || 1) * (640-40-8);

function line(svg, key, color, h, ypad=14){
  const g = document.getElementById(svg);
  const [lo,hi] = ext(F, d=>d[key]);
  const yy = v => (h-ypad) - (v-lo)/((hi-lo)||1) * (h-2*ypad);
  let dstr = "";
  F.forEach((d,i)=>{ dstr += (i?"L":"M") + tx(d.t).toFixed(1) + " " + yy(d[key]).toFixed(1) + " "; });
  g.appendChild(el("path",{d:dstr,fill:"none",stroke:color,"stroke-width":1.3}));
  return {g,yy,lo,hi};
}

// price panel: mid + microprice + trades
const P = document.getElementById("price");
const pLo = ext(F, d=>Math.min(d.mid,d.micro))[0], pHi = ext(F, d=>Math.max(d.mid,d.micro))[1];
const py = v => (200-16) - (v-pLo)/((pHi-pLo)||1) * (200-32);
function path(svg,key,color){ let s=""; F.forEach((d,i)=>{ s+=(i?"L":"M")+tx(d.t).toFixed(1)+" "+py(d[key]).toFixed(1)+" ";}); svg.appendChild(el("path",{d:s,fill:"none",stroke:color,"stroke-width":1.3})); }
path(P,"mid","#58a6ff"); path(P,"micro","#d29922");
for(const tr of D.trades){ if(tr.t<tExt[0]||tr.t>tExt[1])continue;
  P.appendChild(el("circle",{cx:tx(tr.t),cy:py(tr.p),r:1.6,fill:tr.side==="a"?"#f85149":"#3fb950","fill-opacity":.7})); }
[pLo,pHi].forEach((v,i)=>P.appendChild(el("text",{x:2,y:i?194:12},)).append(v.toFixed(2)));

line("spread","spread","#a371f7",120);
// imbalance panel with three series + zero line
const I = document.getElementById("imb");
const iy = v => 70 - v*56;                 // imbalance in [-1,1] -> vertical
I.appendChild(el("line",{x1:40,y1:iy(0),x2:632,y2:iy(0),stroke:"#30363d","stroke-width":1}));
for(const [key,c] of [["imb10","#484f58"],["imb5","#6e7681"],["imb1","#58a6ff"]]){
  let s=""; F.forEach((d,i)=>{ s+=(i?"L":"M")+tx(d.t).toFixed(1)+" "+iy(d[key]).toFixed(1)+" ";});
  I.appendChild(el("path",{d:s,fill:"none",stroke:c,"stroke-width":1.2}));
}

// cursor lines across the three time charts
const cursors = ["price","spread","imb"].map(id=>{
  const svg=document.getElementById(id); const h=+svg.getAttribute("viewBox").split(" ")[3];
  const c=el("line",{y1:0,y2:h,stroke:"#e6edf3","stroke-width":1,"stroke-opacity":.35}); svg.appendChild(c); return c;
});

// ---- depth ladder (redrawn per frame) ----
const DEPTH = document.getElementById("depth");
const maxSize = Math.max(...D.depth.flatMap(s=>[...s.b,...s.a].map(l=>l[1])));
function drawDepth(idx){
  const s = D.depth[idx]; DEPTH.innerHTML="";
  const W=360,H=420,mid=W/2, rows=Math.max(s.b.length,s.a.length), rh=Math.min(15,(H-30)/(rows||1));
  const bw = v => Math.max(1, v/maxSize * (mid-6));
  DEPTH.appendChild(el("line",{x1:mid,y1:0,x2:mid,y2:H-18,stroke:"#30363d"}));
  s.b.forEach((l,k)=>{ const y=6+k*rh; DEPTH.appendChild(el("rect",{x:mid-bw(l[1]),y:y,width:bw(l[1]),height:rh-2,fill:"var(--bid)","fill-opacity":.75}));
    DEPTH.append(Object.assign(el("text",{x:4,y:y+rh-3}),{textContent:l[0].toFixed(2)})); });
  s.a.forEach((l,k)=>{ const y=6+k*rh; DEPTH.appendChild(el("rect",{x:mid,y:y,width:bw(l[1]),height:rh-2,fill:"var(--ask)","fill-opacity":.75}));
    DEPTH.append(Object.assign(el("text",{x:W-40,y:y+rh-3}),{textContent:l[0].toFixed(2)})); });
  // cursor position from this snapshot's timestamp
  const x = tx(s.ts);
  cursors.forEach(c=>{c.setAttribute("x1",x);c.setAttribute("x2",x);});
  const best_b=s.b.length?s.b[0][0]:0, best_a=s.a.length?s.a[0][0]:0;
  document.getElementById("clock").innerHTML =
    `snapshot <b>${idx+1}/${D.depth.length}</b> &nbsp; bid <b>${best_b.toFixed(2)}</b> · ask <b>${best_a.toFixed(2)}</b> · spread <b>${(best_a-best_b).toFixed(2)}</b>`;
}

// ---- controls ----
const scrub=document.getElementById("scrub"); scrub.max=D.depth.length-1;
scrub.addEventListener("input",()=>drawDepth(+scrub.value));
let timer=null;
document.getElementById("play").addEventListener("click",e=>{
  if(timer){clearInterval(timer);timer=null;e.target.textContent="▶ Play";return;}
  e.target.textContent="⏸ Pause";
  timer=setInterval(()=>{ let v=(+scrub.value+1)%D.depth.length; scrub.value=v; drawDepth(v);
    if(v===D.depth.length-1){} },120);
});
drawDepth(0);
</script>
</body>
</html>
"""


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--depth", required=True)
    p.add_argument("--features", required=True)
    p.add_argument("--events", default=None)
    p.add_argument("--product", default="BTC-USD")
    p.add_argument("--out", default="dashboard.html")
    args = p.parse_args()

    payload = {
        "product": args.product,
        "depth": read_depth(args.depth),
        "features": read_features(args.features),
        "trades": read_trades(args.events) if args.events else [],
    }
    payload["n_events"] = len(open(args.features).readlines()) - 1

    html = HTML.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    kb = len(html.encode("utf-8")) / 1024
    print(f"wrote {args.out} ({kb:.0f} KB): {len(payload['depth'])} snapshots, "
          f"{len(payload['features'])} series points, {len(payload['trades'])} trades",
          file=sys.stderr)


if __name__ == "__main__":
    main()
