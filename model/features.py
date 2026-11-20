"""Turn a flight sent to the API into the model's 53 inputs, built the way training built them.

Training read every input from the flights_combined view (data_pipeline/build_database.py). Here
each one comes from a lookup that model/publish_supabase.py copied into Supabase, keyed the same way:

  schedule        the request: local departure (and arrival) time, airline, airports; distance is
                  great-circle miles, within a few miles of BTS's figure
  weather         that day's forecast at the origin and at the destination, both on the
                  departure date as in training (see model/lookups.py for where it comes from)
  carrier quality the latest year's row for the flight's calendar month, i.e. the month before's rates
  congestion      typical scheduled departures at the origin that day of the week and that hour

The airline is the one that operates the flight, as BTS reports it: a brand flight number flown by
a regional ("AA 5069" from CLT, flown by PSA Airlines) is scored as that regional (OH).

A flight is scored in three steps, so a batch needs only a few keyed queries in all:
  schedule()        parse the request; needs only the airports
  operating_carrier() and table_keys()   which lookup rows the flight needs
  build()           the feature row, from those rows and the weather
"""
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

WEATHER_FIELDS = ["weather_code", "temp_max_f", "temp_min_f", "precip_in", "rain_in", "snowfall_in",
                  "precip_hours", "wind_max_mph", "gust_max_mph"]
PREV_FIELDS = ["arr_flights", "del15_rate", "cancel_rate", "avg_delay_min", "carrier_rate",
               "weather_rate", "nas_rate", "late_aircraft_rate"]
# Brands whose flight numbers can be flown by a regional that reports to BTS (see publish_supabase.py).
REGIONAL_BRANDS = {"AA", "DL", "UA", "AS"}
EARTH_MILES = 3958.8
SIDE_NAMES = {"origin": "origin", "dest": "destination"}
NAN = float("nan")


class FlightError(ValueError):
    """The flight can't be scored: an unknown airport, an unreadable time, and so on."""


@dataclass
class Flight:
    airline: str
    origin: str
    dest: str
    departure: str              # local time at the origin, "2026-11-21T09:05" (or with an offset)
    arrival: str | None = None  # local time at the destination
    flight_number: str | int | None = None


@dataclass
class Schedule:
    """A flight's parsed request."""
    brand: str
    number: int | None
    origin: str
    dest: str
    dep: datetime               # local at the origin
    arr: datetime               # local at the destination
    elapsed: float              # scheduled minutes
    distance: float             # miles
    notes: list = field(default_factory=list)


@dataclass
class Lookups:
    """Lookup rows keyed like the Supabase tables; holding only the keys a request needs is fine."""
    operator: dict = field(default_factory=dict)         # (brand, number, origin or '*') -> carrier
    delay_cause: dict = field(default_factory=dict)      # (carrier, airport, month) -> {PREV_FIELDS}
    ontime: dict = field(default_factory=dict)           # (airport, month, block) -> (arr pct, dep pct)
    bags: dict = field(default_factory=dict)             # (carrier, month) -> mishandled per 100
    congestion_day: dict = field(default_factory=dict)   # (origin, month, BTS weekday) -> departures
    congestion_hour: dict = field(default_factory=dict)  # (origin, month, BTS weekday, hour) -> departures


def time_block(hour):
    """The consumer reports' hourly blocks: 0600-0659 ... 2200-2259, then 2300-0559 overnight."""
    return f"{hour:02d}00-{hour:02d}59" if 6 <= hour <= 22 else "2300-0559"


