import altair as alt
import pandas as pd
import streamlit as st

from charts import SERIES, bars, hbars, show
from data import has_live_data, human, live_query

BOARD_URL = "https://jbecke20--flight-departures-board.us-east.modal.run"
UNIT_LIMIT, VISITOR_LIMIT = 400, 380
# The 21 reporting carriers in the BTS data (the `airlines` table), the only ones the model has seen
BTS_CARRIERS = {"9E", "AA", "AS", "B6", "C5", "DL", "F9", "G4", "G7", "HA", "MQ", "NK", "OH", "OO", "PT", "QX",
                "UA", "WN", "YV", "YX", "ZW"}

# WMO weather codes grouped the way the departures board shows them (js/data.js)
CONDITIONS = {
    "Clear skies": [0, 1], "Partly cloudy": [2], "Overcast": [3], "Fog": [45, 48],
    "Drizzle": [51, 53, 55, 56, 57], "Rain": [61, 63, 80, 81], "Heavy rain": [65, 66, 67, 82],
    "Snow": [71, 73, 75, 77, 85, 86], "Thunderstorms": [95, 96, 99],
}
CONDITION_OF = {code: name for name, codes in CONDITIONS.items() for code in codes}

TABLES = [
    ("forecast_daily", "airport × local date",
     "Open-Meteo daily forecast for the next 3 days. Same columns as `weather_daily`, so the model can "
     "score upcoming flights with the features it was trained on."),
    ("forecast_hourly", "airport × local hour",
     "Hourly forecast that fills the board's \"weather when you land\" card."),
    ("departures", "one scheduled departure",
     "Real departures from AeroDataBox for today and tomorrow, saved the first time anyone opens an airport."),
    ("fetched_days", "airport × local date",
     "Which airport-days have been fetched, so nobody pays for the same day twice."),
    ("api_usage", "month",
     "AeroDataBox units spent this month against the free plan's 400."),
]

st.title("Live data")
st.markdown(
    "The historical tables train the model. To use it on flights that haven't left yet, the "
    f"[departures board]({BOARD_URL}) keeps a second, much smaller set of tables in Supabase's `live` "
    "schema. They hold upcoming schedules and forecast weather for **today and tomorrow** only. This page "
    "reads them directly, so it is never more than five minutes behind the board.")

if not has_live_data():
    st.info("The live tables aren't in this database. They exist in Supabase once the board's nightly job "
            "has run. A local copy of the dashboard reads `dashboard.db`, which doesn't have them.")
    st.stop()

# Every query below is filtered or counted in Supabase; only the results come back to the app.
month = pd.Timestamp.now(tz="UTC").strftime("%Y-%m")
totals = live_query(
    "SELECT (SELECT count(*) FROM live.forecast_daily) AS forecast_daily,"
    " (SELECT count(*) FROM live.forecast_hourly) AS forecast_hourly,"
    " (SELECT count(*) FROM live.departures) AS departures,"
    " (SELECT count(*) FROM live.fetched_days) AS fetched_days,"
    " (SELECT count(*) FROM live.api_usage) AS api_usage,"
    " (SELECT count(DISTINCT airport) FROM live.forecast_daily) AS forecast_airports,"
    " (SELECT count(DISTINCT airport) FROM live.fetched_days) AS saved_airports,"
    " (SELECT coalesce(sum(units_used), 0) FROM live.api_usage WHERE month = :month) AS used",
    month=month).iloc[0]
used = int(totals["used"])

c = st.columns(2) + st.columns(2)  # two rows, so "362 of 362" isn't cut off on a laptop screen
c[0].metric("Airports with a forecast", f"{totals['forecast_airports']} of 362", border=True,
            help="Refreshed every night at 09:00 UTC (4–5 AM Eastern).")
c[1].metric("Airports with schedules", int(totals["saved_airports"]), border=True,
            help="Charlotte is loaded every night; the others load the first time a visitor opens them.")
c[2].metric("Saved departures", human(totals["departures"]), border=True)
c[3].metric("Free units used", f"{used} of {UNIT_LIMIT}", border=True,
            help=f"Visitors can spend up to {VISITOR_LIMIT}; the rest is kept for the nightly Charlotte load.")

