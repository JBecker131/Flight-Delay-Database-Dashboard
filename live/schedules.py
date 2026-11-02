"""Saved departures for today and tomorrow at an airport, fetching only the days nobody has loaded yet.

A day is two AeroDataBox requests (the free plan allows 12 hours a request), and its units are counted
before the requests go out, so a failed or slow request can never push the month past its limit.
"""
import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from live import store
from live.airports import by_code

Fetcher = Callable[[str, datetime, datetime], list[dict]]
RETRY_AFTER = timedelta(hours=1)

log = logging.getLogger(__name__)

# Two visitors opening the same unsaved airport at once would otherwise both fetch it.
_locks = defaultdict(threading.Lock)
# (airport, day) -> when fetching it last failed. A failing day isn't retried, or charged for, within the hour.
_failed_at = {}


class QuotaExhausted(Exception):
    """AeroDataBox refused because the plan's quota is spent."""


def local_days(airport, now_utc):
    today = now_utc.astimezone(ZoneInfo(by_code()[airport]["tz"])).date()
    return [today, today + timedelta(days=1)]


def windows(day: date, tz):
    """Request windows covering one local day, each under 12 hours of real time.

    The day clocks go back is 25 hours long, so 00:00-11:59 local would span almost 13 real hours;
    that day gets three 8-hour windows instead of two.
    """
    zone = ZoneInfo(tz)
    start = datetime.combine(day, time(0), zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(0), zone).astimezone(UTC)
    hours = [0, 12] if end - start <= timedelta(hours=24) else [0, 8, 16]
    bounds = hours + [24]
    return [(datetime.combine(day, time(h)), datetime.combine(day, time(0)) + timedelta(hours=nxt, minutes=-1))
            for h, nxt in zip(bounds, bounds[1:])]


def _fetch_day(airport, day, fetcher, tz):
    seen, rows = set(), []
    for start, end in windows(day, tz):
        for row in fetcher(airport, start, end):
            key = (row["flight_number"], row["dep_utc"])  # a flight near a window edge can come back twice
            if key not in seen:
                seen.add(key)
                rows.append(row)
    return rows


def ensure(engine, airport, fetcher: Fetcher, now_utc, unit_limit=store.API_UNIT_LIMIT):
    """Make sure today and tomorrow are saved.

    Returns "ok"; "quota_exhausted" when a day couldn't be fetched for lack of quota; or "error" when a fetch
    failed (it is retried after an hour). Days already saved are kept either way.
    """
    month = now_utc.strftime("%Y-%m")
    tz = by_code()[airport]["tz"]
    status = "ok"
    with _locks[airport]:
        for day in local_days(airport, now_utc):
            failed = _failed_at.get((airport, day))
            if failed and now_utc - failed < RETRY_AFTER:
                status = "error"
                continue
            day_windows = windows(day, tz)
            with engine.begin() as conn:
                if store.fetched(conn, airport, day):
                    continue
                if not store.reserve_units(conn, month, len(day_windows) * store.UNITS_PER_CALL, unit_limit):
                    return "quota_exhausted"
            try:
                rows = _fetch_day(airport, day, fetcher, tz)
            except QuotaExhausted:
                with engine.begin() as conn:
                    store.mark_exhausted(conn, month)
                return "quota_exhausted"
            except Exception:
                log.exception("fetching %s %s failed", airport, day)
                _failed_at[(airport, day)] = now_utc
                status = "error"
                continue
            _failed_at.pop((airport, day), None)
            with engine.begin() as conn:
                store.record_fetch(conn, airport, day, rows)
    return status
