import pandas as pd
import streamlit as st

import catalog
from charts import MISSING, SERIES
from data import table

cat = table("catalog").set_index("table_name")
columns = table("columns")

st.title("Schema & joins")
st.markdown(
    "The raw tables keep each source at its own grain. `flights_combined` is a view, not a copy: it "
    "starts from `flights` and attaches context with left joins, so a flight is never dropped "
    "because some context is missing. Monthly tables are joined through their `_prev_month` "
    "versions, which are shifted forward one month (see **Leakage safeguards**).")

SOURCE_COLORS = {catalog.BTS_ONTIME: SERIES[0], catalog.BTS_CAUSES: SERIES[1],
                 catalog.OPEN_METEO: SERIES[2], catalog.DOT_ATCR: SERIES[3], catalog.DERIVED: MISSING}
DARK_TEXT = {SERIES[3]}  # yellow needs dark ink


def node(name):
    info = cat.loc[name]
    color = SOURCE_COLORS[info["source"]]
    ink = "#0b0b0b" if color in DARK_TEXT else "#ffffff"
    keys = "".join(f'<tr><td align="left"><font point-size="10" color="#52514e">{k.strip()}</font></td></tr>'
                   for k in info["keys"].split(",") if info["keys"] != "same as flights")
    kind = "view" if info["kind"] == "view" else f"{info['n_rows']:,} rows"
    return (f'{name} [label=<<table border="1" cellborder="0" cellspacing="0" cellpadding="4" '
            f'bgcolor="#ffffff" color="#c3c2b7">'
            f'<tr><td bgcolor="{color}"><font color="{ink}"><b>{name}</b></font></td></tr>'
            f'<tr><td align="left"><font point-size="10" color="#898781">{info["grain"]} · {kind}</font></td></tr>'
            f'{keys}</table>>];')


JOIN = '[color="#2a78d6", fontcolor="#2a78d6"'
DERIVE = '[color="#898781", fontcolor="#898781", style=solid'
dot = "\n".join([
    'digraph { rankdir=LR; bgcolor="transparent"; nodesep=0.3; ranksep=0.9;',
    'node [shape=plain, fontname="Segoe UI", fontsize=11]; edge [fontname="Segoe UI", fontsize=9, arrowsize=0.6];',
    *(node(t) for t in cat.index),
    f'flights -> flights_combined {JOIN}, label="every flight", penwidth=2];',
    f'airlines -> flights_combined {JOIN}, label="carrier = airline"];',
    f'weather_daily -> flights_combined {JOIN}, label="airport = origin / dest\\ndate = flight_date"];',
    f'delay_cause_prev_month -> flights_combined {JOIN}, label="carrier = airline\\nairport = origin / dest\\nyear, month"];',
    f'atcr_ontime_by_hour_prev_month -> flights_combined {JOIN}, label="airport = origin / dest\\nyear, month\\nscheduled hour"];',
    f'atcr_carrier_quality_prev_month -> flights_combined {JOIN}, label="carrier = airline\\nyear, month"];',
    f'delay_cause_monthly -> delay_cause_prev_month {DERIVE}, label="rates, +1 month"];',
    f'atcr_airport_ontime_by_hour -> atcr_ontime_by_hour_prev_month {DERIVE}, label="+1 month"];',
    f'atcr_carrier_quality -> atcr_carrier_quality_prev_month {DERIVE}, label="+1 month"];',
    f'delay_cause_monthly -> airlines {DERIVE}, label="codes, names"];',
    f'flights -> airports {DERIVE}, label="codes, cities"];',
    f'weather_daily -> airports {DERIVE}, label="coords, time zone"];',
    "}",
])
st.graphviz_chart(dot, width="stretch")

legend = " &nbsp; ".join(f'<span style="color:{c}">■</span> {s}' for s, c in SOURCE_COLORS.items())
st.markdown(f"{legend} &nbsp;·&nbsp; <span style='color:{SERIES[0]}'>→</span> join into the view "
            f"&nbsp; <span style='color:{MISSING}'>→</span> derived from", unsafe_allow_html=True)
st.caption("`atcr_denied_boarding` stands alone: it is quarterly and was kept for reference, not joined.")

st.subheader("What flights_combined adds to each flight")
view_cols = columns[columns["table_name"] == "flights_combined"]
flight_cols = set(columns.loc[columns["table_name"] == "flights", "column_name"])
added = view_cols[~view_cols["column_name"].isin(flight_cols)]


def group(name):
    if name == "airline_name":
        return "airlines"
    if "_hour_" in name:
        return "atcr_ontime_by_hour_prev_month"
    if name.startswith("airline_prev_bags"):
        return "atcr_carrier_quality_prev_month"
    if "_prev_" in name:
        return "delay_cause_prev_month"
    return "weather_daily"


JOINS = {
    "airlines": ("airline = carrier", "Carrier name"),
    "weather_daily": ("origin / dest airport and flight_date (joined twice)",
                      "Weather code, high/low temperature, precipitation, rain, snow, precipitation "
                      "hours, wind, gusts at each end"),
    "delay_cause_prev_month": ("airline, origin / dest airport, year, month (joined twice)",
                               "Last month's flights, delay rate, cancel rate, average delay, and "
                               "share delayed by each cause, for this carrier at each end"),
    "atcr_ontime_by_hour_prev_month": ("origin airport + scheduled departure hour; dest airport + "
                                       "scheduled arrival hour; year, month",
                                       "Last month's on-time % for that hour at each end"),
    "atcr_carrier_quality_prev_month": ("airline, year, month", "Last month's mishandled bags per 100"),
}
counts = added["column_name"].map(group).value_counts()
recipe = pd.DataFrame([(t, on, adds, counts.get(t, 0)) for t, (on, adds) in JOINS.items()],
                      columns=["Joined table", "Matched on", "Adds", "Columns added"])
st.table(recipe.set_index("Joined table"))
st.markdown(f"`flights` contributes **{len(flight_cols)}** columns and the joins add "
            f"**{len(added)}**, for **{len(view_cols)}** in all.")
