"""Passive market-making backtest on the unified quote+trade event stream
(`lob_engine --emit-events`).

The v2 taker backtest showed that *paying* the spread to act on a signal loses
money. This is the other side of that coin: *earning* the spread by quoting
passively. The MM posts a resting bid and ask at the touch and gets filled only
when real trades come to it. The honest question is whether the spread it earns
survives adverse selection — the tendency to get filled precisely on the side
the price is about to move against.

Fill model (queue position, driven by real trades):
  - When the MM posts at a price level, it joins the *back* of that level's
    queue: `queue_ahead` = the size already resting there.
  - Each real trade that consumes that side at that price first eats the queue
    ahead; only the overflow fills the MM. So being at the touch is necessary
    but not sufficient — you still have to outlast the queue. This is the main
    realism a naive "filled if price is touched" model misses.
  - Queue position is advanced only by trades, not by cancels ahead (we don't
    have order-level data), which makes fills *conservative* (understated).
  - When the touch moves, the MM cancels and re-posts at the new touch,
    resetting its queue position.

Inventory control: quoting stops on the side that would breach an inventory cap
(don't keep buying when already long), the standard first risk control.

PnL is marked to the mid and decomposed into the two pieces that matter:
  spread captured (edge vs mid at fill time)  +  inventory PnL (the mid moving
  while you hold) = total. The second term is where adverse selection shows up.

Usage:
    python market_maker.py ../data/events.csv --qty 0.1 --inv-cap 1.0
"""

import argparse
import sys

import numpy as np

try:
    import pandas as pd
except ImportError:
    sys.exit("pandas required: pip install -r requirements.txt")


def run(df: pd.DataFrame, qty: float, inv_cap: float, maker_fee_bps: float) -> dict:
    et = df["event_type"].to_numpy()
    bid = df["best_bid"].to_numpy()
    ask = df["best_ask"].to_numpy()
    bsz = df["bid_size"].to_numpy()
    asz = df["ask_size"].to_numpy()
    tpx = df["trade_price"].to_numpy()
    tsz = df["trade_size"].to_numpy()
    tside = df["trade_side"].to_numpy()
    n = len(df)
    fee = maker_fee_bps / 1e4

    inventory = 0.0
    cash = 0.0

    # Active quotes. price == nan means "not currently quoting that side".
    mm_bid_px = np.nan
    mm_ask_px = np.nan
    q_ahead_bid = 0.0   # size resting ahead of us at our bid
    q_ahead_ask = 0.0
    rem_bid = 0.0       # our remaining size to be filled at the bid
    rem_ask = 0.0

    equity = np.zeros(n)
    inv_track = np.zeros(n)
    spread_pnl = 0.0     # edge captured vs mid at fill time
    bid_fills = ask_fills = 0
    filled_vol = 0.0

    for i in range(n):
        b, a = bid[i], ask[i]
        mid = 0.5 * (b + a)

        if et[i] == "quote":
            # Re-post to the current touch, honoring the inventory cap. Only
            # reset queue position when the price actually changes (a cancel +
            # re-post); if the price is unchanged the resting order keeps its
            # place and its queue keeps depleting via trades.
            want_bid = inventory < inv_cap
            want_ask = inventory > -inv_cap

            if want_bid:
                if mm_bid_px != b or rem_bid <= 0.0:
                    mm_bid_px = b
                    q_ahead_bid = bsz[i]
                    rem_bid = qty
            else:
                mm_bid_px = np.nan
                rem_bid = 0.0

            if want_ask:
                if mm_ask_px != a or rem_ask <= 0.0:
                    mm_ask_px = a
                    q_ahead_ask = asz[i]
                    rem_ask = qty
            else:
                mm_ask_px = np.nan
                rem_ask = 0.0

        elif et[i] == "trade":
            side = tside[i]
            price = tpx[i]
            size = tsz[i]

            # A bid-consuming trade (side 'b') can fill our resting bid.
            if side == "b" and not np.isnan(mm_bid_px) and price <= mm_bid_px + 1e-12:
                eaten = min(size, q_ahead_bid)
                q_ahead_bid -= eaten
                overflow = size - eaten
                if overflow > 0.0 and rem_bid > 0.0:
                    fill = min(overflow, rem_bid)
                    inventory += fill
                    cash -= mm_bid_px * fill * (1.0 + fee)
                    spread_pnl += (mid - mm_bid_px) * fill      # bought below mid
                    rem_bid -= fill
                    filled_vol += fill
                    bid_fills += 1
                    if rem_bid <= 1e-12:
                        mm_bid_px = np.nan   # done this side until re-quote

            # An ask-consuming trade (side 'a') can fill our resting ask.
            elif side == "a" and not np.isnan(mm_ask_px) and price >= mm_ask_px - 1e-12:
                eaten = min(size, q_ahead_ask)
                q_ahead_ask -= eaten
                overflow = size - eaten
                if overflow > 0.0 and rem_ask > 0.0:
                    fill = min(overflow, rem_ask)
                    inventory -= fill
                    cash += mm_ask_px * fill * (1.0 - fee)
                    spread_pnl += (mm_ask_px - mid) * fill      # sold above mid
                    rem_ask -= fill
                    filled_vol += fill
                    ask_fills += 1
                    if rem_ask <= 1e-12:
                        mm_ask_px = np.nan

        equity[i] = cash + inventory * mid
        inv_track[i] = inventory

    avg_mid = float(np.mean(0.5 * (bid + ask)))
    total_pnl = equity[-1]
    # Total = spread captured + inventory PnL (the mid moving while held).
    inv_pnl = total_pnl - spread_pnl

    running_max = np.maximum.accumulate(equity)
    max_dd = float((equity - running_max).min())

    # Scale everything by the notional actually traded, so bps are comparable
    # to the taker backtest.
    traded_notional = filled_vol * avg_mid

    def bps(x):
        return x / traded_notional * 1e4 if traded_notional > 0 else float("nan")

    return {
        "bid_fills": bid_fills,
        "ask_fills": ask_fills,
        "filled_vol": filled_vol,
        "total_pnl_pts": total_pnl,
        "total_pnl_bps": bps(total_pnl),
        "spread_pnl_bps": bps(spread_pnl),
        "inv_pnl_bps": bps(inv_pnl),
        "max_inventory": float(np.max(np.abs(inv_track))),
        "final_inventory": float(inventory),
        "max_drawdown_pts": max_dd,
        "equity": equity,
        "inventory": inv_track,
    }


