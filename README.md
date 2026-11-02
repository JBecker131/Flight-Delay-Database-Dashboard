# Departures: flight delay forecast

A departures board, styled after an airport split-flap display, that shows how likely each flight is to leave late. Select a flight to see its delay risk and the factors behind it.

The flights and predictions are **synthetic** for now. They're generated in the browser and reshuffle each day.

## Run it

It's plain HTML, CSS, and JavaScript, so there's no build step.

```sh
python -m http.server 8765
```

Then open <http://localhost:8765>. You can also open `index.html` directly.

## Files

| File | What it does |
| --- | --- |
| `index.html` | Page structure: sign, filters, board, flight detail panel |
| `css/styles.css` | All styling, including the split-flap tiles |
| `js/data.js` | Synthetic flights and predictions. **This is the file to replace when the model is ready.** |
| `js/splitflap.js` | The flip animation for board tiles |
| `js/app.js` | Filtering, paging, rendering the board, and the detail panel |

## Data

`data_pipeline/` downloads the datasets from the D1 proposal (2023–2025) and combines them into one SQLite database at `~/flight_data/flights.db`. It lives outside OneDrive because the raw files are several GB. Set `FLIGHT_DATA_DIR` to move it.

```sh
cd data_pipeline
python download_data.py     # resumable; re-run to finish weather (Open-Meteo allows ~125 airports a day)
python extract_consumer_reports.py
python build_database.py
python build_database.py weather   # after more weather downloads, reload just that table
```

transportation.gov blocks scripted downloads, so the DOT Air Travel Consumer Reports were saved through a browser into `~/flight_data/raw/consumer_reports/ATCR_<report month>.pdf` (March 2023 – February 2026 reports, which cover January 2023 – December 2025 data). `extract_consumer_reports.py` needs `pip install pdfplumber`.

| Table | Source | Grain |
| --- | --- | --- |
| `flights` | BTS On-Time Reporting | one scheduled flight (~21M rows) |
| `weather_daily` | Open-Meteo archive | airport × local date |
| `delay_cause_monthly` | BTS Delay Causes | carrier × arrival airport × month (from Dec 2022) |
| `delay_cause_prev_month` | derived | the same as rates, shifted forward one month |
| `atcr_airport_ontime_by_hour` | DOT consumer reports, Tables 3–4 | airport × month × scheduled hour, 30 largest airports |
| `atcr_carrier_quality` | DOT consumer reports | carrier × month: mishandled bags and wheelchairs |
| `atcr_denied_boarding` | DOT consumer reports | carrier × quarter: voluntary and involuntary bumps |
| `airlines`, `airports` | derived | lookups |

`flights_combined` is a view that joins each flight to weather at its origin and destination on the flight date and the airline name. It also adds the previous month's stats: delay-cause rates for that carrier at both airports, the on-time % for the flight's departure and arrival hours at both airports, and the carrier's mishandled-bag rate. It uses last month because the current month's numbers include the flight being predicted.

Gaps to know about: the airport-hour stats only cover the 30 largest airports; Endeavor Air (9E) is absent from the 2025 consumer reports; consumer complaint counts aren't included because DOT has delayed publishing them.

```python
import sqlite3, pandas as pd
con = sqlite3.connect(r"C:\Users\Jbeck\flight_data\flights.db")
df = pd.read_sql("SELECT * FROM flights_combined WHERE year = 2024 AND origin = 'CLT'", con)
```

## Database dashboard

`dashboard/` is a Streamlit app that describes the database for the final project. It covers the sources, schema, every column, coverage and the leakage safeguards. Supabase's free plan holds 500 MB, too little for 21M flights, so `publish.py` computes column profiles, histograms, coverage stats and a few hundred sample rows locally and uploads only those (a few MB) into a `dashboard` schema. The app reads that schema and runs on Modal.

```sh
cd dashboard
cp .env.example .env              # paste your Supabase Session pooler URI
python publish.py                 # ~15 min; re-run whenever flights.db changes
streamlit run streamlit_app.py    # try it locally
modal secret create flight-dashboard-db DASHBOARD_DB_URL="<same URI>"
modal deploy modal_app.py
```

