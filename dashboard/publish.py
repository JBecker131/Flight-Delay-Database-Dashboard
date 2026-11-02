"""Compute everything the dashboard shows from flights.db and upload it.

    python publish.py

Reads ~/flight_data/flights.db and writes a few MB of summary tables to
DASHBOARD_DB_URL (see store.py): your Supabase database, or a local SQLite file
when that is unset. Most of the ~10 minutes is one pass over flights_combined.
Re-run it whenever the database changes, e.g. after more weather is downloaded;
the live dashboard picks up the new numbers within ten minutes.

Tables written
  meta                 key/value facts: publish time, file sizes, download progress
  catalog              one row per table or view: source, grain, row and column counts
  columns              one row per column: meaning, role, type, % missing, distinct, range
  histograms           the distribution of every column, ready to chart
  samples              a few hundred random rows per table, plus the leakage example
  coverage_monthly     per month, how many flights got each kind of joined context
  airport_coverage     per airport, flights and whether it has weather and hourly stats
"""
import json
import random
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

import catalog
from store import get_engine, schema_for

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "data_pipeline"))
from paths import DB_PATH, ONTIME_DIR, RAW_DIR, WEATHER_DIR  # noqa: E402

CHUNK_ROWS = 500_000
SAMPLE_ROWS = 300
MAX_BAR_VALUES = 40  # numeric columns with more distinct values than this get binned
BINS = 30
TOP_TEXT_VALUES = 15
HHMM_COLUMNS = {"sched_dep_time", "dep_time", "wheels_off", "wheels_on", "sched_arr_time", "arr_time"}

# The column in flights_combined that shows whether each kind of context was joined.
COVERAGE_GROUPS = {
    "Weather at origin": "origin_temp_max_f",
    "Weather at destination": "dest_temp_max_f",
    "Delay-cause rates, origin": "origin_prev_del15_rate",
    "Delay-cause rates, destination": "dest_prev_del15_rate",
    "Hourly on-time %, origin": "origin_prev_hour_dep_ontime_pct",
    "Hourly on-time %, destination": "dest_prev_hour_arr_ontime_pct",
    "Carrier baggage rate": "airline_prev_bags_mishandled_per_100",
    "Target (dep_del15)": "dep_del15",
}


class Profiler:
    """Accumulates exact value counts per column over chunks of a table."""

    def __init__(self, columns):
        self.counts = {c: pd.Series(dtype=float) for c in columns}
        self.nulls = dict.fromkeys(columns, 0)
        self.rows = 0

    def add(self, df):
        self.rows += len(df)
        for col, counts in self.counts.items():
            self.nulls[col] += int(df[col].isna().sum())
            self.counts[col] = counts.add(df[col].value_counts(), fill_value=0)


def fmt(value):
    if isinstance(value, (float, np.floating)):
        if float(value).is_integer():
            return f"{int(value):,}" if abs(value) >= 10_000 else str(int(value))
        return f"{value:,.3g}" if abs(value) >= 1 else f"{value:.3g}"
    return str(value)


def weighted_quantile(values, weights, p):
    cum = np.cumsum(weights) / weights.sum()
    return values[min(np.searchsorted(cum, p), len(values) - 1)]


