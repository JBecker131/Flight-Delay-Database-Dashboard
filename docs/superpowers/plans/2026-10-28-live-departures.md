# Live Departures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The departures board shows real departures for today and tomorrow from any US airport, with real forecast
weather. Schedules are fetched from AeroDataBox's free plan when an airport is first opened and shared through
Supabase, and a nightly Modal job refreshes the forecast and preloads Charlotte.

**Architecture:** A new `live/` Python package holds the code: forecast, AeroDataBox client, schedule cache with
quota accounting, and a FastAPI app. It runs as one Modal app: an ASGI web function that serves the static board
plus `/api/*`, and a cron function. Data lives in a `live` schema in the existing Supabase project; local runs and
tests use SQLite. The board's `js/data.js` stops generating flights and fetches from the API.

**Tech Stack:** Python 3.12, FastAPI 0.141, SQLAlchemy 2.1 Core, psycopg 3, httpx 0.28, Modal 1.5.4, pytest;
plain JS on the board.

**Spec:** `docs/superpowers/specs/2026-10-28-live-departures-design.md`

The project folder is not a git repository, so tasks end at passing tests and have no commit steps.

## Global Constraints

- Window is today and tomorrow in each airport's local date. No third day.
- AeroDataBox: free Basic plan via RapidAPI. Every request costs `UNITS_PER_CALL = 2`. A request covers at most 12
  hours, so one day is two requests: `00:00–11:59` and `12:00–23:59` local.
- Quota: the API path refuses new fetches once a month has used `API_UNIT_LIMIT = 380` units. The nightly
  Charlotte preload may go up to `PRELOAD_UNIT_LIMIT = 400`.
- Never show made-up flights. When nothing can be fetched, the response says why.
- The API key is read only from env `AERODATABOX_KEY` (Modal secret `aerodatabox`, or `live/.env` locally).
  It never appears in the page or in any file in `live/` except `.env`, and `.env` is excluded from the Modal image.
- Supabase URL comes from env `DASHBOARD_DB_URL` (existing Modal secret `flight-dashboard-db`). Tables go in schema
  `live` on Postgres and have no schema on SQLite.
- Keep only departures whose destination is in `live/airports.json`, i.e. airports in the BTS data.
- The board opens on Charlotte (`CLT`). There is no "All airports" option.
- Predictions stay placeholders until the model exists, and the board says so in visible text.
- No test calls AeroDataBox or Open-Meteo over the network. Live calls only in the explicit verification steps.

## Review Focus

1. **Two visitors open the same uncached airport at the same moment.** Expect exactly one fetch per missing day
   (per-airport lock). Task 4: `test_concurrent_first_opens_fetch_once`.
2. **The provider says the quota is spent before our counter does** (HTTP 429, or a billing cycle that doesn't match
   the calendar month). Expect the month to be marked exhausted, saved days still served, and status
   `quota_exhausted`. Task 4: `test_provider_429_marks_month_exhausted`. Task 5: `test_client_raises_quota_error_on_429`.
3. **Airports whose local date differs from UTC.** Honolulu at 20:00 local is already tomorrow in UTC. Expect
   "today" to follow the airport's timezone. Task 4: `test_today_uses_airport_timezone`.
4. **A departure with a non-BTS destination or missing fields** (no arrival time, gate, terminal or aircraft).
   Expect it dropped (destination) or kept with nulls (fields), never a crash.
   Task 5: `test_parse_drops_unknown_destinations`, `test_parse_tolerates_missing_fields`.
5. **One of a day's two window requests fails.** Expect the day not recorded as fetched, so a later open retries,
   with units already spent still counted. Task 4: `test_half_fetched_day_is_not_marked_fetched`.

---

### Task 1: Package scaffold and the airport list

**Files:**
- Create: `live/__init__.py`, `live/export_airports.py`, `live/airports.json` (generated), `live/requirements.txt`,
  `live/.env.example`, `live/tests/__init__.py`, `live/tests/conftest.py`
- Test: `live/tests/test_airports.py`