st.subheader("How it's refreshed")
st.markdown(
    "- **Every night at 09:00 UTC** a Modal job deletes days that have passed, refreshes the Open-Meteo "
    "forecast for all 362 airports (free, one request per 200 airports), and loads Charlotte's newly visible day.\n"
    "- **When a visitor opens an airport**, its departures for any day not saved yet are fetched from "
    "AeroDataBox's free plan, 2 requests (4 units) a day, and shared with every later visitor.\n"
    "- **Only US domestic flights are kept**, to airports that appear in the BTS data, because that's what "
    "the model is trained on. Codeshare duplicates, cargo and private flights are left out.")

st.subheader("Live tables")
st.table(pd.DataFrame([{"Table": f"live.{n}", "One row per": g, "Rows": f"{int(totals[n]):,}", "What it holds": d}
                       for n, g, d in TABLES]).set_index("Table"))

st.subheader("Forecast conditions across airports")
# Airports past midnight UTC (Guam, say) already have a date most others don't; keep dates most airports share.
per_day = live_query("SELECT local_date, count(*) AS airports FROM live.forecast_daily GROUP BY local_date")
days = sorted(per_day.loc[per_day["airports"] >= per_day["airports"].max() / 2, "local_date"])
day = st.segmented_control("Day", days, default=days[0], format_func=lambda d: pd.Timestamp(d).strftime("%a %b %d"),
                           key="forecast_day") or days[0]
on_day = live_query("SELECT * FROM live.forecast_daily WHERE local_date = :day", day=day).assign(
    condition=lambda d: d["weather_code"].map(CONDITION_OF).fillna("No forecast"))
by_condition = on_day.groupby("condition").size().rename("airports").reset_index()
show(hbars(by_condition, "condition", "airports", None, "Airports",
           [alt.Tooltip("condition:N", title="Condition"), alt.Tooltip("airports:Q", title="Airports")],
           x_format="d"), height=260)
st.caption("Each airport's worst weather that day, using the WMO code Open-Meteo reports. The local date "
           "follows each airport's own time zone.")
with st.expander("Table view"):
    st.dataframe(on_day.drop(columns=["fetched_at"]).sort_values("airport"), hide_index=True, width="stretch")

st.subheader("Hourly forecast for one airport")
codes = sorted(live_query("SELECT DISTINCT airport FROM live.forecast_daily")["airport"])
airport = st.selectbox("Airport", codes, index=codes.index("CLT") if "CLT" in codes else 0, key="forecast_airport")
one = live_query("SELECT * FROM live.forecast_hourly WHERE airport = :airport ORDER BY time_local",
                 airport=airport).assign(time=lambda d: pd.to_datetime(d["time_local"]))
hover = alt.selection_point(fields=["time"], nearest=True, on="pointerover", empty=False)
tips = [alt.Tooltip("time:T", title="Local time", format="%a %b %d, %-I %p"),
        alt.Tooltip("temp_f:Q", title="Temperature (°F)", format=".0f"),
        alt.Tooltip("precip_prob:Q", title="Chance of rain (%)", format=".0f"),
        alt.Tooltip("wind_mph:Q", title="Wind (mph)", format=".0f")]
x = alt.X("time:T", title=None, axis=alt.Axis(format="%a %-I %p", grid=False, tickCount=8))
base = alt.Chart(one).encode(x=x)
temp = base.mark_line(color=SERIES[0], strokeWidth=2).encode(y=alt.Y("temp_f:Q", title="Temperature (°F)",
                                                                       scale=alt.Scale(zero=False)))
rule = base.mark_rule(color=SERIES[0], opacity=0.3).encode(
    opacity=alt.condition(hover, alt.value(0.4), alt.value(0)), tooltip=tips).add_params(hover)
points = temp.mark_point(filled=True, size=40, color=SERIES[0]).encode(
    opacity=alt.condition(hover, alt.value(1), alt.value(0)))
show(alt.layer(temp, points, rule), height=220)
rain = bars(one, "time", "precip_prob", None, "Chance of rain (%)", tips, x_type="temporal")
show(rain.encode(x=x, y=alt.Y("precip_prob:Q", title="Chance of rain (%)", scale=alt.Scale(domain=[0, 100]))),
     height=160)
with st.expander("Table view"):
    st.dataframe(one.drop(columns=["time"]), hide_index=True, width="stretch")

st.subheader("Saved schedules")
if not totals["departures"]:
    st.info("No departures saved yet. They appear after the nightly job or a visitor opens an airport.")
    st.stop()

