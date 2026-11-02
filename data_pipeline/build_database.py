"""Load the raw downloads into one SQLite database.

    python build_database.py           # everything
    python build_database.py weather   # or: flights, delay_cause, consumer_reports

Each step replaces only its own tables, so weather can be reloaded on its own
after download_data.py fetches more airports.

Tables
  flights                  one row per scheduled flight (BTS on-time data)
  weather_daily            daily weather per airport per local date (Open-Meteo)
  delay_cause_monthly      BTS delay-cause counts per carrier x arrival airport x month
  delay_cause_prev_month   the same stats as rates, keyed to the FOLLOWING month so a
                           flight only sees last month's numbers (no target leakage)
  atcr_airport_ontime_by_hour   DOT Air Travel Consumer Report Tables 3-4: % on time by
                           airport and scheduled hour, 30 largest airports
  atcr_carrier_quality     mishandled baggage and wheelchairs per carrier per month
  atcr_denied_boarding     denied boardings per carrier per quarter
  airlines, airports       lookups

View
  flights_combined         flights joined to weather at both ends, the airline name,
                           and, from the previous month: delay-cause rates for the
                           carrier at both airports, the on-time % for the departure
                           and arrival hour at both airports, and the carrier's
                           mishandled-baggage rate
"""
import json
import sqlite3
import sys
import time
import zipfile

import pandas as pd

from paths import DB_PATH, DELAY_CAUSE_DIR, RAW_DIR, WEATHER_DIR, YEARS, ontime_zip

ATCR_DIR = RAW_DIR / "consumer_reports" / "extracted"

FLIGHT_COLUMNS = {
    "FlightDate": "flight_date",
    "Year": "year",
    "Month": "month",
    "DayofMonth": "day_of_month",
    "DayOfWeek": "day_of_week",
    "Reporting_Airline": "airline",
    "Tail_Number": "tail_number",
    "Flight_Number_Reporting_Airline": "flight_number",
    "Origin": "origin",
    "OriginCityName": "origin_city",
    "OriginState": "origin_state",
    "Dest": "dest",
    "DestCityName": "dest_city",
    "DestState": "dest_state",
    "CRSDepTime": "sched_dep_time",
    "DepTime": "dep_time",
    "DepDelay": "dep_delay",
    "DepDel15": "dep_del15",
    "DepTimeBlk": "dep_time_block",
    "TaxiOut": "taxi_out",
    "WheelsOff": "wheels_off",
    "WheelsOn": "wheels_on",
    "TaxiIn": "taxi_in",
    "CRSArrTime": "sched_arr_time",
    "ArrTime": "arr_time",
    "ArrDelay": "arr_delay",
    "ArrDel15": "arr_del15",
    "Cancelled": "cancelled",
    "CancellationCode": "cancellation_code",
    "Diverted": "diverted",
    "CRSElapsedTime": "sched_elapsed_time",
    "ActualElapsedTime": "actual_elapsed_time",
    "AirTime": "air_time",
    "Distance": "distance",
    "CarrierDelay": "carrier_delay",
    "WeatherDelay": "weather_delay",
    "NASDelay": "nas_delay",
    "SecurityDelay": "security_delay",
    "LateAircraftDelay": "late_aircraft_delay",
}
INT_COLUMNS = ["sched_dep_time", "dep_time", "wheels_off", "wheels_on", "sched_arr_time",
               "arr_time", "dep_del15", "arr_del15", "cancelled", "diverted"]

WEATHER_COLUMNS = {
    "weather_code": "weather_code",
    "temperature_2m_max": "temp_max_f",
    "temperature_2m_min": "temp_min_f",
    "precipitation_sum": "precip_in",
    "rain_sum": "rain_in",
    "snowfall_sum": "snowfall_in",
    "precipitation_hours": "precip_hours",
    "wind_speed_10m_max": "wind_max_mph",
    "wind_gusts_10m_max": "gust_max_mph",
}

PREV_MONTH_COLUMNS = ["arr_flights", "del15_rate", "cancel_rate", "avg_delay_min", "carrier_rate",
                      "weather_rate", "nas_rate", "late_aircraft_rate"]


