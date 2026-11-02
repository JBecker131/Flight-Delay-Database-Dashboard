"""Tables for the live board: forecast, saved schedules, and the AeroDataBox quota counter.

DASHBOARD_DB_URL picks the database, read from the environment, then live/.env, then dashboard/.env.
Without it everything goes to a local SQLite file. On Postgres the tables live in their own `live`
schema, kept off the public schema that Supabase exposes through its REST API.
Dates are stored as ISO strings so the same code runs on SQLite and Postgres.
"""
import os
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import (Column, Float, Integer, MetaData, String, Table, Text, create_engine, delete, exists, insert,
                        select, text, update)

HERE = Path(__file__).parent
ENV_FILES = [HERE / ".env", HERE.parent / "dashboard" / ".env"]
LOCAL_URL = "sqlite:///" + str(Path(os.environ.get("FLIGHT_DATA_DIR", Path.home() / "flight_data"))
                               / "live.db").replace("\\", "/")
POSTGRES_SCHEMA = "live"

# AeroDataBox bills its airport-departures endpoint as Tier 2: 2 units a request. The free plan has
# 400 units a month. Visitors may spend up to 380; the rest is kept for the nightly Charlotte preload.
UNITS_PER_CALL = 2
API_UNIT_LIMIT = 380
PRELOAD_UNIT_LIMIT = 400

metadata = MetaData()

forecast_daily = Table(
    "forecast_daily", metadata,
    Column("airport", String(3), primary_key=True),
    Column("local_date", String(10), primary_key=True),
    Column("weather_code", Integer), Column("temp_max_f", Float), Column("temp_min_f", Float),
    Column("precip_in", Float), Column("rain_in", Float), Column("snowfall_in", Float),
    Column("precip_hours", Float), Column("wind_max_mph", Float), Column("gust_max_mph", Float),
    Column("precip_prob_max", Float), Column("fetched_at", String(20)),
)

forecast_hourly = Table(
    "forecast_hourly", metadata,
    Column("airport", String(3), primary_key=True),
    Column("time_local", String(16), primary_key=True),  # YYYY-MM-DDTHH:MM, airport local time
    Column("local_date", String(10), index=True),
    Column("temp_f", Float), Column("weather_code", Integer), Column("wind_mph", Float),
    Column("wind_dir_deg", Float), Column("visibility_mi", Float), Column("precip_prob", Float),
)

departures = Table(
    "departures", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("airport", String(3), nullable=False),
    Column("local_date", String(10), nullable=False),
    # Text for anything AeroDataBox supplies: Postgres enforces VARCHAR lengths, so a long aircraft name
    # would otherwise fail the whole day's insert.
    Column("airline", Text), Column("airline_name", Text),
    Column("flight_number", Text), Column("dest", String(3)),
    Column("dep_local", Text), Column("dep_utc", Text),
    Column("arr_local", Text), Column("arr_utc", Text),
    Column("aircraft", Text), Column("terminal", Text), Column("gate", Text),
    Column("status", Text), Column("fetched_at", String(20)),
)

# A row means the day was fetched, even when the airport had no departures that day.
fetched_days = Table(
    "fetched_days", metadata,
    Column("airport", String(3), primary_key=True),
    Column("local_date", String(10), primary_key=True),
    Column("fetched_at", String(20)),
)

api_usage = Table(
    "api_usage", metadata,
    Column("month", String(7), primary_key=True),  # YYYY-MM, UTC
    Column("units_used", Integer, nullable=False),
)


def database_url():
    url = os.environ.get("DASHBOARD_DB_URL")
    for env_file in ENV_FILES:
        if url or not env_file.exists():
            continue
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "DASHBOARD_DB_URL":
                url = value.strip().strip("'\"")
    url = url or LOCAL_URL
    # Supabase hands out postgresql:// URLs; SQLAlchemy needs to be told to use psycopg 3.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


def get_engine():
    engine = create_engine(database_url(), pool_pre_ping=True)
    if engine.dialect.name == "postgresql":
        engine = engine.execution_options(schema_translate_map={None: POSTGRES_SCHEMA})
    return engine


def create_tables(engine):
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {POSTGRES_SCHEMA}"))
    metadata.create_all(engine)


def now_iso():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_month(conn, month):
    if not conn.scalar(select(exists().where(api_usage.c.month == month))):
        conn.execute(insert(api_usage).values(month=month, units_used=0))


def reserve_units(conn, month, units, limit):
    """Count `units` against the month unless that would pass `limit`. Returns whether it did."""
    _ensure_month(conn, month)
    result = conn.execute(update(api_usage)
                          .where(api_usage.c.month == month, api_usage.c.units_used + units <= limit)
                          .values(units_used=api_usage.c.units_used + units))
    return result.rowcount == 1


def mark_exhausted(conn, month):
    """AeroDataBox refused for quota, so stop all fetching until the month changes."""
    _ensure_month(conn, month)
    conn.execute(update(api_usage).where(api_usage.c.month == month).values(units_used=PRELOAD_UNIT_LIMIT))


def fetched(conn, airport, day):
    return conn.scalar(select(exists().where(fetched_days.c.airport == airport,
                                             fetched_days.c.local_date == day.isoformat())))


def record_fetch(conn, airport, day, rows):
    """Replace one airport-day of departures and mark it fetched."""
    iso, stamp = day.isoformat(), now_iso()
    conn.execute(delete(departures).where(departures.c.airport == airport, departures.c.local_date == iso))
    conn.execute(delete(fetched_days).where(fetched_days.c.airport == airport, fetched_days.c.local_date == iso))
    if rows:
        conn.execute(insert(departures), [{**r, "airport": airport, "local_date": iso, "fetched_at": stamp}
                                          for r in rows])
    conn.execute(insert(fetched_days).values(airport=airport, local_date=iso, fetched_at=stamp))


def purge_before(conn, day: date):
    for table in (departures, fetched_days, forecast_daily, forecast_hourly):
        conn.execute(delete(table).where(table.c.local_date < day.isoformat()))
