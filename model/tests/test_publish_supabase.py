import sqlite3

import pandas as pd
import pytest

from model import publish_supabase as pub


@pytest.fixture
def con():
    """A tiny flights table with only the columns the builders read."""
    c = sqlite3.connect(":memory:")
    c.execute("""CREATE TABLE flights (flight_date TEXT, month INTEGER, day_of_week INTEGER, airline TEXT,
                                       flight_number INTEGER, origin TEXT, sched_dep_time INTEGER)""")
    yield c
    c.close()


def add(con, rows):
    con.executemany("INSERT INTO flights VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def lookup(table, brand, number, origin):
    hit = table[(table.brand == brand) & (table.flight_number == number) & (table.origin == origin)]
    return hit.operator.item() if len(hit) else None


def test_operator_takes_the_regional_that_flew_the_number_from_that_airport(con):
    add(con, [("2025-11-03", 11, 1, "OH", 5069, "CLT", 900)] * 3
             + [("2025-11-04", 11, 2, "MQ", 5069, "CLT", 900)]
             + [("2025-11-03", 11, 1, "MQ", 5069, "DFW", 1200)])
    table = pub.build_flight_operator(con, since="2025-07-01")
    assert lookup(table, "AA", 5069, "CLT") == "OH"
    assert lookup(table, "AA", 5069, "DFW") == "MQ"
    # The number-wide fallback covers airports the number wasn't seen at.
    assert lookup(table, "AA", 5069, "*") == "OH"


def test_operator_omits_mainline_flights_and_other_brands_partners(con):
    add(con, [("2025-11-03", 11, 1, "AA", 1812, "CLT", 900),
              ("2025-11-03", 11, 1, "9E", 4999, "CLT", 900)])  # Endeavor flies only for Delta
    table = pub.build_flight_operator(con, since="2025-07-01")
    assert lookup(table, "AA", 1812, "CLT") is None   # missing means the brand operates it
    assert lookup(table, "AA", 4999, "CLT") is None
    assert lookup(table, "DL", 4999, "CLT") == "9E"


def test_operator_ignores_flights_before_the_window(con):
    add(con, [("2025-01-03", 1, 5, "OH", 5069, "CLT", 900)])
    assert pub.build_flight_operator(con, since="2025-07-01").empty


def test_congestion_averages_over_the_days_the_airport_was_busy(con):
    # Two Mondays in November at CLT: 3 then 1 departures in the 9 o'clock hour, plus one at 2400.
    add(con, [("2025-11-03", 11, 1, "AA", n, "CLT", 905) for n in range(3)]
             + [("2025-11-10", 11, 1, "AA", 9, "CLT", 930),
                ("2025-11-10", 11, 1, "AA", 10, "CLT", 2400)])
    day, hour = pub.build_congestion(con, since="2025-01-01")
    row = hour.set_index(["origin", "month", "day_of_week", "dep_hour"]).loc[("CLT", 11, 1, 9)]
    assert row.hour_flights == pytest.approx(2.0)
    assert ("CLT", 11, 1, 0) in hour.set_index(["origin", "month", "day_of_week", "dep_hour"]).index
    assert day.set_index(["origin", "month", "day_of_week"]).loc[("CLT", 11, 1)].day_flights == pytest.approx(2.5)


def test_pg_rows_are_plain_python_values():
    df = pd.DataFrame({"a": [1, 2], "b": [1.5, None], "c": ["x", None]})
    rows = list(pub.pg_rows(df))
    assert rows == [(1, 1.5, "x"), (2, None, None)]
    assert type(rows[0][0]) is int