def load_flights(con):
    con.execute("DROP TABLE IF EXISTS flights")
    total = 0
    for year in YEARS:
        for month in range(1, 13):
            path = ontime_zip(year, month)
            with zipfile.ZipFile(path) as z:
                name = next(n for n in z.namelist() if n.endswith(".csv"))
                df = pd.read_csv(z.open(name), usecols=list(FLIGHT_COLUMNS),
                                 dtype={"Tail_Number": str, "CancellationCode": str})
            df = df.rename(columns=FLIGHT_COLUMNS)[list(FLIGHT_COLUMNS.values())]
            for col in INT_COLUMNS:
                df[col] = df[col].astype("Int64")
            df.to_sql("flights", con, if_exists="append", index=False, chunksize=100_000)
            total += len(df)
            print(f"  flights {year}-{month:02d}: {len(df):,} rows")
    print(f"  {total:,} flights")
    for cols in ["flight_date", "origin, flight_date", "dest, flight_date", "airline, year, month"]:
        con.execute(f"CREATE INDEX idx_flights_{cols.replace(', ', '_')} ON flights ({cols})")


def load_weather(con):
    rows, airports = [], []
    coords = json.loads((WEATHER_DIR / "airports.json").read_text())
    for path in sorted((WEATHER_DIR / "daily").glob("*.json")):
        data = json.loads(path.read_text())
        df = pd.DataFrame(data["daily"]).rename(columns={"time": "date", **WEATHER_COLUMNS})
        df.insert(0, "airport", path.stem)
        rows.append(df)
        lat, lon = coords[path.stem]
        airports.append({"code": path.stem, "latitude": lat, "longitude": lon,
                         "timezone": data["timezone"], "elevation_m": data["elevation"]})
    weather = pd.concat(rows, ignore_index=True)
    con.execute("DROP TABLE IF EXISTS weather_daily")
    con.execute("CREATE TABLE weather_daily (airport TEXT, date TEXT, weather_code INTEGER, "
                + ", ".join(f"{c} REAL" for c in list(WEATHER_COLUMNS.values())[1:])
                + ", PRIMARY KEY (airport, date))")
    weather.to_sql("weather_daily", con, if_exists="append", index=False)
    print(f"  {len(weather):,} weather rows for {len(airports)} airports")
    return pd.DataFrame(airports)


def load_airports(con, weather_airports):
    names = con.execute("""
        SELECT origin, origin_city, origin_state FROM flights GROUP BY origin
        UNION SELECT dest, dest_city, dest_state FROM flights GROUP BY dest
    """).fetchall()
    df = (pd.DataFrame(names, columns=["code", "city", "state"]).drop_duplicates("code")
          .merge(weather_airports, on="code", how="left"))
    df.to_sql("airports", con, if_exists="replace", index=False)
    print(f"  {len(df)} airports, {df['latitude'].isna().sum()} without weather")


