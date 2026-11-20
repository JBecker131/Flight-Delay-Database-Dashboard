"""Read the prediction-time lookups from Supabase, and find the weather for each flight's day.

DASHBOARD_DB_URL picks the database, read from the environment (a Modal secret when deployed), then
dashboard/.env, then live/.env. The lookup tables stay in Supabase: each request reads only the rows
its flights need, with one keyed query per table, all sent in a single round trip.

Weather for an (airport, day) comes from, in order:
  1. live.forecast_daily, the Open-Meteo forecast the departures board's nightly job saves
     (every airport, today through two days out)
  2. Open-Meteo directly, for days the saved forecast doesn't cover (about 3 months back to 15 days ahead)
  3. nothing: the weather inputs are left missing, which the model can handle, and the response says so
"""
import logging
import math
import os
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import psycopg

from model.features import PREV_FIELDS, WEATHER_FIELDS, Lookups

ROOT = Path(__file__).resolve().parent.parent
ENV_FILES = [ROOT / "dashboard" / ".env", ROOT / "live" / ".env"]
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
# Open-Meteo name -> column, the same variables data_pipeline/download_data.py trained on
OPEN_METEO_DAILY = {
    "weather_code": "weather_code", "temperature_2m_max": "temp_max_f", "temperature_2m_min": "temp_min_f",
    "precipitation_sum": "precip_in", "rain_sum": "rain_in", "snowfall_sum": "snowfall_in",
    "precipitation_hours": "precip_hours", "wind_speed_10m_max": "wind_max_mph",
    "wind_gusts_10m_max": "gust_max_mph",
}
OPEN_METEO_PAST_DAYS, OPEN_METEO_AHEAD_DAYS = 90, 15

log = logging.getLogger(__name__)