per_airport_day = live_query("SELECT airport, local_date, count(*) AS departures FROM live.departures "
                             "GROUP BY airport, local_date")
saved = (per_airport_day.pivot(index="airport", columns="local_date", values="departures").fillna(0).astype(int)
         .rename_axis(index="Airport", columns=None))
saved["Fetched at (UTC)"] = live_query("SELECT airport, max(fetched_at) AS fetched_at FROM live.fetched_days "
                                       "GROUP BY airport").set_index("airport")["fetched_at"]
st.dataframe(saved, width="stretch")

pick = st.selectbox("Airport", list(saved.index), index=list(saved.index).index("CLT") if "CLT" in saved.index else 0,
                    key="schedule_airport")
mine = live_query("SELECT * FROM live.departures WHERE airport = :airport", airport=pick).assign(
    hour=lambda d: d["dep_local"].str[11:13].astype(int))
left, right = st.columns(2)
with left:
    st.markdown(f"**Departures from {pick} by local hour**, both days")
    per_hour = mine.groupby("hour").size().rename("departures").reindex(range(24), fill_value=0).reset_index()
    show(bars(per_hour, "hour", "departures", "Scheduled hour (local)", "Departures",
              [alt.Tooltip("hour:O", title="Hour"), alt.Tooltip("departures:Q", title="Departures")]), height=240)
with right:
    st.markdown(f"**Airlines at {pick}**")
    # Group by code, since one airline can come back under two names ("American", "American Airlines")
    names = mine.dropna(subset=["airline_name"]).groupby("airline")["airline_name"].agg(lambda s: s.mode().iloc[0])
    counts = mine.groupby("airline").size().sort_values(ascending=False)
    top = counts.head(7)
    airlines = pd.DataFrame({"airline": [f"{code} · {names.get(code, code)}" for code in top.index],
                             "departures": top.values})
    if len(counts) > 7:
        airlines.loc[len(airlines)] = ["Other", counts.iloc[7:].sum()]
    show(hbars(airlines, "airline", "departures", None, "Departures",
               [alt.Tooltip("airline:N", title="Airline"), alt.Tooltip("departures:Q", title="Departures")],
               x_format="d"), height=240)
st.caption("Hub structure is plain in the schedule itself: departures come in banks, and one airline "
           "dominates a hub. Both are features the historical data already captures (`dep_time_block`, `airline`).")
with st.expander("Table view"):
    st.dataframe(mine.drop(columns=["id", "hour"]).sort_values("dep_utc"), hide_index=True, width="stretch")

st.subheader("Known gaps against the training data")
carriers = ", ".join(f"'{code}'" for code in sorted(BTS_CARRIERS))
gaps = live_query(
    "SELECT avg(CASE WHEN airline IN (" + carriers + ") THEN 0.0 ELSE 1.0 END) AS outside,"
    " avg(CASE WHEN airline IN ('AA', 'DL', 'UA') AND split_part(flight_number, ' ', 2) ~ '^[0-9]+$'"
    " AND split_part(flight_number, ' ', 2)::int >= 3000 THEN 1.0 ELSE 0.0 END) AS regional"
    " FROM live.departures").iloc[0]
outside, regional = gaps["outside"], gaps["regional"]
st.markdown(
    f"- **{outside:.1%} of saved departures are by operators outside the 21 BTS reporting carriers** "
    "(charter and private-jet companies such as NetJets). The model has never seen them.\n"
    f"- **Regional flights are listed under the brand, not the operator.** AeroDataBox lists a PSA Airlines "
    "flight as \"AA 5069\"; BTS records it under PSA's code `OH`. "
    f"{regional:.0%} of saved departures look like regional flights (American, Delta or United numbers "
    "3000 and up). Their `airline` feature will need mapping to the operating carrier before the model scores them.\n"
    "- **Gates aren't published** this far ahead, so `gate` is empty for nearly every row.")

st.subheader("Free-plan quota")
st.progress(min(used / UNIT_LIMIT, 1.0), text=f"{used} of {UNIT_LIMIT} AeroDataBox units used in {month}")
st.markdown(
    f"A first open of an airport costs 8 units (two days × two 12-hour requests × 2 units), and an airport "
    f"opened yesterday costs 4. Visitors stop at {VISITOR_LIMIT}, which keeps {UNIT_LIMIT - VISITOR_LIMIT} for "
    "the nightly Charlotte load. When the month runs out, the board says so rather than showing anything made up.")
