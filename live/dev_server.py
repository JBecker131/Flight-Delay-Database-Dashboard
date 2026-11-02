"""Run the board and API on this computer.

    python -m live.dev_server            # real AeroDataBox schedules (key in live/.env), shared Supabase tables
    python -m live.dev_server --sample   # no key needed: replays December 2025 BTS flights for the same weekday

--sample is for checking the board's layout only. It writes to a local SQLite file, never to Supabase,
and this file isn't deployed (modal_app.py leaves it out).
"""
import argparse
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
from sqlalchemy import create_engine

from live import aerodatabox, forecast, store
from live.airports import by_code
from live.api import create_app
from live.capture_sample import api_key

ROOT = Path(__file__).parent.parent
FLIGHTS_DB = Path.home() / "flight_data" / "flights.db"
SAMPLE_DB = "sqlite:///" + str(Path.home() / "flight_data" / "live-sample.db").replace("\\", "/")


def sample_fetcher(airport, start, end):
    con = sqlite3.connect(f"file:{FLIGHTS_DB}?mode=ro", uri=True)
    weekday = start.isoweekday()
    source = con.execute("SELECT max(flight_date) FROM flights WHERE flight_date LIKE '2025-12-%' "
                         "AND day_of_week = ?", [weekday]).fetchone()[0]
    rows = con.execute(
        "SELECT f.airline, a.carrier_name, f.flight_number, f.dest, f.sched_dep_time, f.sched_elapsed_time "
        "FROM flights f LEFT JOIN airlines a ON a.carrier = f.airline "
        "WHERE f.origin = ? AND f.flight_date = ? AND f.sched_dep_time BETWEEN ? AND ?",
        [airport, source, start.hour * 100 + start.minute, end.hour * 100 + end.minute]).fetchall()
    origin_tz = ZoneInfo(by_code()[airport]["tz"])
    out = []
    for airline, name, number, dest, hhmm, elapsed in rows:
        if dest not in by_code():
            continue
        dep_local = start.replace(hour=hhmm // 100 % 24, minute=hhmm % 100)
        dep = dep_local.replace(tzinfo=origin_tz).astimezone(UTC)
        arr = dep + timedelta(minutes=elapsed or 120)
        arr_local = arr.astimezone(ZoneInfo(by_code()[dest]["tz"]))
        out.append({"airline": airline, "airline_name": name, "flight_number": f"{airline} {number}", "dest": dest,
                    "dep_local": f"{dep_local:%Y-%m-%dT%H:%M}", "dep_utc": f"{dep:%Y-%m-%dT%H:%MZ}",
                    "arr_local": f"{arr_local:%Y-%m-%dT%H:%M}", "arr_utc": f"{arr:%Y-%m-%dT%H:%MZ}",
                    "aircraft": None, "terminal": None, "gate": None, "status": "Sample"})
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if args.sample:
        engine, fetcher = create_engine(SAMPLE_DB), sample_fetcher
        store.create_tables(engine)
        print(f"forecast: {forecast.refresh(engine)} airports")
    else:
        engine, fetcher = store.get_engine(), aerodatabox.make_fetcher(api_key())
        store.create_tables(engine)
    uvicorn.run(create_app(engine, fetcher, board_dir=ROOT), port=args.port)


if __name__ == "__main__":
    main()
