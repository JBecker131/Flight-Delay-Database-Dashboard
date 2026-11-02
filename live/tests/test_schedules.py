import threading
import time
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, insert, select

from live import schedules, store

NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)  # 10 AM in Charlotte


class FakeFetcher:
    def __init__(self, fail_on=None, delay=0.0):
        self.calls, self.fail_on, self.delay = [], fail_on, delay

    def __call__(self, airport, start, end):
        self.calls.append((airport, start, end))
        time.sleep(self.delay)
        if self.fail_on and self.fail_on(len(self.calls)):
            raise self.fail_on.error
        return [{"airline": "AA", "flight_number": f"AA {len(self.calls)}", "dest": "ATL",
                 "dep_local": start.strftime("%Y-%m-%dT%H:%M"), "dep_utc": "2026-11-02T12:00Z"}]


def fail(when, error):
    when.error = error
    return when


def usage(engine):
    with engine.begin() as conn:
        return conn.scalar(select(store.api_usage.c.units_used)) or 0


def set_usage(engine, units):
    with engine.begin() as conn:
        conn.execute(insert(store.api_usage).values(month="2026-11", units_used=units))


def test_first_open_fetches_two_days(engine):
    fetcher = FakeFetcher()
    assert schedules.ensure(engine, "CLT", fetcher, NOW) == "ok"
    assert len(fetcher.calls) == 4
    assert usage(engine) == 8
    assert [c[1] for c in fetcher.calls] == [datetime(2026, 11, 2, 0, 0), datetime(2026, 11, 2, 12, 0),
                                             datetime(2026, 11, 3, 0, 0), datetime(2026, 11, 3, 12, 0)]
    assert fetcher.calls[0][2] == datetime(2026, 11, 2, 11, 59)


def test_reopen_same_day_is_free(engine):
    schedules.ensure(engine, "CLT", FakeFetcher(), NOW)
    fetcher = FakeFetcher()
    assert schedules.ensure(engine, "CLT", fetcher, NOW) == "ok"
    assert fetcher.calls == []


def test_next_day_fetches_only_new_day(engine):
    schedules.ensure(engine, "CLT", FakeFetcher(), NOW)
    fetcher = FakeFetcher()
    schedules.ensure(engine, "CLT", fetcher, NOW + timedelta(days=1))
    assert len(fetcher.calls) == 2
    assert {c[1].date() for c in fetcher.calls} == {date(2026, 11, 4)}


def test_quota_reached_returns_status_without_calling(engine):
    set_usage(engine, 377)
    fetcher = FakeFetcher()
    assert schedules.ensure(engine, "CLT", fetcher, NOW) == "quota_exhausted"
    assert fetcher.calls == []


def test_preload_limit_allows_up_to_400(engine):
    set_usage(engine, 377)
    fetcher = FakeFetcher()
    assert schedules.ensure(engine, "CLT", fetcher, NOW, unit_limit=store.PRELOAD_UNIT_LIMIT) == "ok"
    assert len(fetcher.calls) == 4


def test_provider_429_marks_month_exhausted(engine):
    schedules.ensure(engine, "CLT", FakeFetcher(), NOW)
    fetcher = FakeFetcher(fail_on=fail(lambda n: True, schedules.QuotaExhausted()))
    assert schedules.ensure(engine, "CLT", fetcher, NOW + timedelta(days=1)) == "quota_exhausted"
    assert usage(engine) == store.PRELOAD_UNIT_LIMIT
    with engine.begin() as conn:
        saved = conn.scalar(select(func.count()).select_from(store.departures)
                            .where(store.departures.c.local_date == "2026-11-03"))
    assert saved == 2


def test_half_fetched_day_is_not_marked_fetched(engine):
    fetcher = FakeFetcher(fail_on=fail(lambda n: n == 2, RuntimeError("timeout")))
    assert schedules.ensure(engine, "CLT", fetcher, NOW) == "error"
    with engine.begin() as conn:
        assert not store.fetched(conn, "CLT", date(2026, 11, 2))
        assert store.fetched(conn, "CLT", date(2026, 11, 3))  # the other day still loads
    assert usage(engine) == 8  # the failed day's units stay spent


def test_failure_keeps_saved_days_and_cools_down(engine):
    schedules.ensure(engine, "CLT", FakeFetcher(), NOW)
    failing = FakeFetcher(fail_on=fail(lambda n: True, RuntimeError("400 bad request")))
    later = NOW + timedelta(days=1)
    assert schedules.ensure(engine, "CLT", failing, later) == "error"
    assert schedules.ensure(engine, "CLT", failing, later + timedelta(minutes=30)) == "error"
    assert len(failing.calls) == 1  # no retry inside the cooldown, so no more units spent
    assert usage(engine) == 12
    schedules.ensure(engine, "CLT", failing, later + timedelta(minutes=61))
    assert len(failing.calls) == 2


def test_dst_fall_back_day_uses_three_windows():
    day = date(2026, 11, 1)  # clocks go back an hour in New York: a 25-hour day
    assert len(schedules.windows(day, "America/New_York")) == 3
    assert len(schedules.windows(day, "America/Phoenix")) == 2
    assert len(schedules.windows(date(2026, 11, 2), "America/New_York")) == 2
    for start, end in schedules.windows(day, "America/New_York"):
        assert end - start < timedelta(hours=12)


def test_rows_seen_in_both_windows_are_saved_once(engine):
    def fetcher(airport, start, end):
        return [{"airline": "AA", "flight_number": "AA 1", "dest": "ATL", "dep_local": "2026-11-02T11:59",
                 "dep_utc": "2026-11-02T16:59Z"}]
    schedules.ensure(engine, "CLT", fetcher, NOW)
    with engine.begin() as conn:
        assert conn.scalar(select(func.count()).select_from(store.departures)
                           .where(store.departures.c.local_date == "2026-11-02")) == 1


def test_today_uses_airport_timezone():
    now = datetime(2026, 11, 3, 6, 30, tzinfo=UTC)
    assert schedules.local_days("HNL", now) == [date(2026, 11, 2), date(2026, 11, 3)]
    assert schedules.local_days("CLT", now) == [date(2026, 11, 3), date(2026, 11, 4)]


def test_concurrent_first_opens_fetch_once(engine):
    fetcher = FakeFetcher(delay=0.2)
    threads = [threading.Thread(target=schedules.ensure, args=(engine, "ATL", fetcher, NOW)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(fetcher.calls) == 4
