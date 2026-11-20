"""Copy what the delay model needs at prediction time from flights.db into Supabase.

    python model/publish_supabase.py

The 20.9M flights stay local; what goes up is about 20 MB of lookups. It all goes in its own
`model` schema, kept off the public schema that Supabase exposes through its REST API, and is
replaced in one transaction, so an app reading it never sees a half-written copy. Re-run it
whenever flights.db changes. DASHBOARD_DB_URL (see dashboard/store.py) picks the database.

Tables (day_of_week is BTS's: 1 = Monday ... 7 = Sunday)
  airports                         code, lat/lon, timezone: distance and local hours
  delay_cause_prev_month           origin_prev_* / dest_prev_*, by carrier, airport, year, month
  atcr_ontime_by_hour_prev_month   origin_prev_hour_dep_ontime_pct / dest_prev_hour_arr_ontime_pct
  atcr_carrier_quality_prev_month  airline_prev_bags_mishandled_per_100, by carrier
  flight_operator                  the regional airline that flies a brand's flight number (AA 5069 -> OH)
  congestion_day                   origin_day_flights: typical departures that day
  congestion_hour                  origin_hour_flights: typical departures in that hour
  meta                             publish time and how far the BTS data runs

The *_prev_month tables are keyed by the month of the flight; each row holds the month before's
rates, as in training. BTS data stops at 2025, so the views *_latest give each key's most recent
year for every calendar month: a November 2026 flight is scored with the November 2025 row.
"""
import sqlite3
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "data_pipeline"))
sys.path.insert(0, str(ROOT / "dashboard"))
from paths import DB_PATH  # noqa: E402
from store import database_url  # noqa: E402

SCHEMA = "model"

# The regional airlines that reported to BTS in 2025, and the brands each one flies for. Regionals
# that don't report (Piedmont, Mesa, Horizon, ...) aren't in the training data, so their flights
# keep the brand's code.
REGIONAL_PARTNERS = {
    "AA": ["MQ", "OH", "OO", "YX"],
    "DL": ["9E", "OO", "YX"],
    "UA": ["OO", "YX"],
    "AS": ["OO"],
}

COPIED_TABLES = {
    "airports": ["code"],
    "delay_cause_prev_month": ["carrier", "airport", "year", "month"],
    "atcr_ontime_by_hour_prev_month": ["airport", "year", "month", "time_block"],
    "atcr_carrier_quality_prev_month": ["carrier", "year", "month"],
}

LATEST_VIEWS = {
    "delay_cause_latest": ("delay_cause_prev_month", ["carrier", "airport", "month"]),
    "ontime_by_hour_latest": ("atcr_ontime_by_hour_prev_month", ["airport", "month", "time_block"]),
    "carrier_quality_latest": ("atcr_carrier_quality_prev_month", ["carrier", "month"]),
}


def build_flight_operator(con, since):
    """For each brand flight number, which carrier flew it in BTS since `since`.

    One row per (brand, flight_number, origin), plus an origin '*' row per number for airports it
    wasn't seen at. Only numbers a regional flew are kept: a missing number means the brand itself.
    """
    carriers = sorted(set(REGIONAL_PARTNERS) | {c for cs in REGIONAL_PARTNERS.values() for c in cs})
    flown = pd.read_sql(f"""
        SELECT airline, flight_number, origin, COUNT(*) AS flights
        FROM flights
        WHERE flight_date >= ? AND airline IN ({', '.join('?' * len(carriers))})
        GROUP BY airline, flight_number, origin
    """, con, params=[since, *carriers])

    tables = []
    for brand, partners in REGIONAL_PARTNERS.items():
        mine = flown[flown.airline.isin([brand, *partners])]
        anywhere = mine.groupby(["airline", "flight_number"], as_index=False)["flights"].sum().assign(origin="*")
        for counts in (mine, anywhere):
            keys = ["flight_number", "origin"]
            total = counts.groupby(keys)["flights"].transform("sum")
            best = counts.assign(share=counts.flights / total).sort_values("flights", ascending=False)
            best = best.drop_duplicates(keys)
            tables.append(best[best.airline != brand].rename(columns={"airline": "operator"}).assign(brand=brand))

    out = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
    if out.empty:
        return pd.DataFrame(columns=["brand", "flight_number", "origin", "operator", "flights", "share"])
    out["share"] = out["share"].round(3)
    return out[["brand", "flight_number", "origin", "operator", "flights", "share"]].sort_values(
        ["brand", "flight_number", "origin"], ignore_index=True)


