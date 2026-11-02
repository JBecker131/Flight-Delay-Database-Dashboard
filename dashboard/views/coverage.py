import altair as alt
import pandas as pd
import pydeck as pdk
import streamlit as st

from charts import MISSING, SERIES, hbars, show
from data import meta, table

m = meta()
coverage = table("coverage_monthly")
airports = table("airport_coverage")
columns = table("columns")

st.title("Coverage & quality")
st.markdown("How much of each flight's context actually made it into `flights_combined`, and the "
            "gaps a model will have to live with.")

totals = coverage.groupby("context", sort=False)[["flights", "covered"]].sum()
totals["pct"] = totals["covered"] / totals["flights"]
flights_total = totals["flights"].iloc[0]
hourly = airports[airports["in_hourly_stats"]]
deps = airports["departures"].sum()

c = st.columns(3)
c[0].metric("Airports with weather", f"{m['weather_airports_downloaded']} of {m['weather_airports_needed']}",
            help="Open-Meteo's free tier allows about 125 airports a day; the busiest were fetched first.",
            border=True)
c[1].metric("Flights with origin weather", f"{totals.loc['Weather at origin', 'pct']:.1%}", border=True)
c[2].metric("Departures from the 30 airports with hourly stats",
            f"{hourly['departures'].sum() / deps:.1%}", border=True)

st.subheader("Share of flights that received each kind of context")
overall = totals.reset_index()
show(hbars(overall, "context", "pct", None, "Share of all flights",
           [alt.Tooltip("context:N", title="Context"), alt.Tooltip("pct:Q", title="Flights covered", format=".1%"),
            alt.Tooltip("covered:Q", title="Flights", format=",")], x_format="%", domain=[0, 1]),
     height=260)
st.caption("The target `dep_del15` is missing only for flights cancelled before they left the gate.")

st.subheader("Coverage month by month")
picked = ["Weather at origin", "Delay-cause rates, origin", "Hourly on-time %, origin", "Carrier baggage rate"]
monthly = coverage[coverage["context"].isin(picked)].assign(
    month_start=lambda d: pd.to_datetime(d[["year", "month"]].assign(day=1)),
    pct=lambda d: d["covered"] / d["flights"])
lines = alt.Chart(monthly).mark_line(strokeWidth=2).encode(
    x=alt.X("month_start:T", title=None, axis=alt.Axis(format="%b %Y", grid=False)),
    y=alt.Y("pct:Q", title="Share of the month's flights", scale=alt.Scale(domain=[0, 1]),
            axis=alt.Axis(format="%", tickCount=5)),
    color=alt.Color("context:N", scale=alt.Scale(domain=picked, range=SERIES[:len(picked)]),
                    legend=alt.Legend(columns=4)),
)
hover = alt.selection_point(fields=["month_start"], nearest=True, on="pointerover", empty=False)
rule = alt.Chart(monthly).mark_rule(color=MISSING).encode(
    x="month_start:T",
    opacity=alt.condition(hover, alt.value(1), alt.value(0)),
    tooltip=[alt.Tooltip("yearmonth(month_start):T", title="Month", format="%B %Y")]
            + [alt.Tooltip(f"{name}:Q", format=".1%") for name in picked],
).transform_pivot("context", value="pct", groupby=["month_start"]).add_params(hover)
points = lines.mark_point(filled=True, size=40).encode(opacity=alt.condition(hover, alt.value(1), alt.value(0)))
show(alt.layer(lines, points, rule), height=300)
st.markdown(
    "- **Hourly on-time %** and **baggage rate** are blank for January 2023. The first consumer "
    "report covers January 2023, so there is no December 2022 to look back on. After that, "
    "hourly coverage tracks the share of flights touching the 30 largest airports.\n"
    "- **Delay-cause rates** go back to December 2022, so January 2023 is covered.")
with st.expander("Table view"):
    wide = (coverage.assign(pct=coverage["covered"] / coverage["flights"])
            .pivot_table(index=["year", "month"], columns="context", values="pct", sort=False).reset_index())
    st.dataframe(wide, hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(c, format="percent")
                                for c in wide.columns if c not in ("year", "month")})

st.subheader("Airports")
legend = {"Weather + hourly stats": SERIES[0], "Weather only": SERIES[2], "No weather yet": MISSING}
airports = airports.dropna(subset=["latitude", "longitude"]).assign(
    status=lambda d: d["has_weather"].map({True: "Weather only", False: "No weather yet"})
    .where(~(d["has_weather"] & d["in_hourly_stats"]), "Weather + hourly stats"))
airports["color"] = airports["status"].map(lambda s: [int(legend[s][i:i + 2], 16) for i in (1, 3, 5)] + [210])
airports["radius"] = (airports["departures"] ** 0.5) * 45 + 9000
airports["deps_text"] = airports["departures"].map("{:,.0f}".format)
st.pydeck_chart(pdk.Deck(
    layers=[pdk.Layer("ScatterplotLayer", airports, get_position=["longitude", "latitude"],
                      get_fill_color="color", get_radius="radius", pickable=True,
                      stroked=True, get_line_color=[252, 252, 251], line_width_min_pixels=1)],
    initial_view_state=pdk.ViewState(latitude=38, longitude=-96, zoom=3.1),
    map_style="light",
    tooltip={"html": "<b>{code}</b> · {city}<br/>{deps_text} departures<br/>{status}"},
), height=460)
st.markdown(" &nbsp; ".join(f'<span style="color:{c}">●</span> {s} ({(airports["status"] == s).sum()})'
                            for s, c in legend.items()) + " &nbsp;·&nbsp; size = departures",
            unsafe_allow_html=True)
with st.expander("Table view"):
    st.dataframe(airports[["code", "city", "departures", "arrivals", "status"]], hide_index=True,
                 width="stretch", column_config={"departures": st.column_config.NumberColumn(format="localized"),
                                                 "arrivals": st.column_config.NumberColumn(format="localized")})

st.subheader("Known gaps")
missing_weather = airports[~airports["has_weather"]]
cancelled = columns.query("table_name == 'flights' and column_name == 'dep_del15'").iloc[0]
cancelled_total = int(coverage.drop_duplicates(["year", "month"])["cancelled"].sum())
st.markdown(f"""
- **Weather** has been downloaded for {m['weather_airports_downloaded']} of {m['weather_airports_needed']} airports.
  The missing {len(missing_weather)} are small airports with {missing_weather['departures'].sum() / deps:.1%} of
  departures. Re-running the download over the next few days fills them in.
- **Hourly on-time %** exists only for the 30 airports DOT reports on, by design of the source.
- **Endeavor Air (9E)** stops appearing after 2024, in both the BTS flight data and the consumer reports, so
  the set of airlines differs between the training years and 2025.
- **Consumer complaint counts**, part of the D1 plan, are not included: DOT has delayed publishing them.
- **Cancelled flights**: `dep_del15` is empty for {cancelled['n_nulls']:,} flights ({cancelled['null_pct']:.1f}%), all
  cancelled before leaving the gate. Another {cancelled_total - cancelled['n_nulls']:,} were cancelled after pushing back
  and keep their delay. Whether to drop cancellations or count them as delays is a modeling choice.
- **Weather is daily**, so a storm at 8 pm counts the same for a 6 am departure. Hourly weather would be a later upgrade.
""")
