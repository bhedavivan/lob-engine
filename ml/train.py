"""Train and honestly evaluate a short-horizon mid-price direction classifier
on the order-book features emitted by the C++ engine (`lob_engine --emit`).

The v2 backtest showed a single feature (top-of-book imbalance) predicts the
next mid move above baseline. The question here: does a trained model over the
full feature set do meaningfully better, and does it hold up *out of sample*?

Two things make this an honest evaluation rather than an overfit demo:

  - **Walk-forward, time-ordered split.** Markets are a time series; a random
    train/test split leaks the future into the past. We train on an earlier
    window and test on a strictly later one.
  - **Purge gap.** The label is a forward return over H events, so a sample's
    label peeks H events ahead. We drop H events at the train/test boundary so
    no training label overlaps the test window (lookahead leakage).

Baselines it must beat to matter:
  1. Majority class (always predict the more common direction).
  2. The naive single-feature rule: sign(imb1). If the model can't beat one
     feature and a sign(), the extra machinery earns nothing.

Usage:
    python train.py ../data/features.csv --horizon 50
"""

import argparse
import sys

import numpy as np

try:
    import pandas as pd
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import accuracy_score, roc_auc_score
    from sklearn.preprocessing import StandardScaler
except ImportError:
    sys.exit("deps required: pip install -r requirements.txt")


FEATURE_COLS = [
    "imb1", "imb5", "imb10",       # order-book imbalance at three depths
    "microprice_tilt",             # (microprice - mid), signed pressure at the touch
    "rel_spread",                  # spread / mid, the cost/volatility regime
    "mom",                         # recent mid momentum over the horizon
    "log_size_ratio",              # log(bid_size / ask_size) at the touch
]


def load_and_engineer(path: str, horizon: int) -> pd.DataFrame:
    df = pd.read_csv(path)

    # Collapse the runs of identical top-of-book that deep-book updates emit,
    # matching the backtester's notion of an informative event.
    keep = (
        (df["mid"].diff() != 0)
        | (df["imb1"].diff() != 0)
        | (df["imb5"].diff() != 0)
        | (df["imb10"].diff() != 0)
    )
    keep.iloc[0] = True
    df = df[keep].reset_index(drop=True)

    # Engineered features (all computable from a single event except momentum,
    # which looks only backward -- no lookahead).
    df["microprice_tilt"] = df["microprice"] - df["mid"]
    df["rel_spread"] = df["spread"] / df["mid"]
    df["mom"] = df["mid"].diff(horizon).fillna(0.0)
    df["log_size_ratio"] = np.log(
        (df["bid_size"] + 1e-9) / (df["ask_size"] + 1e-9)
    )

    # Target: sign of the forward mid move over the horizon. Ties (no move)
    # carry no direction, so they're dropped.
    fwd = df["mid"].shift(-horizon) - df["mid"]
    df["fwd"] = fwd
    df["target"] = np.sign(fwd)
    df = df.iloc[:-horizon]                       # last H rows have no label
    df = df[df["target"] != 0].reset_index(drop=True)
    df["target"] = (df["target"] > 0).astype(int)  # 1 = up, 0 = down
    return df


def walk_forward_folds(n: int, horizon: int, n_folds: int = 5):
    """Expanding-window folds. Each fold trains on everything up to a cut,
    purges `horizon` events, then tests on the following block."""
    test_size = n // (n_folds + 1)
    for k in range(1, n_folds + 1):
        train_end = test_size * k
        test_start = train_end + horizon           # purge gap
        test_end = min(test_start + test_size, n)
        if test_start >= n:
            break
        yield (np.arange(0, train_end),
               np.arange(test_start, test_end))


