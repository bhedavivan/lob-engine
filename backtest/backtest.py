"""Backtest an order-book-imbalance signal on the feature stream emitted by
the C++ engine (`lob_engine --emit`).

Two questions, kept deliberately separate because they have different answers:

  1. Signal quality  -- does imbalance predict the next mid-price move at all?
     Measured with zero trading costs: directional accuracy and correlation of
     the signal against the forward mid return. This is the "is there alpha"
     question.

  2. Tradability     -- does a naive taker rule that acts on the signal make
     money after the cost of crossing the spread (and exchange fees)? This is
     usually a very different, humbler answer, and the gap between (1) and (2)
     is the whole point.

Execution model (intentionally conservative, honest with L2-only data):
  - Act at the touch: to go long we lift the best ask; to go short we hit the
    best bid. Both are prices actually visible in the book at that event, so
    there is no fill-model guesswork and no assumption of queue priority.
  - Mark open positions to the mid. No lookahead: the signal at event t uses
    only the book state up to and including event t.

Usage:
    python backtest.py ../data/features.csv --signal imb5 --horizon 50 \
        --threshold 0.30 --fee-bps 0
"""

import argparse
import sys

import numpy as np

try:
    import pandas as pd
except ImportError:
    sys.exit("pandas is required: pip install -r requirements.txt")


# Shared dark theme so every generated chart matches the dashboards.
def _dark(plt):
    plt.rcParams.update({
        "figure.facecolor": "#06080c", "axes.facecolor": "#0b0e14",
        "axes.edgecolor": "#1f2630", "text.color": "#e9edf4",
        "axes.labelcolor": "#6d7686", "xtick.color": "#6d7686",
        "ytick.color": "#6d7686", "axes.titlecolor": "#e9edf4",
        "font.size": 9, "grid.color": "#1f2630",
    })