def histogram(col, counts, numeric):
    """[(label, lo, hi, count)] describing the column's distribution."""
    if col in HHMM_COLUMNS:
        hours = counts.groupby((counts.index.astype(int) // 100) % 24).sum()
        return [(f"{h:02d}:00", h, h + 1, hours.get(h, 0)) for h in range(24)]
    if col.endswith("date"):
        months = counts.groupby(counts.index.str[:7]).sum().sort_index()
        return [(m, None, None, n) for m, n in months.items()]
    if not numeric:
        top = counts.sort_values(ascending=False)
        buckets = [(str(k), None, None, n) for k, n in top.head(TOP_TEXT_VALUES).items()]
        if len(top) > TOP_TEXT_VALUES:
            buckets.append((f"Other ({len(top) - TOP_TEXT_VALUES:,} values)", None, None,
                            top.iloc[TOP_TEXT_VALUES:].sum()))
        return buckets
    counts = counts.sort_index()
    if len(counts) <= MAX_BAR_VALUES:
        return [(fmt(k), k, k, n) for k, n in counts.items()]
    # Bin between the 0.5th and 99.5th percentiles, folding the long tails into
    # the end bins so a few extreme delays don't flatten the chart.
    values, weights = counts.index.to_numpy(float), counts.to_numpy(float)
    lo, hi = weighted_quantile(values, weights, 0.005), weighted_quantile(values, weights, 0.995)
    if hi <= lo:  # nearly every row has the same value, e.g. security_delay is almost always 0
        lo, hi = values[0], values[-1]
    edges = np.linspace(lo, hi, BINS + 1)
    idx = np.clip(np.searchsorted(edges, values, side="right") - 1, 0, BINS - 1)
    binned = np.bincount(idx, weights=weights, minlength=BINS)
    return [(f"{fmt(edges[i])} to {fmt(edges[i + 1])}", edges[i], edges[i + 1], binned[i])
            for i in range(BINS)]


def summarize(table, profiler, types):
    """(column rows, histogram rows) for one table."""
    columns, buckets = [], []
    for ordinal, (col, counts) in enumerate(profiler.counts.items()):
        counts = counts[counts > 0]
        numeric = pd.api.types.is_numeric_dtype(counts.index) and types[col] != "TEXT"
        if table in ("flights", "flights_combined"):
            role, description = catalog.combined_column(col)
        else:
            role, description = None, catalog.COLUMNS[table].get(col, "")
        row = {"table_name": table, "ordinal": ordinal, "column_name": col, "sql_type": types[col],
               "role": role, "description": description, "n_rows": profiler.rows,
               "n_nulls": profiler.nulls[col],
               "null_pct": 100 * profiler.nulls[col] / profiler.rows if profiler.rows else 0.0,
               "n_distinct": len(counts), "min_value": None, "max_value": None,
               "mean": None, "median": None}
        if len(counts):
            ordered = counts.sort_index()
            row["min_value"], row["max_value"] = fmt(ordered.index[0]), fmt(ordered.index[-1])
            if numeric:
                values, weights = ordered.index.to_numpy(float), ordered.to_numpy(float)
                row["mean"] = float((values * weights).sum() / weights.sum())
                row["median"] = float(weighted_quantile(values, weights, 0.5))
        top = counts.sort_values(ascending=False).head(5)
        row["top_values"] = ", ".join(f"{fmt(k)} ({100 * n / profiler.rows:.1f}%)" for k, n in top.items())
        columns.append(row)
        for order, (label, lo, hi, n) in enumerate(histogram(col, counts, numeric)):
            buckets.append({"table_name": table, "column_name": col, "bucket": order, "label": label,
                            "lo": None if lo is None else float(lo), "hi": None if hi is None else float(hi),
                            "count": float(n)})
    return columns, buckets


def column_types(con, table):
    return {r[1]: r[2] for r in con.execute(f"PRAGMA table_info({table})")}


def combined_select(con):
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name = 'flights_combined'").fetchone()[0]
    return sql[sql.index("SELECT"):]


def scan_combined(con):
    """One pass over flights_combined: value counts for every column, coverage per month."""
    select = combined_select(con)
    types = column_types(con, "flights_combined")
    profiler = Profiler(list(types))
    coverage = []
    last = con.execute("SELECT max(rowid) FROM flights").fetchone()[0]
    start = time.time()
    for first in range(1, last + 1, CHUNK_ROWS):
        df = pd.read_sql(f"{select} WHERE f.rowid BETWEEN {first} AND {first + CHUNK_ROWS - 1}", con)
        profiler.add(df)
        present = df[list(COVERAGE_GROUPS.values())].notna()
        present[["year", "month"]] = df[["year", "month"]]
        present["flights"] = 1
        present["cancelled"] = df["cancelled"]
        coverage.append(present.groupby(["year", "month"]).sum())
        done = min(first + CHUNK_ROWS - 1, last)
        print(f"  {done:,} / {last:,} flights  ({time.time() - start:.0f}s)", flush=True)
    by_month = pd.concat(coverage).groupby(level=[0, 1]).sum().reset_index()
    long = by_month.melt(id_vars=["year", "month", "flights", "cancelled"],
                         value_vars=list(COVERAGE_GROUPS.values()), var_name="column", value_name="covered")
    long.insert(2, "context", long["column"].map({v: k for k, v in COVERAGE_GROUPS.items()}))
    return profiler, types, long


def sample_combined(con, select):
    last = con.execute("SELECT max(rowid) FROM flights").fetchone()[0]
    rowids = sorted(random.Random(2025).sample(range(1, last + 1), SAMPLE_ROWS))
    return pd.read_sql(f"{select} WHERE f.rowid IN ({','.join(map(str, rowids))})", con)


def leakage_example(con):
    """Show one month's stats moving forward: AA at CLT, January 2024 -> February 2024 flights."""
    source = pd.read_sql("""
        SELECT year, month, carrier, airport, arr_flights, arr_del15, arr_cancelled, arr_delay,
               carrier_ct, weather_ct, nas_ct, late_aircraft_ct
        FROM delay_cause_monthly WHERE carrier = 'AA' AND airport = 'CLT' AND year = 2024 AND month = 1""", con)
    shifted = pd.read_sql("""
        SELECT * FROM delay_cause_prev_month
        WHERE carrier = 'AA' AND airport = 'CLT' AND year = 2024 AND month = 2""", con)
    flights = pd.read_sql("""
        SELECT flight_date, airline, flight_number, origin, dest, sched_dep_time,
               origin_prev_del15_rate, origin_prev_cancel_rate, origin_prev_avg_delay_min, dep_del15
        FROM flights_combined WHERE origin = 'CLT' AND airline = 'AA' AND flight_date = '2024-02-14'
        ORDER BY sched_dep_time LIMIT 5""", con)
    return {"example:source": source, "example:shifted": shifted, "example:flights": flights}


def airport_coverage(con, profiler):
    coords = json.loads((WEATHER_DIR / "airports.json").read_text())
    with_weather = {r[0] for r in con.execute("SELECT DISTINCT airport FROM weather_daily")}
    hourly = {r[0] for r in con.execute("SELECT DISTINCT airport FROM atcr_airport_ontime_by_hour")}
    airports = pd.read_sql("SELECT code, city, state FROM airports", con)
    airports["latitude"] = airports["code"].map(lambda c: coords.get(c, (None, None))[0])
    airports["longitude"] = airports["code"].map(lambda c: coords.get(c, (None, None))[1])
    airports["departures"] = airports["code"].map(profiler.counts["origin"]).fillna(0)
    airports["arrivals"] = airports["code"].map(profiler.counts["dest"]).fillna(0)
    airports["has_weather"] = airports["code"].isin(with_weather)
    airports["in_hourly_stats"] = airports["code"].isin(hourly)
    return airports.sort_values("departures", ascending=False)


def meta(con, profiler):
    dates = profiler.counts["flight_date"].index
    # Count what is loaded in the database, which can lag the files on disk until
    # build_database.py weather is re-run.
    airports, with_weather = con.execute(
        "SELECT count(*), sum(code IN (SELECT DISTINCT airport FROM weather_daily)) FROM airports").fetchone()
    return pd.DataFrame([
        ("published_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
        ("flights_db_bytes", DB_PATH.stat().st_size),
        ("ontime_zip_files", len(list(ONTIME_DIR.glob("*.zip")))),
        ("ontime_zip_bytes", sum(p.stat().st_size for p in ONTIME_DIR.glob("*.zip"))),
        ("atcr_reports", len(list((RAW_DIR / "consumer_reports").glob("ATCR_*.pdf")))),
        ("weather_airports_downloaded", with_weather),
        ("weather_airports_needed", airports),
        ("first_flight_date", min(dates)),
        ("last_flight_date", max(dates)),
    ], columns=["key", "value"]).astype(str)


def main():
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    start = time.time()

    print("flights_combined (one pass over every flight)")
    combined, combined_types, coverage = scan_combined(con)
    profiles = {"flights_combined": (combined, combined_types)}

    # flights is the first 39 columns of flights_combined with the same rows, so
    # its profile comes from the same pass.
    flights_types = column_types(con, "flights")
    flights = Profiler(list(flights_types))
    flights.rows = combined.rows
    flights.counts = {c: combined.counts[c] for c in flights_types}
    flights.nulls = {c: combined.nulls[c] for c in flights_types}
    profiles["flights"] = (flights, flights_types)

    print("smaller tables")
    small = {}
    for table in catalog.TABLES:
        if table in profiles:
            continue
        df = pd.read_sql(f"SELECT * FROM {table}", con)
        profiler = Profiler(list(df.columns))
        profiler.add(df)
        profiles[table] = (profiler, column_types(con, table))
        small[table] = df
        print(f"  {table}: {len(df):,} rows")

    column_rows, bucket_rows, catalog_rows = [], [], []
    for order, (table, info) in enumerate(catalog.TABLES.items()):
        profiler, types = profiles[table]
        cols, buckets = summarize(table, profiler, types)
        column_rows += cols
        bucket_rows += buckets
        catalog_rows.append({"table_name": table, "display_order": order, **info,
                             "n_rows": profiler.rows, "n_columns": len(types)})

    print("samples")
    select = combined_select(con)
    combined_sample = sample_combined(con, select)
    samples = {"flights_combined": combined_sample,
               "flights": combined_sample[list(flights_types)]}
    for table, df in small.items():
        samples[table] = df.sample(min(SAMPLE_ROWS, len(df)), random_state=2025).sort_index()
    samples.update(leakage_example(con))

    tables = {
        "meta": meta(con, combined),
        "catalog": pd.DataFrame(catalog_rows),
        "columns": pd.DataFrame(column_rows),
        "histograms": pd.DataFrame(bucket_rows),
        "samples": pd.DataFrame([{"table_name": name, "data_json": df.to_json(orient="split", index=False)}
                                 for name, df in samples.items()]),
        "coverage_monthly": coverage,
        "airport_coverage": airport_coverage(con, combined),
    }

    engine = get_engine()
    schema = schema_for(engine)
    print(f"uploading to {engine.dialect.name}" + (f" schema {schema}" if schema else ""))
    if schema:
        with engine.begin() as c:
            c.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    for name, df in tables.items():
        df.to_sql(name, engine, schema=schema, if_exists="replace", index=False,
                  method="multi", chunksize=500)
        print(f"  {name}: {len(df):,} rows")
    print(f"done in {(time.time() - start) / 60:.1f} min")


if __name__ == "__main__":
    main()