def evaluate(df: pd.DataFrame, horizon: int, n_folds: int) -> dict:
    X = df[FEATURE_COLS].to_numpy()
    y = df["target"].to_numpy()
    imb1 = df["imb1"].to_numpy()
    n = len(df)

    rows = {"majority": [], "sign(imb1)": [], "logistic": [], "gbt": []}
    auc = {"logistic": [], "gbt": []}
    importances = []

    for tr, te in walk_forward_folds(n, horizon, n_folds):
        ytr, yte = y[tr], y[te]
        if len(np.unique(ytr)) < 2 or len(te) == 0:
            continue

        # Baseline 1: majority class from the training window.
        maj = int(round(ytr.mean()))
        rows["majority"].append(accuracy_score(yte, np.full_like(yte, maj)))

        # Baseline 2: the naive single-feature rule, sign(imb1) -> up/down.
        pred_sign = (imb1[te] > 0).astype(int)
        rows["sign(imb1)"].append(accuracy_score(yte, pred_sign))

        # Model 1: logistic regression on standardized features.
        scaler = StandardScaler().fit(X[tr])
        lr = LogisticRegression(max_iter=1000)
        lr.fit(scaler.transform(X[tr]), ytr)
        p_lr = lr.predict_proba(scaler.transform(X[te]))[:, 1]
        rows["logistic"].append(accuracy_score(yte, (p_lr > 0.5).astype(int)))
        auc["logistic"].append(roc_auc_score(yte, p_lr))

        # Model 2: gradient-boosted trees (nonlinear, feature interactions).
        gbt = HistGradientBoostingClassifier(max_depth=3, max_iter=200,
                                             learning_rate=0.05)
        gbt.fit(X[tr], ytr)
        p_gbt = gbt.predict_proba(X[te])[:, 1]
        rows["gbt"].append(accuracy_score(yte, (p_gbt > 0.5).astype(int)))
        auc["gbt"].append(roc_auc_score(yte, p_gbt))

        # Permutation importance on this fold's test set (GBT).
        importances.append(_perm_importance(gbt, X[te], yte))

    return {
        "acc": {k: (np.mean(v) if v else float("nan")) for k, v in rows.items()},
        "acc_std": {k: (np.std(v) if v else float("nan")) for k, v in rows.items()},
        "auc": {k: (np.mean(v) if v else float("nan")) for k, v in auc.items()},
        "importance": (np.mean(importances, axis=0) if importances else None),
        "n_folds_used": len(rows["gbt"]),
        "n_samples": n,
    }


def _perm_importance(model, X, y, repeats: int = 3) -> np.ndarray:
    """Drop in accuracy when each feature column is shuffled. Higher = the
    model relies on it more. Simple, model-agnostic, honest."""
    base = accuracy_score(y, model.predict(X))
    rng = np.random.default_rng(0)
    imp = np.zeros(X.shape[1])
    for j in range(X.shape[1]):
        drops = []
        for _ in range(repeats):
            Xp = X.copy()
            Xp[:, j] = rng.permutation(Xp[:, j])
            drops.append(base - accuracy_score(y, model.predict(Xp)))
        imp[j] = np.mean(drops)
    return imp


def fmt(x, nd=4):
    return "nan" if x != x else f"{x:.{nd}f}"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("features")
    p.add_argument("--horizon", type=int, default=50, help="forward horizon in events")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--plot", metavar="PNG", help="write a feature-importance PNG")
    args = p.parse_args()

    df = load_and_engineer(args.features, args.horizon)
    print(f"{len(df)} labelled events (up={df['target'].mean():.3f}), "
          f"horizon={args.horizon}, {args.folds}-fold walk-forward\n")

    res = evaluate(df, args.horizon, args.folds)
    if res["n_folds_used"] == 0:
        sys.exit("not enough data for the requested folds/horizon")

    print(f"=== Out-of-sample accuracy ({res['n_folds_used']} folds, "
          f"mean ± std) ===")
    order = ["majority", "sign(imb1)", "logistic", "gbt"]
    labels = {"majority": "majority class", "sign(imb1)": "sign(imb1) baseline",
              "logistic": "logistic regression", "gbt": "gradient-boosted trees"}
    for k in order:
        line = f"  {labels[k]:<24} {fmt(res['acc'][k])} ± {fmt(res['acc_std'][k], 3)}"
        if k in res["auc"]:
            line += f"   (AUC {fmt(res['auc'][k], 3)})"
        print(line)

    if res["importance"] is not None:
        print("\n=== GBT permutation importance (accuracy drop when shuffled) ===")
        pairs = sorted(zip(FEATURE_COLS, res["importance"]), key=lambda t: -t[1])
        for name, imp in pairs:
            print(f"  {name:<18} {fmt(imp, 4)}")

    if args.plot and res["importance"] is not None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            print("matplotlib not installed; skipping plot", file=sys.stderr)
        else:
            pairs = sorted(zip(FEATURE_COLS, res["importance"]), key=lambda t: t[1])
            names = [p[0] for p in pairs]
            vals = [p[1] for p in pairs]
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.barh(names, vals)
            ax.set_title(f"GBT feature importance (horizon={args.horizon})")
            ax.set_xlabel("out-of-sample accuracy drop when shuffled")
            fig.tight_layout()
            fig.savefig(args.plot, dpi=110)
            print(f"\nwrote plot -> {args.plot}")


if __name__ == "__main__":
    main()
