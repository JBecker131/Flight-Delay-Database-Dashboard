from datetime import date

from sqlalchemy import func, insert, select

from live import store


def dep(day, number="AA 1"):
    return {"airport": "CLT", "local_date": day, "airline": "AA", "airline_name": "American", "flight_number": number,
            "dest": "ATL", "dep_local": f"{day}T08:00", "dep_utc": f"{day}T12:00Z"}


def count(conn, table, **where):
    q = select(func.count()).select_from(table)
    for col, val in where.items():
        q = q.where(table.c[col] == val)
    return conn.scalar(q)


def test_reserve_units_stops_at_limit(engine):
    with engine.begin() as conn:
        assert all(store.reserve_units(conn, "2026-11", 2, 380) for _ in range(190))
        assert not store.reserve_units(conn, "2026-11", 2, 380)
        assert conn.scalar(select(store.api_usage.c.units_used)) == 380


def test_reserve_units_separate_months(engine):
    with engine.begin() as conn:
        assert store.reserve_units(conn, "2026-11", 380, 380)
        assert not store.reserve_units(conn, "2026-11", 2, 380)
        assert store.reserve_units(conn, "2026-12", 2, 380)


def test_record_fetch_replaces_rows(engine):
    day = date(2026, 11, 2)
    with engine.begin() as conn:
        store.record_fetch(conn, "CLT", day, [dep("2026-11-02", f"AA {i}") for i in range(3)])
        store.record_fetch(conn, "CLT", day, [dep("2026-11-02", f"AA {i}") for i in range(2)])
        assert count(conn, store.departures) == 2
        assert store.fetched(conn, "CLT", day)
        assert not store.fetched(conn, "CLT", date(2026, 11, 3))


def test_record_fetch_zero_rows_counts_as_fetched(engine):
    with engine.begin() as conn:
        store.record_fetch(conn, "PIR", date(2026, 11, 2), [])
        assert store.fetched(conn, "PIR", date(2026, 11, 2))


def test_purge_before(engine):
    with engine.begin() as conn:
        for day in ("2026-11-01", "2026-11-02"):
            store.record_fetch(conn, "CLT", date.fromisoformat(day), [dep(day)])
            conn.execute(insert(store.forecast_daily).values(airport="CLT", local_date=day))
            conn.execute(insert(store.forecast_hourly).values(airport="CLT", local_date=day, time_local=f"{day}T00:00"))
        store.purge_before(conn, date(2026, 11, 2))
        for table in (store.departures, store.fetched_days, store.forecast_daily, store.forecast_hourly):
            assert count(conn, table, local_date="2026-11-01") == 0
            assert count(conn, table, local_date="2026-11-02") == 1


def test_provider_text_columns_are_unbounded():
    from sqlalchemy import Text
    for name in ("airline_name", "flight_number", "aircraft", "terminal", "gate", "status"):
        assert isinstance(store.departures.c[name].type, Text), name