def fmt(x, nd=2):
    return "nan" if x != x else f"{x:.{nd}f}"


# Shared dark theme so every generated chart matches the dashboards.
def _dark(plt):
    plt.rcParams.update({
        "figure.facecolor": "#06080c", "axes.facecolor": "#0b0e14",
        "axes.edgecolor": "#1f2630", "text.color": "#e9edf4",
        "axes.labelcolor": "#6d7686", "xtick.color": "#6d7686",
        "ytick.color": "#6d7686", "axes.titlecolor": "#e9edf4",
        "font.size": 9, "grid.color": "#1f2630",
    })


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("events")
    p.add_argument("--qty", type=float, default=0.1, help="quote size per side")
    p.add_argument("--inv-cap", type=float, default=1.0, help="max abs inventory")
    p.add_argument("--maker-fee-bps", type=float, default=0.0,
                   help="maker fee per fill (negative = rebate)")
    p.add_argument("--plot", metavar="PNG")
    args = p.parse_args()

    df = pd.read_csv(args.events)
    n_quotes = int((df["event_type"] == "quote").sum())
    n_trades = int((df["event_type"] == "trade").sum())
    print(f"loaded {len(df)} events ({n_quotes} quotes, {n_trades} trades)\n")
    if n_trades == 0:
        sys.exit("no trade events -- recapture with the updated capture_feed.py "
                 "(matches channel)")

    r = run(df, args.qty, args.inv_cap, args.maker_fee_bps)

    print(f"=== Passive MM: quote {args.qty} at touch, inventory cap "
          f"{args.inv_cap}, maker fee {args.maker_fee_bps}bps ===")
    print(f"  fills                {r['bid_fills']} bid / {r['ask_fills']} ask "
          f"({fmt(r['filled_vol'], 4)} units)")
    print(f"  max |inventory|      {fmt(r['max_inventory'], 4)}  "
          f"(final {fmt(r['final_inventory'], 4)})")
    print(f"  spread captured      {fmt(r['spread_pnl_bps'])} bps")
    print(f"  inventory / adverse  {fmt(r['inv_pnl_bps'])} bps")
    print(f"  --------------------")
    print(f"  net PnL (to mid)     {fmt(r['total_pnl_bps'])} bps "
          f"({fmt(r['total_pnl_pts'])} pts)")
    print(f"  max drawdown         {fmt(r['max_drawdown_pts'])} pts")
    print()
    print("Read: the MM earns a positive spread, and inventory / adverse "
          "selection is\nwhat it gives back. Net is the difference -- the honest "
          "test of whether\npassive quoting beats the taker that paid the spread.")

    if args.plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            print("matplotlib not installed; skipping plot", file=sys.stderr)
        else:
            _dark(plt)
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
            ax1.plot(r["equity"], lw=1.3, color="#f0a63a")
            ax1.axhline(0, color="#6d7686", lw=0.6, alpha=0.6)
            ax1.set_ylabel("PnL (price points)")
            ax1.set_title(f"Passive MM — qty={args.qty}, inv cap={args.inv_cap}")
            ax2.plot(r["inventory"], lw=1.3, color="#8b8ff5")
            ax2.axhline(0, color="#6d7686", lw=0.6, alpha=0.6)
            ax2.set_ylabel("inventory")
            ax2.set_xlabel("event")
            fig.tight_layout()
            fig.savefig(args.plot, dpi=110, facecolor="#06080c")
            print(f"\nwrote plot -> {args.plot}")


if __name__ == "__main__":
    main()
