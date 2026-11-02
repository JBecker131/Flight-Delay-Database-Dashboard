"""Write airports.json from flights.db, so the deployed app knows every airport without the 4 GB database.

    python -m live.export_airports    # re-run after build_database.py adds airports
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "flight_data" / "flights.db"
OUT = Path(__file__).with_name("airports.json")


def board_name(city):
    return city.split(",")[0].upper()[:18]


def main():
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    airports = [
        {"code": code, "city": city.split(",")[0], "board": board_name(city), "state": state,
         "lat": lat, "lon": lon, "tz": tz}
        for code, city, state, lat, lon, tz in con.execute(
            "SELECT code, city, state, latitude, longitude, timezone FROM airports "
            "WHERE latitude IS NOT NULL AND timezone IS NOT NULL ORDER BY code")
    ]
    airlines = dict(con.execute("SELECT carrier, carrier_name FROM airlines ORDER BY carrier"))
    OUT.write_text(json.dumps({"airports": airports, "airlines": airlines}, indent=1))
    print(f"{len(airports)} airports, {len(airlines)} airlines -> {OUT.name}")


if __name__ == "__main__":
    main()