def miles_between(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_MILES * math.asin(math.sqrt(h))


def local_time(value, tz, what):
    """A naive time is local to `tz`; one with an offset (or Z) is converted to it."""
    try:
        when = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        raise FlightError(f"{what} {value!r} isn't an ISO date and time like 2026-11-21T09:05") from None
    zone = ZoneInfo(tz)
    return when.replace(tzinfo=zone) if when.tzinfo is None else when.astimezone(zone)


def flight_digits(number, brand):
    """5069 from 5069, "5069", "AA 5069" or "AA5069"; None if there's no number."""
    if number is None:
        return None
    text = str(number).strip().upper().removeprefix(brand).strip()
    match = re.match(r"\d{1,4}", text)
    return int(match.group()) if match else None


def schedule(flight: Flight, airports, elapsed_fit) -> Schedule:
    """`airports` maps code -> (lat, lon, tz). Without an arrival time, the trip length is estimated."""
    brand, origin, dest = (s.strip().upper() for s in (flight.airline, flight.origin, flight.dest))
    for code in (origin, dest):
        if code not in airports:
            raise FlightError(f"unknown airport {code}")
    if origin == dest:
        raise FlightError("origin and destination are the same airport")
    o_lat, o_lon, o_tz = airports[origin]
    d_lat, d_lon, d_tz = airports[dest]
    notes = []
    dep = local_time(flight.departure, o_tz, "departure")
    distance = miles_between((o_lat, o_lon), (d_lat, d_lon))
    if flight.arrival:
        arr = local_time(flight.arrival, d_tz, "arrival")
        elapsed = (arr - dep).total_seconds() / 60
        if not 0 < elapsed < 24 * 60:
            raise FlightError("arrival must be after departure and within a day of it")
    else:
        elapsed = round(elapsed_fit["intercept"] + elapsed_fit["per_mile"] * distance)
        arr = (dep + timedelta(minutes=elapsed)).astimezone(ZoneInfo(d_tz))
        notes.append(f"no arrival time: assumed a typical {elapsed:.0f}-minute trip")
    return Schedule(brand, flight_digits(flight.flight_number, brand), origin, dest, dep, arr,
                    float(elapsed), distance, notes)


def operator_keys(s: Schedule):
    if s.brand not in REGIONAL_BRANDS or s.number is None:
        return []
    return [(s.brand, s.number, s.origin), (s.brand, s.number, "*")]


def operating_carrier(s: Schedule, operators):
    for key in operator_keys(s):
        if key in operators:
            return operators[key]
    return s.brand


def table_keys(s: Schedule, carrier):
    """The rows of each lookup table this flight needs, by table."""
    month, dow = s.dep.month, s.dep.isoweekday()
    return {
        "delay_cause": [(carrier, s.origin, month), (carrier, s.dest, month)],
        "ontime": [(s.origin, month, time_block(s.dep.hour)), (s.dest, month, time_block(s.arr.hour))],
        "bags": [(carrier, month)],
        "congestion_day": [(s.origin, month, dow)],
        "congestion_hour": [(s.origin, month, dow, s.dep.hour)],
    }


def storm(code):
    return NAN if code is None else float(code >= 95)


def snow(code):
    return NAN if code is None else float(71 <= code <= 77 or 85 <= code <= 86)


def build(s: Schedule, carrier, lookups: Lookups, weather):
    """The feature dict for one flight, plus notes on what had to be estimated or was missing.

    `weather(airport, day)` returns that day's WEATHER_FIELDS (or None) and where they came from.
    """
    notes = list(s.notes)
    if carrier != s.brand:
        notes.append(f"{s.brand} {s.number} from {s.origin} is operated by {carrier}")
    day, month, dow, dep_hour, arr_hour = s.dep.date(), s.dep.month, s.dep.isoweekday(), s.dep.hour, s.arr.hour
    row = {"month": month, "day_of_week": dow, "day_of_month": day.day, "dep_hour": dep_hour,
           "arr_hour": arr_hour, "sched_elapsed_time": s.elapsed, "distance": round(s.distance),
           "airline": carrier, "origin": s.origin, "dest": s.dest}

    sources = {}
    for side, airport in (("origin", s.origin), ("dest", s.dest)):
        values, source = weather(airport, day)
        sources[side] = source
        for f in WEATHER_FIELDS:
            v = (values or {}).get(f)
            row[f"{side}_{f}"] = NAN if v is None else float(v)
        code = (values or {}).get("weather_code")
        row[f"{side}_storm"], row[f"{side}_snow"] = storm(code), snow(code)
    missing = [SIDE_NAMES[side] for side, source in sources.items() if source is None]
    if missing:
        notes.append(f"no weather forecast for the {' or '.join(missing)} on {day}")

    for side, airport in (("origin", s.origin), ("dest", s.dest)):
        stats = lookups.delay_cause.get((carrier, airport, month), {})
        for f in PREV_FIELDS:
            row[f"{side}_prev_{f}"] = stats.get(f, NAN)
    row["origin_prev_hour_dep_ontime_pct"] = lookups.ontime.get((s.origin, month, time_block(dep_hour)), (NAN, NAN))[1]
    row["dest_prev_hour_arr_ontime_pct"] = lookups.ontime.get((s.dest, month, time_block(arr_hour)), (NAN, NAN))[0]
    row["airline_prev_bags_mishandled_per_100"] = lookups.bags.get((carrier, month), NAN)

    # Training counted every scheduled flight, so each flight's own hour held at least itself.
    hour_flights = lookups.congestion_hour.get((s.origin, month, dow, dep_hour), 1.0)
    row["origin_hour_flights"] = hour_flights
    row["origin_day_flights"] = lookups.congestion_day.get((s.origin, month, dow), hour_flights)
    return row, {"operating_airline": carrier, "weather": sources, "notes": notes}


def to_frame(rows, features, levels):
    """Rows as the model's input: its column order, and its categories with unseen codes as OTHER."""
    df = pd.DataFrame(rows, columns=features)
    for col, cats in levels.items():
        df[col] = pd.Categorical(df[col].where(df[col].isin(cats), "OTHER"), categories=cats)
    numeric = [c for c in features if c not in levels]
    df[numeric] = df[numeric].astype("float64")
    return df
