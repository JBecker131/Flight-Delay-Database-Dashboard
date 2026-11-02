# Live departures: real schedules and forecast weather for the board

Date: 2026-10-28. Status: approved in chat.

## Why

The departures board (`index.html`, `js/`) currently shows synthetic flights. For the final project it has to show
real upcoming departures from US airports with real forecast weather, so the delay model can be shown predicting
flights that haven't happened yet. The professor grading the project is the audience. Everything has to run on
free tiers.

## What we settled in conversation

- **Window: today and tomorrow** (2 days, airport local time). The user cut it from 3 days to stretch the free quota.
  The board already shows exactly these two days.
- **Coverage: any US airport with departures**, with **real** schedules. A predicted schedule built from older BTS
  months was rejected: backtested on 2025, a two-month-old weekday template matched only 29% of real flights (same
  number, route, and time within 15 min), and 55% of its flights didn't exist.
- **No scraping and no extra accounts.** FlightAware's terms forbid scraping, airport sites block it (ATL returns 403),
  and multiplying free accounts breaks provider rules. One legitimate free account only.
- **Schedules load when someone opens an airport, and the copy is shared.** Source: AeroDataBox FIDS (airport
  departures), Basic free plan, 400 units/month, max 12 hours per request, Tier 2 (2 units per request).
  - First open of an airport with nothing saved: fetch today + tomorrow = 4 requests (8 units).
  - Next day, today is already saved from yesterday; only the new tomorrow is fetched = 2 requests.
  - Everyone after that, including reopening the same airport, reads the saved copy at no cost.
  - Saved days are not re-fetched; overnight schedule changes are accepted as a trade-off.
  - When the month's quota is spent, the board says so. It never shows made-up flights in place of real ones.
- **Charlotte is loaded ahead of time** by the nightly job (only its newly added day, about 60 requests/month), so the
  home airport always opens instantly.
- **Weather: Open-Meteo forecast API** for all 362 airports nightly. Free for non-commercial use, up to 1,000
  locations per request.
- **Predictions:** the model isn't trained yet. Until it is, real flights and weather get the existing placeholder
  scoring, and the board labels predictions as placeholders.
- The user will sign up for AeroDataBox themselves and add the key the same way as the Supabase password.

## Goals

1. A nightly batch job that refreshes the 2-day weather forecast for every airport, preloads Charlotte's new day, and
   clears days that have passed.
2. An endpoint the board calls per airport that serves saved departures or fetches missing days within the quota.
3. The board reads real flights from that endpoint instead of generating them.
4. The board is hosted publicly so the professor can open it.

## Non-goals

- Training or serving the delay model (later phase; the endpoint's response leaves room for it).
- Arrivals boards, international destinations, live gate or delay status.
- Describing the new tables in the database dashboard (can follow once they exist).

## Anti-goals (failure even if it technically works)

- Fake or predicted flights shown as real.
- The API key visible in the public page or committed to files.
- A quota overrun: an unbounded loop or a burst of visitors that bills or bans the account.
- A blank board on presentation day with no explanation.

## Design (builder's calls, marked as mine)

**Hosting (my call).** One new Modal app, `flight-departures`, separate from the database dashboard:
- a web endpoint (FastAPI via `@modal.asgi_app`) that serves the static board and `GET /api/departures?airport=CLT`;
- a scheduled function, `modal.Cron`, at 09:00 UTC (4–5 AM Eastern), before most first departures;
- `max_containers=1` on the web function so one in-process lock per airport stops two simultaneous first opens from
  fetching twice.

Secrets: the existing `flight-dashboard-db` (Supabase URI) plus a new `aerodatabox` secret holding the API key. The
user creates it, as before.

**Storage (my call).** A new `live` schema in the same Supabase project, kept apart from `dashboard`:
- `forecast_daily (airport, local_date, <same daily variables as weather_daily>, weather_code, precip_prob_max, fetched_at)`
- `departures (airport, local_date, airline, flight_number, dest, sched_dep_local, sched_arr_local, aircraft, terminal, gate, status, fetched_at)`
- `fetched_days (airport, local_date, fetched_at)`. A row means "this day was fetched", so an airport with zero
  departures that day isn't re-fetched.
- `api_usage (month, units_used)`. Every AeroDataBox call adds its units first; the endpoint refuses new fetches at
  380 of 400 units, leaving headroom for the Charlotte preload.

**Fetch details (my calls; verify against AeroDataBox docs during the build).**
- Departures only, via `/flights/airports/iata/{code}/{from}/{to}` with `direction=Departure`, codeshares excluded,
  cargo and private excluded, cancelled included.
- Two 12-hour local windows per day: 00:00–11:59 and 12:00–23:59.
- Keep only flights whose destination is an airport in the `airports` table, which in effect means domestic,
  because the model is trained on BTS domestic flights.
- Requests are rate-limited to one per second.

**"Today" (my call).** Dates are each airport's local date. The endpoint returns the airport's today and tomorrow and
fetches whichever is missing from `fetched_days`. The nightly job deletes rows older than yesterday (local), so
nothing ever needs a timezone-exact purge.

**Forecast (my call).** One Open-Meteo forecast request per ~200 airports (`forecast_days=3`, `timezone=auto`),
asking for the same daily variables `download_data.py` stores in `weather_daily` so model features line up. Also
fetch the hourly variables the board's destination-weather card needs: weather code, wind, visibility and
precipitation probability. Upsert daily values into `forecast_daily` and hourly values into
`forecast_hourly (airport, time_local, temp_f, weather_code, wind_mph, wind_dir_deg, visibility_mi, precip_prob)`.

**Board changes.**
- `js/data.js`: `getFlights(airport)` becomes an async `fetch` of `/api/departures`, mapped to the existing flight
  shape. Gate and aircraft are blank when AeroDataBox lacks them. Placeholder `prediction` computed from real
  forecast weather, time of day, airline and airport, reusing the current scoring.
- `js/app.js`: await data before rendering; load on airport change. States are loading, quota spent, and
  airport has no departures.
- Placeholder predictions carry a visible label.
- The airport list comes from the 362 airports in the database instead of the 16 hard-coded ones.

**Testing.** Unit-test quota accounting, the missing-days logic and AeroDataBox-to-board mapping against a saved
sample response. One live end-to-end check on Charlotte, about 8 units. No test may call AeroDataBox without an
explicit flag.

## Board opens on Charlotte (user decision)

The board opens on Charlotte, and the "All airports" option is removed from the origin menu. An all-airports view
would spend the quota. The destination filter and flight-number search still work within the chosen airport.

## Left to the builder

Table indexes, response caching headers, exact Open-Meteo variable names, error retry policy, and code layout
(suggested: `live/` folder with `modal_app.py`, `aerodatabox.py`, `forecast.py`, `store.py`).
