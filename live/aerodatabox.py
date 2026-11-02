"""AeroDataBox's airport departures board (FIDS), through RapidAPI's free Basic plan.

One request covers at most 12 hours of an airport's departures. Codeshares, cargo and private flights
are left out so each departure appears once; cancelled ones stay in, since they're on the schedule.
"""
import re
import threading
import time

import httpx

from live.airports import by_code
from live.schedules import QuotaExhausted

BASE = "https://aerodatabox.p.rapidapi.com"
HOST = "aerodatabox.p.rapidapi.com"
PARAMS = {"direction": "Departure", "withLeg": "true", "withCancelled": "true", "withCodeshared": "false",
          "withCargo": "false", "withPrivate": "false", "withLocation": "false"}

_last_call = 0.0
_rate_lock = threading.Lock()  # visitors opening different airports share one per-second allowance


class RateLimited(Exception):
    """Too many requests per second. Not the monthly quota, so nothing is marked spent."""


class UnreadablePayload(Exception):
    """Departures came back but none could be read: the response format isn't what parse() expects."""


def _quota_spent(response):
    return (response.headers.get("x-ratelimit-requests-remaining") == "0"
            or "quota" in response.text.lower())


def fetch_window(airport, start_local, end_local, client, min_interval=1.0, retry_wait=2.0):
    global _last_call
    path = f"/flights/airports/iata/{airport}/{start_local:%Y-%m-%dT%H:%M}/{end_local:%Y-%m-%dT%H:%M}"
    for attempt in range(2):
        with _rate_lock:
            wait = _last_call + min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last_call = time.monotonic()
        response = client.get(path, params=PARAMS)
        if response.status_code != 429:
            break
        if _quota_spent(response):
            raise QuotaExhausted(response.text[:200])
        if attempt == 0:
            time.sleep(retry_wait)
    else:
        raise RateLimited(response.text[:200])
    if response.status_code in (204, 404):  # no flights in the window, or an airport AeroDataBox doesn't list
        return {"departures": []}
    response.raise_for_status()
    return response.json()


def _get(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj or None


def _utc(value):  # "2026-11-03 11:00Z" -> "2026-11-03T11:00Z"
    return value.replace(" ", "T") if value else None


def _local(value):  # "2026-11-03 06:00-05:00" -> "2026-11-03T06:00"
    return value[:16].replace(" ", "T") if value else None


def parse(payload, origin):
    airports = by_code()
    rows = []
    for d in payload.get("departures") or []:
        dest = _get(d, "arrival", "airport", "iata")
        dep_utc = _get(d, "departure", "scheduledTime", "utc")
        dep_local = _get(d, "departure", "scheduledTime", "local")
        number = (d.get("number") or "").strip()
        if dest not in airports or dest == origin or not dep_utc or not dep_local or not number:
            continue
        airline = _get(d, "airline", "iata")
        # "B6 123" or "B6123": keep the airline code whole (it can contain digits) and take the number after it
        rest = number.removeprefix(airline).strip() if airline else number
        flight = f"{airline} {rest}" if airline and re.fullmatch(r"\d{1,4}[A-Z]?", rest) else number
        rows.append({
            "airline": airline, "airline_name": _get(d, "airline", "name"),
            "flight_number": flight,
            "dest": dest,
            "dep_local": _local(dep_local), "dep_utc": _utc(dep_utc),
            "arr_local": _local(_get(d, "arrival", "scheduledTime", "local")),
            "arr_utc": _utc(_get(d, "arrival", "scheduledTime", "utc")),
            "aircraft": _get(d, "aircraft", "model"),
            "terminal": _get(d, "departure", "terminal"), "gate": _get(d, "departure", "gate"),
            "status": d.get("status"),
        })
    return rows


def make_client(key):
    return httpx.Client(base_url=BASE, timeout=30, headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": HOST})


def make_fetcher(key, client=None, min_interval=1.0):
    client = client or make_client(key)
    airports = by_code()

    def fetch(airport, start_local, end_local):
        payload = fetch_window(airport, start_local, end_local, client, min_interval=min_interval)
        rows = parse(payload, airport)
        listed = [d for d in payload.get("departures") or []
                  if _get(d, "arrival", "airport", "iata") in airports]
        if listed and not rows:
            raise UnreadablePayload(f"{len(listed)} departures from {airport}, none readable")
        return rows

    return fetch
