"""Score flights with the gradient boosting model: features in, expected delay and risk out.

The model predicts the *average* delay of flights like this one (squared-error loss), and the
notebook's conclusion is that it ranks flights well but can't call the size of long delays. So
each prediction also comes with what that number meant on the 411,465 held-out test flights
(model/artifacts/serving.json, from export_serving.py):

  risk            low (below the median prediction), medium, or high (the riskiest 20%)
  chance_late_15  the share of test flights with a similar prediction that left 15+ minutes late
  chance_late_60  ... and 60+ minutes late
"""
import bisect
import json
from pathlib import Path

import joblib

from model import features as feat

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"


def load_artifacts(directory=ARTIFACTS):
    """The model, its feature list, the calibration and the airports (code -> lat, lon, tz)."""
    directory = Path(directory)
    read = lambda name: json.loads((directory / name).read_text(encoding="utf-8"))  # noqa: E731
    airports = {code: tuple(v) for code, v in read("airports.json").items()}
    return joblib.load(directory / "gradient_boosting.joblib"), read("feature_list.json"), read("serving.json"), airports


class Predictor:
    def __init__(self, model, feature_meta, serving, airports, lookups, weather):
        """`lookups` is a SupabaseLookups or InMemoryLookups; `weather` a lookups.Weather or alike."""
        self.model, self.feature_meta, self.serving, self.airports = model, feature_meta, serving, airports
        self.features = feature_meta["features"]
        self.levels = feature_meta["categorical_levels"]
        self.lookups, self.weather = lookups, weather
        self._uppers = [b["upper"] for b in serving["bins"][:-1]]

    def describe(self, value):
        thresholds = self.serving["risk_thresholds"]
        risk = "high" if value >= thresholds["high"] else "medium" if value >= thresholds["medium"] else "low"
        group = self.serving["bins"][bisect.bisect_right(self._uppers, value)]
        return {"risk": risk, "chance_late_15": group["late15"], "chance_late_60": group["late60"]}

    def predict(self, flights: list[feat.Flight], include_features=False):
        results, schedules = [], []
        for f in flights:
            try:
                schedules.append(feat.schedule(f, self.airports, self.serving["elapsed_fit"]))
                results.append(None)
            except feat.FlightError as error:
                schedules.append(None)
                results.append({"error": str(error)})
        ok = [(i, s) for i, s in enumerate(schedules) if s is not None]
        if not ok:
            return results

        # Two keyed reads for the whole batch: who operates each flight, then every row the flights need.
        operators = self.lookups.fetch({"operator": [k for _, s in ok for k in feat.operator_keys(s)]})["operator"]
        carriers = {i: feat.operating_carrier(s, operators) for i, s in ok}
        needed = {}
        for i, s in ok:
            for table, keys in feat.table_keys(s, carriers[i]).items():
                needed.setdefault(table, []).extend(keys)
        tables = feat.Lookups(operator=operators, **self.lookups.fetch(needed))
        self.weather.prefetch([(a, s.dep.date()) for _, s in ok for a in (s.origin, s.dest)])

        rows = []
        for i, s in ok:
            row, info = feat.build(s, carriers[i], tables, self.weather.get)
            rows.append(row)
            results[i] = info
        predictions = self.model.predict(feat.to_frame(rows, self.features, self.levels))
        for (i, _), row, value in zip(ok, rows, predictions):
            value = max(float(value), 0.0)
            results[i] = {"expected_delay_min": round(value, 1), **self.describe(value), **results[i], "error": None}
            if include_features:
                results[i]["features"] = {k: _plain(v) for k, v in row.items()}
        return results

    def info(self):
        s = self.serving
        return {
            "model": f"{s['model']} ({s['trees']} trees)",
            "target": self.feature_meta["target"],
            "trained_on": f"a 1-in-{self.feature_meta['sample_rate']} sample of BTS domestic flights, 2023-2025",
            "test": {"flights": s["test_flights"], "rmse_min": s["test_rmse"], "auc_late15": s["test_auc_late15"]},
            "risk_thresholds_min": s["risk_thresholds"],
            "lookups": self.lookups.meta(),
            "features": self.features,
        }


def _plain(v):
    """JSON-safe: NaN becomes null."""
    return None if isinstance(v, float) and v != v else v