def database_url():
    url = os.environ.get("DASHBOARD_DB_URL")
    for env_file in ENV_FILES:
        if url or not env_file.exists():
            continue
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "DASHBOARD_DB_URL":
                url = value.strip().strip("'\"")
    if not url:
        raise RuntimeError("DASHBOARD_DB_URL isn't set (the Supabase connection string; see dashboard/.env.example)")
    for prefix in ("postgresql+psycopg://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql://" + url.removeprefix(prefix)
    return url


class Database:
    """One connection shared by the container's requests, reopened if Supabase drops it."""

    def __init__(self, url):
        self.url = url
        self._conn = None
        self._lock = threading.Lock()

    def query_many(self, statements):
        """Run (sql, params) pairs in one round trip; returns each one's rows."""
        with self._lock:
            for attempt in range(2):
                try:
                    if self._conn is None or self._conn.closed:
                        self._conn = psycopg.connect(self.url, autocommit=True, prepare_threshold=None,
                                                     connect_timeout=10)
                    with self._conn.pipeline():
                        cursors = [self._conn.execute(sql, params) for sql, params in statements]
                    return [c.fetchall() for c in cursors]
                except psycopg.OperationalError:
                    self._conn = None
                    if attempt:
                        raise

    def query(self, sql, params=None):
        return self.query_many([(sql, params)])[0]


def _num(v):
    return float("nan") if v is None else float(v)


# table -> (Supabase relation, key columns and their types, value columns, how to read the values)
TABLES = {
    "operator": ("flight_operator", [("brand", "text"), ("flight_number", "bigint"), ("origin", "text")],
                 ["operator"], lambda v: v[0]),
    "delay_cause": ("delay_cause_latest", [("carrier", "text"), ("airport", "text"), ("month", "bigint")],
                    PREV_FIELDS, lambda v: dict(zip(PREV_FIELDS, map(_num, v)))),
    "ontime": ("ontime_by_hour_latest", [("airport", "text"), ("month", "bigint"), ("time_block", "text")],
               ["arr_ontime_pct", "dep_ontime_pct"], lambda v: (_num(v[0]), _num(v[1]))),
    "bags": ("carrier_quality_latest", [("carrier", "text"), ("month", "bigint")],
             ["bags_mishandled_per_100"], lambda v: _num(v[0])),
    "congestion_day": ("congestion_day", [("origin", "text"), ("month", "bigint"), ("day_of_week", "bigint")],
                       ["day_flights"], lambda v: _num(v[0])),
    "congestion_hour": ("congestion_hour", [("origin", "text"), ("month", "bigint"), ("day_of_week", "bigint"),
                                            ("dep_hour", "bigint")], ["hour_flights"], lambda v: _num(v[0])),
}


def keyed_select(table, keys):
    """SQL and parameters for just the rows of `table` matching `keys`, joined against the key arrays."""
    relation, key_cols, value_cols, _ = TABLES[table]
    arrays = ", ".join(f"%s::{kind}[]" for _, kind in key_cols)
    names = ", ".join(f"k{i}" for i in range(len(key_cols)))
    on = " AND ".join(f"t.{col} = k.k{i}" for i, (col, _) in enumerate(key_cols))
    columns = ", ".join(f"t.{c}" for c in [c for c, _ in key_cols] + value_cols)
    sql = f"SELECT {columns} FROM unnest({arrays}) AS k({names}) JOIN model.{relation} t ON {on}"
    return sql, [list(col) for col in zip(*sorted(set(keys)))]


class SupabaseLookups:
    """Reads only the lookup rows a request needs; the tables stay in Supabase."""

    def __init__(self, db: Database):
        self.db = db

    def fetch(self, keys_by_table):
        """{table: [keys]} -> {table: {key: value}}, in one round trip."""
        wanted = [(t, k) for t, k in keys_by_table.items() if k]
        results = self.db.query_many([keyed_select(t, k) for t, k in wanted]) if wanted else []
        out = {t: {} for t in keys_by_table}
        for (table, _), rows in zip(wanted, results):
            n, read = len(TABLES[table][1]), TABLES[table][3]
            out[table] = {tuple(r[:n]): read(r[n:]) for r in rows}
        return out

    def meta(self):
        return dict(self.db.query("SELECT key, value FROM model.meta"))


class InMemoryLookups:
    """The same interface over a Lookups held in memory (tests, or a local copy)."""

    def __init__(self, tables: Lookups, meta=None):
        self.tables, self._meta = tables, meta or {}

    def fetch(self, keys_by_table):
        return {t: {k: getattr(self.tables, t)[k] for k in keys if k in getattr(self.tables, t)}
                for t, keys in keys_by_table.items()}

    def meta(self):
        return self._meta


class Weather:
    """Daily weather per (airport, local day), cached for `ttl` seconds."""

    def __init__(self, db: Database, airports, client=None, ttl=30 * 60, today=lambda: datetime.now(UTC).date()):
        self.db, self.airports, self.ttl, self.today = db, airports, ttl, today
        self.client = client or httpx.Client(timeout=15)
        self._cache = {}  # (airport, iso day) -> (values or None, source or None, saved at)
        self._lock = threading.Lock()

    def _fresh(self, key):
        hit = self._cache.get(key)
        return hit is not None and time.monotonic() - hit[2] < self.ttl

    def _save(self, key, values, source):
        self._cache[key] = (values, source, time.monotonic())

    def prefetch(self, pairs):
        """Fill the cache for every (airport, day) in `pairs`: two lookups at most, whatever the count."""
        with self._lock:
            wanted = {(a, d.isoformat()) for a, d in pairs if a in self.airports}
            wanted = {k for k in wanted if not self._fresh(k)}
            if not wanted:
                return
            found = self._from_saved_forecast(wanted)
            for key, values in found.items():
                self._save(key, values, "forecast")
            remaining = wanted - found.keys()
            fetched = self._from_open_meteo(remaining)
            for key, values in fetched.items():
                self._save(key, values, "open-meteo")
            for key in remaining - fetched.keys():
                self._save(key, None, None)

    def get(self, airport, day: date):
        key = (airport, day.isoformat())
        if not self._fresh(key):
            self.prefetch([(airport, day)])
        values, source, _ = self._cache.get(key, (None, None, 0))
        return values, source

    def _from_saved_forecast(self, keys):
        try:
            rows = self.db.query(
                f"SELECT airport, local_date, {', '.join(WEATHER_FIELDS)} FROM live.forecast_daily "
                "WHERE airport = ANY(%s) AND local_date = ANY(%s)",
                (sorted({a for a, _ in keys}), sorted({d for _, d in keys})))
        except psycopg.Error:
            log.exception("reading live.forecast_daily failed")
            return {}
        out = {}
        for airport, day, *values in rows:
            if (airport, day) in keys and values[0] is not None:
                out[(airport, day)] = dict(zip(WEATHER_FIELDS, values))
        return out

    def _from_open_meteo(self, keys):
        today = self.today()
        by_day = {}
        for airport, day in keys:
            offset = (date.fromisoformat(day) - today).days
            if -OPEN_METEO_PAST_DAYS <= offset <= OPEN_METEO_AHEAD_DAYS:
                by_day.setdefault(day, []).append(airport)
        out = {}
        for day, airports in by_day.items():
            airports.sort()
            params = {
                "latitude": ",".join(str(round(self.airports[a][0], 4)) for a in airports),
                "longitude": ",".join(str(round(self.airports[a][1], 4)) for a in airports),
                "daily": ",".join(OPEN_METEO_DAILY), "timezone": "auto", "start_date": day, "end_date": day,
                "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
            }
            try:
                response = self.client.get(OPEN_METEO, params=params)
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                log.exception("Open-Meteo request for %s failed", day)
                continue
            locations = payload if isinstance(payload, list) else [payload]
            for airport, loc in zip(airports, locations):
                daily = loc.get("daily") or {}
                values = {col: (daily.get(name) or [None])[0] for name, col in OPEN_METEO_DAILY.items()}
                if values["weather_code"] is not None and not math.isnan(values["weather_code"]):
                    out[(airport, day)] = values
        return out
