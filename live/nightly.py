"""The nightly batch: drop days that have passed, refresh every airport's forecast, preload Charlotte."""
from datetime import timedelta

from live import forecast, schedules, store

HOME = "CLT"


def run(engine, fetcher, now_utc):
    store.create_tables(engine)
    with engine.begin() as conn:
        store.purge_before(conn, now_utc.date() - timedelta(days=1))
    airports = forecast.refresh(engine)
    status = schedules.ensure(engine, HOME, fetcher, now_utc, unit_limit=store.PRELOAD_UNIT_LIMIT)
    return {"forecast_airports": airports, "clt": status}