def load_delay_causes(con):
    dc = pd.read_csv(DELAY_CAUSE_DIR / "Airline_Delay_Cause.csv")
    dc.to_sql("delay_cause_monthly", con, if_exists="replace", index=False)
    (dc[["carrier", "carrier_name"]].drop_duplicates("carrier", keep="first")
     .to_sql("airlines", con, if_exists="replace", index=False))

    flights = dc["arr_flights"].where(dc["arr_flights"] > 0)
    delayed = dc["arr_del15"].where(dc["arr_del15"] > 0)
    prev = pd.DataFrame({
        "carrier": dc["carrier"],
        "airport": dc["airport"],
        "arr_flights": dc["arr_flights"],
        "del15_rate": dc["arr_del15"] / flights,
        "cancel_rate": dc["arr_cancelled"] / flights,
        "avg_delay_min": dc["arr_delay"] / delayed,
        "carrier_rate": dc["carrier_ct"] / flights,
        "weather_rate": dc["weather_ct"] / flights,
        "nas_rate": dc["nas_ct"] / flights,
        "late_aircraft_rate": dc["late_aircraft_ct"] / flights,
    })
    prev.insert(0, "year", dc["year"])
    prev.insert(1, "month", dc["month"])
    shift_to_next_month(prev).to_sql("delay_cause_prev_month", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX idx_dcpm ON delay_cause_prev_month (carrier, airport, year, month)")
    print(f"  {len(dc):,} delay-cause rows")


def shift_to_next_month(df):
    """Relabel each row with the following month, so December 2023's stats become
    the features for January 2024 flights."""
    next_month = df["year"] * 12 + df["month"]  # months since year 0, zero-based, plus one
    return df.assign(year=next_month // 12, month=next_month % 12 + 1)


def load_consumer_reports(con):
    hourly = pd.read_csv(ATCR_DIR / "airport_ontime_by_hour.csv")
    quality = pd.read_csv(ATCR_DIR / "carrier_quality_monthly.csv")
    denied = pd.read_csv(ATCR_DIR / "denied_boarding_quarterly.csv")
    hourly.to_sql("atcr_airport_ontime_by_hour", con, if_exists="replace", index=False)
    quality.to_sql("atcr_carrier_quality", con, if_exists="replace", index=False)
    denied.to_sql("atcr_denied_boarding", con, if_exists="replace", index=False)

    shift_to_next_month(hourly).to_sql("atcr_ontime_by_hour_prev_month", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX idx_atcr_hourly_prev "
                "ON atcr_ontime_by_hour_prev_month (airport, year, month, time_block)")
    shift_to_next_month(quality).to_sql("atcr_carrier_quality_prev_month", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX idx_atcr_quality_prev ON atcr_carrier_quality_prev_month (carrier, year, month)")
    print(f"  {len(hourly):,} airport-hour rows, {len(quality):,} carrier-months, {len(denied):,} carrier-quarters")


def create_view(con):
    weather = ",\n  ".join(f"{side[0]}w.{c} AS {side}_{c}" for side in ["origin", "dest"]
                           for c in WEATHER_COLUMNS.values())
    delay = ",\n  ".join(f"{side[0]}dc.{c} AS {side}_prev_{c}" for side in ["origin", "dest"]
                         for c in PREV_MONTH_COLUMNS)
    # The consumer reports bucket scheduled times by hour from 0600 to 2259, with
    # 2300-0559 as one overnight block.
    dep_block, arr_block = (
        f"CASE WHEN f.{col} / 100 BETWEEN 6 AND 22 "
        f"THEN printf('%02d00-%02d59', f.{col} / 100, f.{col} / 100) ELSE '2300-0559' END"
        for col in ["sched_dep_time", "sched_arr_time"])
    con.execute("DROP VIEW IF EXISTS flights_combined")
    con.execute(f"""
CREATE VIEW flights_combined AS
SELECT
  f.*,
  al.carrier_name AS airline_name,
  {weather},
  {delay},
  oh.dep_ontime_pct AS origin_prev_hour_dep_ontime_pct,
  dh.arr_ontime_pct AS dest_prev_hour_arr_ontime_pct,
  cq.bags_mishandled_per_100 AS airline_prev_bags_mishandled_per_100
FROM flights f
LEFT JOIN airlines al ON al.carrier = f.airline
LEFT JOIN weather_daily ow ON ow.airport = f.origin AND ow.date = f.flight_date
LEFT JOIN weather_daily dw ON dw.airport = f.dest AND dw.date = f.flight_date
LEFT JOIN delay_cause_prev_month odc
  ON odc.carrier = f.airline AND odc.airport = f.origin AND odc.year = f.year AND odc.month = f.month
LEFT JOIN delay_cause_prev_month ddc
  ON ddc.carrier = f.airline AND ddc.airport = f.dest AND ddc.year = f.year AND ddc.month = f.month
LEFT JOIN atcr_ontime_by_hour_prev_month oh
  ON oh.airport = f.origin AND oh.year = f.year AND oh.month = f.month AND oh.time_block = {dep_block}
LEFT JOIN atcr_ontime_by_hour_prev_month dh
  ON dh.airport = f.dest AND dh.year = f.year AND dh.month = f.month AND dh.time_block = {arr_block}
LEFT JOIN atcr_carrier_quality_prev_month cq
  ON cq.carrier = f.airline AND cq.year = f.year AND cq.month = f.month
""")


def load_weather_and_airports(con):
    load_airports(con, load_weather(con))


STEPS = {"flights": load_flights, "delay_cause": load_delay_causes,
         "consumer_reports": load_consumer_reports, "weather": load_weather_and_airports}


def main(steps):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode = OFF")
    con.execute("PRAGMA synchronous = OFF")
    start = time.time()
    for name in steps:
        print(name)
        STEPS[name](con)
    create_view(con)
    con.commit()
    con.execute("ANALYZE")
    con.close()
    print(f"done in {(time.time() - start) / 60:.1f} min -> {DB_PATH}")


if __name__ == "__main__":
    main(sys.argv[1:] or STEPS)