def load_features(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    # Deep-book updates emit an event even when the top of book is unchanged,
    # so the raw stream carries runs of identical mids. Collapse consecutive
    # rows with an unchanged mid AND unchanged signal set: they hold no new
    # information for a mid-reversion/continuation study and would otherwise
    # inflate event counts with trivial zero-return samples.
    keep = (
        (df["mid"].diff() != 0)
        | (df["imb1"].diff() != 0)
        | (df["imb5"].diff() != 0)
        | (df["imb10"].diff() != 0)
    )
    keep.iloc[0] = True
    collapsed = df[keep].reset_index(drop=True)
    return collapsed


def signal_quality(df: pd.DataFrame, signal: str, horizon: int) -> dict:
    mid = df["mid"].to_numpy()
    sig = df[signal].to_numpy()
    fwd = np.full_like(mid, np.nan)
    fwd[:-horizon] = mid[horizon:] - mid[:-horizon]

    valid = ~np.isnan(fwd)
    moved = valid & (fwd != 0.0)  # ties carry no directional label

    # Directional accuracy over events where the price actually moved.
    correct = np.sign(sig[moved]) == np.sign(fwd[moved])
    acc = correct.mean() if moved.any() else float("nan")

    # Majority-class baseline: always predict the more common direction.
    up_rate = (fwd[moved] > 0).mean() if moved.any() else float("nan")
    baseline = max(up_rate, 1 - up_rate)

    corr = np.corrcoef(sig[valid], fwd[valid])[0, 1] if valid.sum() > 2 else float("nan")

    return {
        "events": int(valid.sum()),
        "moved_events": int(moved.sum()),
        "accuracy": acc,
        "baseline": baseline,
        "edge": acc - baseline,
        "correlation": corr,
    }


def run_strategy(df: pd.DataFrame, signal: str, threshold: float, fee_bps: float) -> dict:
    """Event-driven taker sim. Position is in {-1, 0, +1} of one unit; PnL is
    reported in basis points of traded notional, which is scale-free."""
    mid = df["mid"].to_numpy()
    bid = df["best_bid"].to_numpy()
    ask = df["best_ask"].to_numpy()
    sig = df[signal].to_numpy()
    n = len(df)
    fee = fee_bps / 1e4

    position = 0
    entry_px = 0.0
    realized = 0.0  # cumulative realized PnL in price points (dollars per unit)
    equity = np.zeros(n)
    trade_pnls = []
    n_trades = 0
    notional_traded = 0.0

    def target(s: float) -> int:
        if s > threshold:
            return 1
        if s < -threshold:
            return -1
        return 0  # inside the band: flat

    for i in range(n):
        want = target(sig[i])
        if want != position:
            # Close the existing position at the touch, then open the new one.
            if position == 1:
                exit_px = bid[i]                      # sell to close a long
                realized += (exit_px - entry_px) - fee * exit_px
                trade_pnls.append((exit_px - entry_px) - fee * exit_px)
                notional_traded += exit_px
                n_trades += 1
            elif position == -1:
                exit_px = ask[i]                      # buy to close a short
                realized += (entry_px - exit_px) - fee * exit_px
                trade_pnls.append((entry_px - exit_px) - fee * exit_px)
                notional_traded += exit_px
                n_trades += 1

            if want == 1:
                entry_px = ask[i] + fee * ask[i]      # lift the offer, pay fee
                notional_traded += ask[i]
            elif want == -1:
                entry_px = bid[i] - fee * bid[i]      # hit the bid, pay fee
                notional_traded += bid[i]
            position = want

        # Mark to market at the mid.
        if position == 1:
            equity[i] = realized + (mid[i] - entry_px)
        elif position == -1:
            equity[i] = realized + (entry_px - mid[i])
        else:
            equity[i] = realized

    final_pnl = equity[-1]
    avg_mid = float(np.mean(mid))

    # Per-event PnL changes -> a scale-free Sharpe in event time (NOT annualized;
    # event time is not wall-clock, so annualizing would be dishonest).
    step = np.diff(equity, prepend=0.0)
    sharpe_evt = step.mean() / step.std() if step.std() > 0 else float("nan")

    running_max = np.maximum.accumulate(equity)
    drawdown = equity - running_max
    max_dd = drawdown.min()

    wins = [p for p in trade_pnls if p > 0]
    hit_rate = len(wins) / len(trade_pnls) if trade_pnls else float("nan")

    return {
        "final_pnl_pts": final_pnl,
        "final_pnl_bps": final_pnl / avg_mid * 1e4,
        "n_trades": n_trades,
        "hit_rate": hit_rate,
        "avg_trade_bps": (np.mean(trade_pnls) / avg_mid * 1e4) if trade_pnls else float("nan"),
        "sharpe_event": sharpe_evt,
        "max_drawdown_pts": max_dd,
        "turnover_notional_mult": notional_traded / avg_mid if avg_mid else float("nan"),
        "equity": equity,
    }


def fmt(x: float, nd: int = 4) -> str:
    return "nan" if x != x else f"{x:.{nd}f}"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("features")
    p.add_argument("--signal", default="imb5", choices=["imb1", "imb5", "imb10"])
    p.add_argument("--horizon", type=int, default=50, help="forward horizon in events")
    p.add_argument("--threshold", type=float, default=0.30, help="entry band on the signal")
    p.add_argument("--fee-bps", type=float, default=0.0, help="per-leg taker fee, bps")
    p.add_argument("--plot", metavar="PNG", help="write an equity-curve PNG")
    p.add_argument("--equity-csv", metavar="CSV", help="write the equity curve as CSV")
    args = p.parse_args()

    df = load_features(args.features)
    print(f"loaded {len(df)} informative events "
          f"(collapsed from the raw emit stream)\n")

    sq = signal_quality(df, args.signal, args.horizon)
    print(f"=== Signal quality: {args.signal} vs forward mid, horizon={args.horizon} events ===")
    print(f"  events (labelled):   {sq['events']}")
    print(f"  events with a move:  {sq['moved_events']}")
    print(f"  directional accuracy {fmt(sq['accuracy'])}   (baseline {fmt(sq['baseline'])}, "
          f"edge {fmt(sq['edge'])})")
    print(f"  signal/return corr   {fmt(sq['correlation'])}\n")

    print(f"=== Taker strategy: |{args.signal}| > {args.threshold}, "
          f"fee={args.fee_bps}bps/leg ===")
    res = run_strategy(df, args.signal, args.threshold, args.fee_bps)
    print(f"  trades (round-trips) {res['n_trades']}")
    print(f"  hit rate             {fmt(res['hit_rate'])}")
    print(f"  avg trade            {fmt(res['avg_trade_bps'], 3)} bps")
    print(f"  total PnL            {fmt(res['final_pnl_pts'], 2)} pts "
          f"({fmt(res['final_pnl_bps'], 2)} bps of notional)")
    print(f"  event-time Sharpe    {fmt(res['sharpe_event'], 4)}  (NOT annualized)")
    print(f"  max drawdown         {fmt(res['max_drawdown_pts'], 2)} pts")
    print(f"  turnover             {fmt(res['turnover_notional_mult'], 1)}x notional\n")

    # Fee sensitivity: the gap between signal quality and tradability lives here.
    print("=== Fee sensitivity (total PnL, bps of notional) ===")
    for fb in (0.0, 0.5, 1.0, 2.0, 5.0):
        r = run_strategy(df, args.signal, args.threshold, fb)
        print(f"  {fb:>4}bps/leg:  {fmt(r['final_pnl_bps'], 2):>10} bps")

    if args.equity_csv:
        pd.DataFrame({"event": np.arange(len(res["equity"])), "equity_pts": res["equity"]}).to_csv(
            args.equity_csv, index=False)
        print(f"\nwrote equity curve -> {args.equity_csv}")

    if args.plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            print("matplotlib not installed; skipping plot", file=sys.stderr)
        else:
            _dark(plt)
            fig, ax = plt.subplots(figsize=(10, 4))
            ax.plot(res["equity"], lw=1.3, color="#f0a63a")
            ax.axhline(0, color="#6d7686", lw=0.6, alpha=0.6)
            ax.set_title(f"Equity curve — {args.signal}, thr={args.threshold}, "
                         f"fee={args.fee_bps}bps")
            ax.set_xlabel("event")
            ax.set_ylabel("PnL (price points)")
            fig.tight_layout()
            fig.savefig(args.plot, dpi=110, facecolor="#06080c")
            print(f"wrote plot -> {args.plot}")


if __name__ == "__main__":
    main()