**Interfaces:**
- Produces: `live/airports.json` with `{"airports": [{"code", "city", "board", "state", "lat", "lon", "tz"}],
  "airlines": {"AA": "American Airlines Network", ...}}`. `board` is the city before the first comma, uppercased
  and truncated to 18 characters, e.g. `"Allentown/Bethlehem/Easton, PA"` → `"ALLENTOWN/BETHLEHE"`.
- Produces: `live.airports.load() -> dict` (cached), `live.airports.by_code() -> dict[str, dict]`.
- Produces: pytest fixture `engine` in `conftest.py`: a SQLite in-memory SQLAlchemy engine with `live.store.create_tables(engine)`
  applied. Task 2 defines `create_tables`; add the fixture in Task 2.

- [ ] **Step 1:** `pip install pytest` and create `live/requirements.txt`: `fastapi==0.141.1`, `httpx==0.28.1`,
  `sqlalchemy==2.1.6`, `psycopg[binary]==3.3.6`, `uvicorn==<the version pip resolves with fastapi>`.
- [ ] **Step 2: Write the failing test** `test_airports.py::test_airports_json_covers_bts_airports`: asserts
  `len(load()["airports"]) == 362`, `by_code()["CLT"]["tz"] == "America/New_York"`, `by_code()["CLT"]["board"] == "CHARLOTTE"`,
  and `"AA" in load()["airlines"]`.
- [ ] **Step 3: Run** `python -m pytest live/tests/test_airports.py -v`. Expected: FAIL (module or file missing).
- [ ] **Step 4:** Implement `live/export_airports.py`. It reads `airports` and `airlines` from `~/flight_data/flights.db`
  (read-only URI, as `dashboard/publish.py` does) and writes `live/airports.json`. Run it once. Implement
  `live/airports.py`.
- [ ] **Step 5: Run** the test. Expected: PASS.

### Task 2: Store: tables, quota and fetched-day bookkeeping

**Files:**
- Create: `live/store.py`
- Modify: `live/tests/conftest.py` (add `engine` fixture)
- Test: `live/tests/test_store.py`

**Interfaces:**
- Produces in `live/store.py`:
  - `get_engine() -> Engine`: URL from env `DASHBOARD_DB_URL`, then `live/.env`, then `../dashboard/.env`, else
    `sqlite:///~/flight_data/live.db`. Normalize `postgres://` to `postgresql+psycopg://`, as `dashboard/store.py:database_url` does.
  - `create_tables(engine) -> None`: SQLAlchemy Core `MetaData(schema="live" if postgres else None)` with
    `forecast_daily`, `forecast_hourly`, `departures`, `fetched_days`, `api_usage`. Columns as in the spec.
    `departures` also has `dep_utc` and `arr_utc` (ISO strings) and `airline_name`. On Postgres, runs `CREATE SCHEMA IF NOT EXISTS live` first.
  - `reserve_units(conn, month: str, units: int, limit: int) -> bool`: adds `units` to `api_usage[month]` only if the
    new total is ≤ `limit`; returns whether it did. Month is `"YYYY-MM"` in UTC.
  - `mark_exhausted(conn, month: str) -> None`: sets `units_used` to `PRELOAD_UNIT_LIMIT`.
  - `fetched(conn, airport: str, day: date) -> bool`, `record_fetch(conn, airport, day, rows: list[dict]) -> None`:
    in one transaction, replaces `departures` for (airport, day) and inserts `fetched_days`.
  - `purge_before(conn, day: date) -> None`: deletes all rows with `local_date < day` in all three dated tables.
  - Constants `UNITS_PER_CALL = 2`, `API_UNIT_LIMIT = 380`, `PRELOAD_UNIT_LIMIT = 400`.

- [ ] **Step 1: Write the failing tests:**
  - `test_reserve_units_stops_at_limit`: 190 calls of `reserve_units(conn, "2026-11", 2, 380)` return True (reaching exactly
    380 is allowed) and the 191st returns False (382 is not).
  - `test_reserve_units_separate_months`: `"2026-11"` at 380 doesn't block `"2026-12"`.
  - `test_record_fetch_replaces_rows`: recording CLT 2026-11-02 twice with 3 then 2 rows leaves 2 rows and `fetched()` is True.
  - `test_record_fetch_zero_rows_counts_as_fetched`.
  - `test_purge_before`: rows on 11-01 are removed and rows on 11-02 kept, for `departures`, `fetched_days` and `forecast_daily`.
