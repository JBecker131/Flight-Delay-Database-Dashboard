"""Download the raw datasets listed in the D1 proposal.

    python download_data.py            # everything
    python download_data.py flights    # or: delay_cause, weather

Files that already exist are skipped, so it is safe to re-run after a failure.

Sources:
  flights      BTS On-Time Reporting Carrier Performance, one zip per month
  delay_cause  BTS Airline On-Time Statistics and Delay Causes (carrier x airport x month)
  weather      Open-Meteo historical archive, daily weather for every airport in the data
"""
import csv
import json
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import pandas as pd

from paths import (DELAY_CAUSE_DIR, END_DATE, ONTIME_DIR, START_DATE, WEATHER_DIR,
                   YEARS, ontime_zip)

USER_AGENT = "UNCC-DataScience-Group1/1.0 (course project)"

WEATHER_VARS = [
    "weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_sum",
    "rain_sum", "snowfall_sum", "precipitation_hours", "wind_speed_10m_max",
    "wind_gusts_10m_max",
]


def fetch(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=300) as resp, open(tmp, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    tmp.replace(dest)


def download_flights():
    ONTIME_DIR.mkdir(parents=True, exist_ok=True)
    for year in YEARS:
        for month in range(1, 13):
            dest = ontime_zip(year, month)
            if dest.exists() and zipfile.is_zipfile(dest):
                continue
            print(f"flights {year}-{month:02d}")
            fetch(f"https://transtats.bts.gov/PREZIP/{dest.name}", dest)


def bts_obfuscate(text):
    # The BTS download links encode their query string with a rotate-13 cipher
    # over a-z, 0-9, A-Z.
    alphabet = string.ascii_lowercase + string.digits + string.ascii_uppercase
    return "".join(alphabet[(alphabet.index(c) + 13) % 62] if c in alphabet else c for c in text)


def download_delay_cause():
    DELAY_CAUSE_DIR.mkdir(parents=True, exist_ok=True)
    dest = DELAY_CAUSE_DIR / "Airline_Delay_Cause.zip"
    if not dest.exists():
        # BTS identifies a month as year * 12 + month. Start one month early so
        # January of the first year has a previous month to look back on.
        first, last = YEARS[0] * 12, YEARS[-1] * 12 + 12
        query = bts_obfuscate(f"where yearmonth between {first} AND {last}")
        print("delay causes")
        fetch("https://www.transtats.bts.gov/ot_delay/ot_delaycause1_DL.aspx?8n4="
              + urllib.parse.quote(query), dest)
    with zipfile.ZipFile(dest) as z:
        z.extract("Airline_Delay_Cause.csv", DELAY_CAUSE_DIR)


def airports_in_data():
    """Every airport code that appears in the delay-cause file or the flight files."""
    codes = set(pd.read_csv(DELAY_CAUSE_DIR / "Airline_Delay_Cause.csv", usecols=["airport"])["airport"])
    for path in sorted(ONTIME_DIR.glob("*.zip")):
        if not zipfile.is_zipfile(path):  # still downloading
            continue
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith(".csv"))
            df = pd.read_csv(z.open(name), usecols=["Origin", "Dest"])
        codes |= set(df["Origin"]) | set(df["Dest"])
    return sorted(codes)


def airport_traffic():
    """Arriving flights per airport over the whole period, used to fetch busy airports first."""
    dc = pd.read_csv(DELAY_CAUSE_DIR / "Airline_Delay_Cause.csv", usecols=["airport", "arr_flights"])
    return dc.groupby("airport")["arr_flights"].sum().to_dict()


# Airports missing from OurAirports.
EXTRA_COORDINATES = {
    "PBI": (26.6832, -80.0956),  # Palm Beach International
}


def airport_coordinates():
    """IATA code -> (lat, lon), from OurAirports."""
    path = WEATHER_DIR / "ourairports.csv"
    if not path.exists():
        fetch("https://davidmegginson.github.io/ourairports-data/airports.csv", path)
    coords = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = row["iata_code"]
            # Several closed or minor fields can share a code; prefer the one that has scheduled service.
            if code and (code not in coords or row["scheduled_service"] == "yes"):
                coords[code] = (float(row["latitude_deg"]), float(row["longitude_deg"]))
    return {**coords, **EXTRA_COORDINATES}


def download_weather():
    out_dir = WEATHER_DIR / "daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    coords = airport_coordinates()
    airports = airports_in_data()
    missing = [a for a in airports if a not in coords]
    if missing:
        print("no coordinates for:", " ".join(missing))

    with open(WEATHER_DIR / "airports.json", "w") as f:
        json.dump({a: coords[a] for a in airports if a in coords}, f)

    # Open-Meteo's free tier bills a 3-year request as ~78 calls, so only ~125
    # airports fit in a day. Fetch the busiest first; re-running fills in the rest.
    traffic = airport_traffic()
    todo = [a for a in airports if a in coords and not (out_dir / f"{a}.json").exists()]
    todo.sort(key=lambda a: -traffic.get(a, 0))
    print(f"weather: {len(airports) - len(todo)} of {len(airports)} airports already downloaded")
    for i, code in enumerate(todo, 1):
        lat, lon = coords[code]
        params = {
            "latitude": lat, "longitude": lon, "start_date": START_DATE, "end_date": END_DATE,
            "daily": ",".join(WEATHER_VARS), "timezone": "auto",
            "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
        }
        url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(params)
        while True:
            try:
                fetch(url, out_dir / f"{code}.json")
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace").lower()
                if e.code != 429:
                    raise
                if "daily" in body:
                    sys.exit(f"Open-Meteo daily limit reached after {i - 1} airports; re-run tomorrow.")
                wait = 600 if "hourly" in body else 60
                print(f"  rate limited, waiting {wait}s")
                time.sleep(wait)
        print(f"  [{i}/{len(todo)}] {code}")
        time.sleep(1)


STEPS = {"flights": download_flights, "delay_cause": download_delay_cause, "weather": download_weather}

if __name__ == "__main__":
    for step in sys.argv[1:] or STEPS:
        STEPS[step]()
