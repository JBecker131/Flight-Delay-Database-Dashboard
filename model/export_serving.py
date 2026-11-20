"""Copy the trained gradient boosting model into model/artifacts/ for the API, with its calibration.

    python model/export_serving.py

Run it after delay_minutes_model.ipynb saves a new model. It reads the notebook's outputs from
FLIGHT_DATA_DIR/models (gradient_boosting.joblib, feature_list.json and the cached sample), redraws
the same random 80/20 split, and scores the held-out 20% to work out what each predicted delay
means in practice:

  risk_thresholds   the predicted minutes that start "medium" (the median test prediction) and
                    "high" (the riskiest 20%), so the website can show a label, not a promise
  bins              20 equal groups of test flights by predicted delay, each with the share that
                    actually left 15+ and 60+ minutes late: the API's chance_late_15 / chance_late_60
  elapsed_fit       scheduled minutes ~ a + b * miles, for a flight sent without an arrival time

It also writes airports.json (code -> latitude, longitude, timezone) from flights.db, for distances
and local times.
"""
import json
import os
import shutil
import sqlite3
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, root_mean_squared_error
from sklearn.model_selection import train_test_split

DATA_DIR = Path(os.environ.get("FLIGHT_DATA_DIR", Path.home() / "flight_data"))
MODEL_DIR = DATA_DIR / "models"
OUT_DIR = Path(__file__).resolve().parent / "artifacts"
N_BINS = 20


def notebook_features(raw, meta):
    """The notebook's section 3, with the categorical levels it saved."""
    df = raw.copy()
    df["delay_min"] = df["dep_delay"].clip(lower=0)
    df["dep_hour"] = df["dep_hour"] % 24
    df["arr_hour"] = (df["sched_arr_time"] // 100) % 24
    for side in ("origin", "dest"):
        code = df[f"{side}_weather_code"]
        df[f"{side}_storm"] = (code >= 95).astype("float32")
        df[f"{side}_snow"] = (code.between(71, 77) | code.between(85, 86)).astype("float32")
    for col, levels in meta["categorical_levels"].items():
        df[col] = pd.Categorical(df[col].where(df[col].isin(levels), "OTHER"), categories=levels)
    return df


def calibration(pred, actual_delay):
    edges = np.quantile(pred, np.linspace(0, 1, N_BINS + 1))
    which = np.clip(np.searchsorted(edges, pred, side="right") - 1, 0, N_BINS - 1)
    bins = []
    for b in range(N_BINS):
        hit = which == b
        bins.append({"upper": None if b == N_BINS - 1 else round(float(edges[b + 1]), 3),
                     "flights": int(hit.sum()),
                     "mean_predicted": round(float(pred[hit].mean()), 2),
                     "mean_actual": round(float(np.clip(actual_delay[hit], 0, None).mean()), 2),
                     "late15": round(float((actual_delay[hit] >= 15).mean()), 4),
                     "late60": round(float((actual_delay[hit] >= 60).mean()), 4)})
    return bins


def main():
    meta = json.loads((MODEL_DIR / "feature_list.json").read_text(encoding="utf-8"))
    model = joblib.load(MODEL_DIR / "gradient_boosting.joblib")
    sample = MODEL_DIR / f"sample_1in{meta['sample_rate']}.pkl"
    print(f"reading {sample}")
    df = notebook_features(pd.read_pickle(sample), meta)

    _, test_idx = train_test_split(df.index, test_size=meta["test_fraction"], random_state=meta["split_seed"])
    test = df.loc[test_idx]
    pred = model.predict(test[meta["features"]])
    actual = test["dep_delay"].to_numpy()
    rmse = root_mean_squared_error(test["delay_min"], pred)
    auc = roc_auc_score(actual >= 15, pred)
    saved = meta["test_scores"]["Gradient Boosting"]
    print(f"test flights {len(test):,}: RMSE {rmse:.4f} (notebook {saved['RMSE']}), "
          f"AUC {auc:.4f} (notebook {saved['ROC AUC for 15+ min late']})")
    if abs(rmse - saved["RMSE"]) > 0.01:
        raise SystemExit("The redrawn test set doesn't reproduce the notebook's score; was the sample replaced?")

    slope, intercept = np.polyfit(df["distance"], df["sched_elapsed_time"], 1)
    serving = {
        "model": "HistGradientBoostingRegressor",
        "trees": meta["gradient_boosting_params"]["trees"],
        "test_flights": len(test),
        "test_rmse": round(float(rmse), 4),
        "test_auc_late15": round(float(auc), 4),
        "risk_thresholds": {"medium": round(float(np.quantile(pred, 0.5)), 3),
                            "high": round(float(np.quantile(pred, 0.8)), 3)},
        "bins": calibration(pred, actual),
        "elapsed_fit": {"intercept": round(float(intercept), 3), "per_mile": round(float(slope), 5)},
    }

    OUT_DIR.mkdir(exist_ok=True)
    shutil.copy2(MODEL_DIR / "gradient_boosting.joblib", OUT_DIR / "gradient_boosting.joblib")
    shutil.copy2(MODEL_DIR / "feature_list.json", OUT_DIR / "feature_list.json")
    (OUT_DIR / "serving.json").write_text(json.dumps(serving, indent=2), encoding="utf-8")
    con = sqlite3.connect(f"file:{(DATA_DIR / 'flights.db').as_posix()}?mode=ro", uri=True)
    airports = {code: [lat, lon, tz] for code, lat, lon, tz in con.execute(
        "SELECT code, latitude, longitude, timezone FROM airports WHERE latitude IS NOT NULL "
        "AND longitude IS NOT NULL AND timezone IS NOT NULL ORDER BY code")}
    con.close()
    (OUT_DIR / "airports.json").write_text(json.dumps(airports, indent=0), encoding="utf-8")
    print(f"risk: medium from {serving['risk_thresholds']['medium']:.1f} min, high from "
          f"{serving['risk_thresholds']['high']:.1f} min; {len(airports)} airports; wrote {OUT_DIR}")


if __name__ == "__main__":
    main()