Without a `.env`, `publish.py` and the app use a local `~/flight_data/dashboard.db` instead of Supabase. Use Supabase's **Session pooler** URI, not the direct `db.<ref>.supabase.co` one: on the free plan the direct address is IPv6-only, and Modal can't reach it.

Live at <https://jbecke20--flight-delay-database-dashboard.us-east.modal.run>. Re-running `publish.py` updates it within ten minutes, with no redeploy.

## Live departures

`live/` puts real flights on the board, live at <https://jbecke20--flight-departures-board.us-east.modal.run>. It shows today's and tomorrow's departures for any US airport in the data, with forecast weather. It runs as one Modal app with two parts:

- **Nightly job (09:00 UTC).** Refreshes the Open-Meteo forecast for all 362 airports (free), loads Charlotte's newly added day, and deletes days that have passed.
- **Board and API.** The first time anyone opens an airport on a given day, `/api/departures` fetches that airport's schedule from AeroDataBox and saves it in Supabase (`live` schema). Everyone after that gets the saved copy.

AeroDataBox's free Basic plan on RapidAPI gives 400 units a month, and each 12-hour request costs 2. A first open costs 8 units, and an airport opened yesterday costs 4. Visitors can spend up to 380 units, and the rest is kept for the Charlotte preload. When the month runs out, the board says so instead of showing anything made up. Delay chances are placeholders until the model is trained.

```sh
cp live/.env.example live/.env          # add AERODATABOX_KEY
python -m live.capture_sample           # saves one real response as the parser's test fixture (2 units)
python -m pytest live                   # no test calls AeroDataBox or Open-Meteo
python -m live.dev_server --sample      # board on localhost:8766 with replayed BTS flights, no key needed
python -m modal secret create aerodatabox --from-dotenv live/.env
python -m modal run live/modal_app.py::nightly_job
python -m modal deploy live/modal_app.py
```

Re-run `python -m live.export_airports` whenever `build_database.py` adds airports.

## Connecting the model

The board gets flights from `FlightData.loadDepartures()` (`js/data.js`), which maps the live API's rows into this shape:

```js
{
  id: 'AA1423-2026-10-19',
  flightNumber: 'AA 1423',
  airline: { code: 'AA', name: 'American Airlines' },
  origin: { code: 'CLT', city: 'Charlotte', board: 'CHARLOTTE' },
  dest:   { code: 'ATL', city: 'Atlanta',   board: 'ATLANTA' },
  departure: Date,          // scheduled departure, origin local time
  arrival: Date,            // scheduled arrival, destination local time
  durationMin: 75,
  destWeather: {            // forecast at the destination for the arrival time
    condition: 'Thunderstorms',
    icon: 'storm',          // 'clear' | 'cloud' | 'rain' | 'storm' | 'wind' | 'fog'
    tempF: 74,
    windMph: 18,
    windDir: 'SW',          // null when calm
    visibilityMi: 2,
    rainChance: 80
  },
  gate: 'B12',
  aircraft: 'A321',
  prediction: {
    probability: 72,        // 0-100, chance of a delay over 15 minutes
    tier: 'likely',         // 'on-time' (<35), 'possible' (35-59), 'likely' (60+)
    expectedDelayMin: 50,
    baseRisk: 25,           // starting point the factors adjust from
    factors: [              // shown in the detail panel, largest effect first
      { name: 'Weather at departure', detail: 'Thunderstorms in Charlotte', pts: 20 }
    ]
  }
}
```

Until the model is trained, `prediction` comes from placeholder scoring in `js/data.js`. To connect it, have `/api/departures` in `live/api.py` add a `prediction` to each row and use that in `loadDepartures()` instead. The forecast in `live.forecast_daily` uses the same columns as `weather_daily`, so the model's weather features line up. For a tree model, SHAP values map naturally onto `factors[].pts`.