- [ ] **Step 2: Run** `python -m pytest live/tests/test_store.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `live/store.py` and the `engine` fixture.
- [ ] **Step 4: Run** the tests. Expected: PASS.

### Task 3: Forecast from Open-Meteo

**Files:**
- Create: `live/forecast.py`, `live/tests/fixtures/openmeteo_two_airports.json` (captured)
- Test: `live/tests/test_forecast.py`

**Interfaces:**
- Consumes: `live.airports.load()`, `live.store` tables.
- Produces:
  - `request_url(airports: list[dict]) -> str`: `https://api.open-meteo.com/v1/forecast`, comma-joined `latitude`/`longitude`,
    `daily` = `download_data.WEATHER_VARS` (copy the list; don't import across folders),
    `hourly=temperature_2m,weather_code,wind_speed_10m,wind_direction_10m,visibility,precipitation_probability`,
    `forecast_days=3`, `timezone=auto`, Fahrenheit, mph, inches.
  - `parse(payload: list | dict, airports: list[dict]) -> tuple[list[dict], list[dict]]`: returns daily and hourly rows.
    Daily columns are named like `weather_daily` (`temp_max_f`, `precip_in`, …). Visibility converts from metres
    to miles. One-location responses come back as a dict, so wrap them in a list.
  - `refresh(engine) -> int`: fetches in batches of 200 airports, replaces rows per (airport, local_date), returns the airport count.

- [ ] **Step 1:** Capture the fixture with one real request for CLT and DEN (free, no key): `python -c` calling `request_url`'s
  URL with httpx and saving the JSON. Write this as a short note in the test file's docstring.
- [ ] **Step 2: Write the failing tests:**
  - `test_parse_two_airports`: 2 airports × 3 days = 6 daily rows and 2 × 72 = 144 hourly rows; CLT rows carry `airport == "CLT"`;
    `temp_max_f` is a float; `visibility_mi` ≈ metres / 1609.34.
  - `test_parse_single_location_dict`: the CLT element alone (a dict) parses to 3 daily rows.
  - `test_request_url_has_all_daily_vars`: all 9 `WEATHER_VARS` names appear in the URL.
- [ ] **Step 3: Run** `python -m pytest live/tests/test_forecast.py -v`. Expected: FAIL.
- [ ] **Step 4:** Implement `live/forecast.py`.
- [ ] **Step 5: Run** the tests. Expected: PASS.

### Task 4: Schedule cache: fetch only missing days, within quota

**Files:**
- Create: `live/schedules.py`
- Test: `live/tests/test_schedules.py`

**Interfaces:**
- Consumes: `live.store` functions and constants; `live.airports.by_code()`.
- Produces:
  - `class QuotaExhausted(Exception)`, raised by fetchers when the provider refuses (Task 5 raises it on 429).
  - `Fetcher = Callable[[str, datetime, datetime], list[dict]]`: (airport, start_local, end_local) → parsed departure rows.
  - `local_days(airport: str, now_utc: datetime) -> list[date]`: `[today, tomorrow]` in the airport's `tz`.
  - `ensure(engine, airport: str, fetcher: Fetcher, now_utc: datetime, unit_limit: int = API_UNIT_LIMIT) -> str`:
    returns `"ok"` when both days are saved, or `"quota_exhausted"` when a missing day couldn't be fetched because of quota.
    For each missing day, it reserves `2 * UNITS_PER_CALL`, fetches both windows, then calls `record_fetch`.
    If the reservation fails, it returns `"quota_exhausted"` without calling the fetcher. On `QuotaExhausted` it
    calls `mark_exhausted` and returns `"quota_exhausted"`. Any other exception propagates after the units are counted.
  - Per-airport `threading.Lock` held for the whole `ensure` call.

- [ ] **Step 1: Write the failing tests.** Each uses the `engine` fixture, a fake fetcher that records its calls, and a fixed `now_utc`:
  - `test_first_open_fetches_two_days`: 4 fetcher calls, `api_usage` = 8, status `"ok"`.
  - `test_reopen_same_day_is_free`: a second `ensure` makes 0 calls.
  - `test_next_day_fetches_only_new_day`: `now_utc` + 1 day makes 2 calls, both for the new tomorrow.
  - `test_quota_reached_returns_status_without_calling`: with `api_usage` at 377 and limit 380 (a day needs 4 units), 0 calls and `"quota_exhausted"`.
  - `test_preload_limit_allows_up_to_400`: with usage at 377 and `unit_limit=400`, the fetch proceeds.
  - `test_provider_429_marks_month_exhausted`: the fetcher raises `QuotaExhausted`, the status is `"quota_exhausted"`, usage
    becomes 400, and already saved days are still in `departures`.
  - `test_half_fetched_day_is_not_marked_fetched`: the second window raises `RuntimeError`, `fetched()` is False, and usage counts that day's 4 units.
  - `test_today_uses_airport_timezone`: for `now_utc = 2026-11-03T06:30Z`, `local_days("HNL")` is `[2026-11-02, 2026-11-03]`
    and `local_days("CLT")` is `[2026-11-03, 2026-11-04]`.
  - `test_concurrent_first_opens_fetch_once`: two threads call `ensure("ATL")` while the fake fetcher sleeps 0.2 s, and the total calls is 4.
- [ ] **Step 2: Run** `python -m pytest live/tests/test_schedules.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `live/schedules.py`. Use `zoneinfo.ZoneInfo(by_code()[airport]["tz"])`. On Windows, add `tzdata` to the dev install if `ZoneInfo` raises.
- [ ] **Step 4: Run** the tests. Expected: PASS.

### Task 5: AeroDataBox client and parser

**User action first:** sign up for AeroDataBox's **Basic (free)** plan on RapidAPI, copy the key into `live/.env` as
`AERODATABOX_KEY=...`, and run `python -m modal secret create aerodatabox --from-dotenv live/.env` yourself, as was
done for the database secret.

**Files:**
- Create: `live/aerodatabox.py`, `live/capture_sample.py`, `live/tests/fixtures/aerodatabox_clt_departures.json` (captured)
- Test: `live/tests/test_aerodatabox.py`

**Interfaces:**
- Consumes: `live.schedules.QuotaExhausted`, `live.airports.by_code()`.
- Produces:
  - `BASE = "https://aerodatabox.p.rapidapi.com"`, with headers `X-RapidAPI-Key` and `X-RapidAPI-Host: aerodatabox.p.rapidapi.com`.
  - `fetch_window(airport, start_local, end_local, client: httpx.Client) -> dict`: calls `GET /flights/airports/iata/{code}/{start:%Y-%m-%dT%H:%M}/{end:%Y-%m-%dT%H:%M}`
    with `direction=Departure`, `withLeg=true`, `withCancelled=true`, `withCodeshared=false`, `withCargo=false`,
    `withPrivate=false`, `withLocation=false`. Raises `QuotaExhausted` on HTTP 429. Treats 204 (no flights) as `{"departures": []}`.
    Sleeps so calls are at least 1 second apart.
  - `parse(payload: dict, origin: str) -> list[dict]`: one row per departure with the `departures` table columns. It
    drops destinations not in `by_code()` and flights without a scheduled departure time. Missing optional fields
    become `None`.
  - `make_fetcher(key: str) -> Fetcher`: combines the two above for `live.schedules.ensure`.

The response field names below are from memory. **Confirm them against the captured fixture before writing `parse`:**
`departures[].number`, `.status`, `.airline.{iata,name}`, `.aircraft.model`, `.departure.{scheduledTime.{utc,local},terminal,gate}`,
`.arrival.{airport.iata,scheduledTime.{utc,local}}`.

- [ ] **Step 1:** Write `capture_sample.py` (reads the key from `live/.env`, calls `fetch_window` once for CLT, 06:00–11:59
  tomorrow, saves the fixture; about 2 units) and run it. Open the fixture and correct the field list above if it differs.
- [ ] **Step 2: Write the failing tests** (the HTTP layer uses `httpx.MockTransport`):
  - `test_parse_fixture`: there are more than 0 rows; every row has `airport == "CLT"`, a 3-letter `dest`, and a parseable `dep_utc`;
    and a `flight_number` like `"AA 1234"` (airline code, space, digits).
  - `test_parse_drops_unknown_destinations`: inject a departure to `LHR`; it's absent from the output.
  - `test_parse_tolerates_missing_fields`: a departure with no `aircraft`, `gate`, `terminal` or `arrival.scheduledTime` gives `None`s, not a crash.
  - `test_client_raises_quota_error_on_429`.
  - `test_client_treats_204_as_empty`.
  - `test_request_path_and_params`: the mock transport asserts the path `/flights/airports/iata/CLT/2026-11-02T00:00/2026-11-02T11:59` and `withCodeshared=false`.
- [ ] **Step 3: Run** `python -m pytest live/tests/test_aerodatabox.py -v`. Expected: FAIL.
- [ ] **Step 4:** Implement `live/aerodatabox.py`.
- [ ] **Step 5: Run** the tests. Expected: PASS.

### Task 6: API

**Files:**
- Create: `live/api.py`
- Test: `live/tests/test_api.py`

**Interfaces:**
- Consumes: `schedules.ensure`, `schedules.local_days`, `aerodatabox.make_fetcher`, `store.get_engine`, `airports.load`.
- Produces: `create_app(engine, fetcher, board_dir: Path | None, now=lambda: datetime.now(UTC)) -> FastAPI`:
  - `GET /api/airports`: `[{code, city, board, tz}]`, sorted by city.
  - `GET /api/departures?airport=CLT`: uppercases the code and returns 404 for unknown codes. Calls `ensure`, then returns
    `{"airport", "status", "days": [iso, iso], "flights": [...]}`. Each flight carries the `departures` row fields plus:
    - `origin_weather`: the `forecast_daily` row for (origin, dep local date) or null;
    - `dest_weather`: the `forecast_hourly` row for (dest, arrival local hour, falling back to departure hour + 2) or null.

    When status is `"quota_exhausted"`, any saved days are still included.
  - When `board_dir` is set, mounts it as static files at `/` with `html=True`.
  - `Cache-Control: no-store` on `/api/*`.

- [ ] **Step 1: Write the failing tests** using FastAPI `TestClient`, the `engine` fixture and a fake fetcher returning 2 rows per window:
  - `test_departures_first_open`: status `"ok"`, 8 flights (2 days × 2 windows × 2 rows), `days` has 2 entries.
  - `test_lowercase_code_ok_unknown_404`: `?airport=clt` returns 200 and `?airport=XXX` returns 404.
  - `test_quota_exhausted_still_returns_saved_days`.
  - `test_dest_weather_joined`: with a seeded `forecast_hourly` row for the destination at the arrival hour, `dest_weather.temp_f` matches it.
  - `test_airports_list`: 362 entries; the first entry's city sorts first.
- [ ] **Step 2: Run** `python -m pytest live/tests/test_api.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `live/api.py`.
- [ ] **Step 4: Run** `python -m pytest live -v`. Expected: all PASS.

### Task 7: Modal app, nightly job, deploy

**Files:**
- Create: `live/modal_app.py`, `live/nightly.py`
- Modify: `README.md` (new "Live departures" section: what runs, the two secrets, commands, the free-quota behavior)
- Test: `live/tests/test_nightly.py`

**Interfaces:**
- Consumes: `forecast.refresh`, `schedules.ensure`, `store.purge_before`, `store.create_tables`, `api.create_app`.
- Produces:
  - `nightly.run(engine, fetcher, now_utc) -> dict`: runs `create_tables`, then `purge_before(UTC date − 1 day)`,
    `forecast.refresh`, and `ensure("CLT", fetcher, now_utc, unit_limit=PRELOAD_UNIT_LIMIT)`. Returns counts and status for logging.
  - `modal_app.py`: `modal.App("flight-departures")`, image `debian_slim(python_version="3.12")` +
    `uv_pip_install(requirements=[live/requirements.txt])` + `add_local_dir` for `live/` (ignore `.env`, `tests`,
    `__pycache__`, `capture_sample.py`, `export_airports.py`) and for the board (`index.html`, `css/`, `js/`). Secrets are
    `flight-dashboard-db` and `aerodatabox`.
    - `@app.function(..., max_containers=1, scaledown_window=15*60) @modal.concurrent(max_inputs=100) @modal.asgi_app(label="flight-departures-board")`
    - `@app.function(..., schedule=modal.Cron("0 9 * * *"), timeout=600)` calling `nightly.run`.

- [ ] **Step 1: Write the failing test** `test_nightly_preloads_clt_and_purges`: with a fake fetcher and a fake forecast,
  CLT gets 4 calls, a seeded departures row dated two days ago is gone, and the result status is `"ok"`. Monkeypatch
  `forecast.refresh` so no network call is made.
- [ ] **Step 2: Run** it. Expected: FAIL. **Step 3:** Implement `nightly.py`. **Step 4:** Run `python -m pytest live -v`. Expected: all PASS.
- [ ] **Step 5:** Implement `modal_app.py`. Run `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -m modal run live/modal_app.py::nightly_job`
  once. Expected log: about 362 airports refreshed, CLT status `ok`, about 8 units used. Check `live.api_usage` in Supabase.
- [ ] **Step 6:** `python -m modal deploy live/modal_app.py`. Expected: the URL
  `https://jbecke20--flight-departures-board.us-east.modal.run` prints. `curl .../api/departures?airport=CLT` returns status `ok`
  and flights for 2 dates without using new units (already saved).
- [ ] **Step 7:** Write the README section.

### Task 8: Board reads the API

**Files:**
- Modify: `js/data.js` (replace synthetic generation), `js/app.js` (async loading, origin menu, time zones, states), `index.html` (placeholder notice)

**Interfaces:**
- Consumes: `/api/airports`, `/api/departures` (Task 6 shapes).
- Produces in `window.FlightData`:
  - `loadAirports(): Promise<Airport[]>`
  - `loadDepartures(code): Promise<{status, flights}>`. It maps API rows to the flight shape in `README.md`:
    - `departure` and `arrival` become `Date` objects from the `*_utc` fields;
    - `origin` and `dest` become airport objects with `tz`;
    - `destWeather` comes from `dest_weather` via a WMO-code → `{label, icon, pts}` table built from the existing six `WEATHER` entries;
    - `gate` and `aircraft` may be `null`;
    - `prediction` comes from the existing `predict` scoring with real weather, time of day and airline record (`AIRLINE_RECORD` keyed by code, 0 when unknown). Traffic uses that airport's departures that day, ≥ 400 = heavy, ≥ 150 = busy. Drop the "Incoming aircraft" factor.
  - Remove `AIRPORTS`, `generateDay` and the RNG helpers. Keep `TIERS`.

- [ ] **Step 1:** In `app.js`: start with `await loadAirports()`. Fill the origin select with no "All airports" option and set
  it to `CLT`. Load departures on origin change. **Clear** resets the destination and search and leaves the origin on CLT. Show
  `boardTime`/`readableTime` in the origin airport's `tz` via `toLocaleTimeString([], {timeZone})`.
- [ ] **Step 2:** Add these status texts to `#board-status`:
  - Loading: `Loading departures from <city>…`
  - Quota spent with nothing saved: `This month's free flight-data allowance is used up, so <city> can't be loaded. Charlotte is always available.`
  - Quota spent with saved days: append ` Some days couldn't be loaded.`
  - No flights: the existing empty message.
  - A null gate or aircraft shows as `—`.
- [ ] **Step 3:** Add one visible line under the board: `Delay chances are placeholders until our model is trained; flights and weather are real.`
- [ ] **Step 4: Verify in the browser.** Run locally (`uvicorn` via a small `live/dev_server.py` with `board_dir` = repo root,
  using SQLite and the real key) or use the deployed URL. Check each of these:
  - CLT loads with real flight numbers and the times look right for Charlotte;
  - switching to DEN shows Mountain-time times;
  - switching back to CLT uses no new units (`api_usage` unchanged);
  - a flight's detail panel shows destination weather;
  - no console errors.
- [ ] **Step 5:** Redeploy (`python -m modal deploy live/modal_app.py`) and repeat Step 4's checks on the live URL.
