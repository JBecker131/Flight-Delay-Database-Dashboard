"""Open-Meteo forecast for every airport: daily values for the model, hourly ones for the board's weather card.

The daily variables match data_pipeline/download_data.py's WEATHER_VARS, so forecast rows line up with
the historical weather_daily rows the model is trained on.
"""
import urllib.parse

import httpx
from sqlalchemy import delete, insert

from live import store
from live.airports import load

URL = "https://api.open-meteo.com/v1/forecast"
BATCH = 200  # Open-Meteo takes up to 1,000 locations a request; smaller batches keep responses modest

# Open-Meteo name -> forecast_daily column (same names as weather_daily)
DAILY = {
    "weather_code": "weather_code", "temperature_2m_max": "temp_max_f", "temperature_2m_min": "temp_min_f",
    "precipitation_sum": "precip_in", "rain_sum": "rain_in", "snowfall_sum": "snowfall_in",
    "precipitation_hours": "precip_hours", "wind_speed_10m_max": "wind_max_mph",
    "wind_gusts_10m_max": "gust_max_mph", "precipitation_probability_max": "precip_prob_max",
}
DAILY_VARS = list(DAILY)
HOURLY = {
    "temperature_2m": "temp_f", "weather_code": "weather_code", "wind_speed_10m": "wind_mph",
    "wind_direction_10m": "wind_dir_deg", "visibility": "visibility_mi", "precipitation_probability": "precip_prob",
}
FEET_PER_MILE, METRES_PER_MILE = 5280, 1609.34


def request_url(airports):
    params = {
        "latitude": ",".join(str(round(a["lat"], 4)) for a in airports),
        "longitude": ",".join(str(round(a["lon"], 4)) for a in airports),
        "daily": ",".join(DAILY_VARS), "hourly": ",".join(HOURLY),
        "forecast_days": 3, "timezone": "auto",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
    }
    return URL + "?" + urllib.parse.urlencode(params, safe=",")


def _value(column, v):
    if v is None:
        return None
    return int(v) if column == "weather_code" else float(v)


def parse(payload, airports):
    """Open-Meteo returns a list for several locations, in request order, and a bare object for one."""
    locations = payload if isinstance(payload, list) else [payload]
    daily_rows, hourly_rows = [], []
    for airport, loc in zip(airports, locations, strict=True):
        code = airport["code"]
        d = loc["daily"]
        for i, day in enumerate(d["time"]):
            daily_rows.append({"airport": code, "local_date": day,
                               **{col: _value(col, d[name][i]) for name, col in DAILY.items()}})
        h = loc["hourly"]
        vis_per_mile = FEET_PER_MILE if loc["hourly_units"]["visibility"] == "ft" else METRES_PER_MILE
        for i, t in enumerate(h["time"]):
            row = {"airport": code, "time_local": t, "local_date": t[:10],
                   **{col: _value(col, h[name][i]) for name, col in HOURLY.items()}}
            if row["visibility_mi"] is not None:
                row["visibility_mi"] /= vis_per_mile
            hourly_rows.append(row)
    return daily_rows, hourly_rows


def refresh(engine, client=None):
    """Replace the forecast for every airport. Returns how many airports were refreshed."""
    airports = load()["airports"]
    client = client or httpx.Client(timeout=60)
    stamp = store.now_iso()
    for start in range(0, len(airports), BATCH):
        batch = airports[start:start + BATCH]
        response = client.get(request_url(batch))
        response.raise_for_status()
        daily, hourly = parse(response.json(), batch)
        codes = [a["code"] for a in batch]
        with engine.begin() as conn:
            conn.execute(delete(store.forecast_daily).where(store.forecast_daily.c.airport.in_(codes)))
            conn.execute(delete(store.forecast_hourly).where(store.forecast_hourly.c.airport.in_(codes)))
            conn.execute(insert(store.forecast_daily), [{**r, "fetched_at": stamp} for r in daily])
            conn.execute(insert(store.forecast_hourly), hourly)
    return len(airports)