def build_congestion(con, since):
    """Average scheduled departures per origin and day, and per origin and hour, since `since`.

    The counts match the training features: every scheduled flight, cancelled ones included, with
    the average taken over the days (or hours) the airport had departures.
    """
    hourly = pd.read_sql("""
        SELECT origin, flight_date, month, day_of_week, (sched_dep_time / 100) % 24 AS dep_hour,
               COUNT(*) AS n
        FROM flights
        WHERE flight_date >= ?
        GROUP BY origin, flight_date, dep_hour
    """, con, params=[since])
    day_keys = ["origin", "month", "day_of_week"]
    daily = hourly.groupby(["origin", "flight_date", "month", "day_of_week"], as_index=False)["n"].sum()
    day = daily.groupby(day_keys, as_index=False)["n"].mean().rename(columns={"n": "day_flights"})
    hour = (hourly.groupby(day_keys + ["dep_hour"], as_index=False)["n"].mean()
            .rename(columns={"n": "hour_flights"}))
    return day.round(2), hour.round(2)


def pg_rows(df):
    """DataFrame rows as plain Python values, with None for missing, ready for COPY."""
    return df.astype(object).where(df.notna(), None).itertuples(index=False, name=None)


def pg_type(dtype):
    return {"i": "bigint", "f": "double precision", "b": "boolean"}.get(dtype.kind, "text")


def write_table(cur, name, df, key):
    columns = ", ".join(f'"{c}" {pg_type(df[c].dtype)}' for c in df.columns)
    cur.execute(f"DROP TABLE IF EXISTS {SCHEMA}.{name} CASCADE")
    cur.execute(f"CREATE TABLE {SCHEMA}.{name} ({columns}, PRIMARY KEY ({', '.join(key)}))")
    with cur.copy(f"COPY {SCHEMA}.{name} ({', '.join(df.columns)}) FROM STDIN") as copy:
        for row in pg_rows(df):
            copy.write_row(row)
    print(f"  {SCHEMA}.{name}: {len(df):,} rows")


def latest_view_sql(view, table, key):
    keys = ", ".join(key)
    return (f"CREATE VIEW {SCHEMA}.{view} AS SELECT DISTINCT ON ({keys}) * FROM {SCHEMA}.{table} "
            f"ORDER BY {keys}, year DESC")


def months_before(day, months):
    total = day.year * 12 + day.month - 1 - months
    return date(total // 12, total % 12 + 1, 1)


def main():
    url = database_url()
    if not url.startswith("postgresql"):
        sys.exit("DASHBOARD_DB_URL isn't set to a Postgres database (see dashboard/.env.example).")

    con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    last = date.fromisoformat(con.execute("SELECT MAX(flight_date) FROM flights").fetchone()[0])
    operator_since = months_before(last, 5).isoformat()     # the last 6 months: numbers get reassigned
    congestion_since = months_before(last, 11).isoformat()  # the last 12 months: every calendar month

    print(f"reading {DB_PATH} (flights through {last})")
    frames = {name: (pd.read_sql(f"SELECT * FROM {name}", con), key) for name, key in COPIED_TABLES.items()}
    frames["flight_operator"] = (build_flight_operator(con, operator_since), ["brand", "flight_number", "origin"])
    day, hour = build_congestion(con, congestion_since)
    con.close()
    frames["congestion_day"] = (day, ["origin", "month", "day_of_week"])
    frames["congestion_hour"] = (hour, ["origin", "month", "day_of_week", "dep_hour"])
    frames["meta"] = (pd.DataFrame({"key": ["published_at", "bts_flights_through", "operator_window_start",
                                            "congestion_window_start"],
                                    "value": [datetime.now(UTC).isoformat(timespec="seconds"), last.isoformat(),
                                              operator_since, congestion_since]}), ["key"])

    print("writing to Supabase")
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as pg, pg.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        for name, (df, key) in frames.items():
            write_table(cur, name, df, key)
        for view, (table, key) in LATEST_VIEWS.items():
            cur.execute(latest_view_sql(view, table, key))
        cur.execute(f"SELECT pg_size_pretty(SUM(pg_total_relation_size(c.oid))) FROM pg_class c "
                    f"JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = '{SCHEMA}'")
        print(f"done: the {SCHEMA} schema takes {cur.fetchone()[0]}")


if __name__ == "__main__":
    main()
