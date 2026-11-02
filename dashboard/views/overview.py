import altair as alt
import pandas as pd
import streamlit as st

import catalog
from charts import SERIES, show
from data import human, human_bytes, meta, table

m = meta()
cat = table("catalog")
flights = cat.set_index("table_name").loc["flights"]
coverage = table("coverage_monthly")

st.title("Flight Delay Database")
st.markdown("> **Research question:** What is the likelihood that your flight will be delayed, "
            "based on your airline and destination?")
st.markdown(
    "To answer it we built one SQLite database that puts every U.S. domestic flight from 2023 "
    "through 2025 next to the weather at both airports and the recent track record of the airline "
    "at those airports. This dashboard describes that database: where each piece came from, how "
    "the tables fit together, how complete they are, and how the design keeps a model from "
    "seeing the future. What the data says about delays comes in a later part of the project.")

n_tables = (cat["kind"] == "table").sum()
n_views = (cat["kind"] == "view").sum()
months = coverage[["year", "month"]].drop_duplicates()
cols = st.columns(5)
cols[0].metric("Flights", human(flights["n_rows"]), border=True)
cols[1].metric("Tables + views", f"{n_tables} + {n_views}", border=True)
cols[2].metric("Sources", len(catalog.SOURCES), border=True)
cols[3].metric("Months", len(months), help=f"{m['first_flight_date']} to {m['last_flight_date']}", border=True)
cols[4].metric("Database size", human_bytes(m["flights_db_bytes"]), border=True)

st.subheader("Four sources")
fed_by = cat.groupby("source").agg(tables=("table_name", list), rows=("n_rows", "sum"))
grid = st.columns(2)
for i, (name, src) in enumerate(catalog.SOURCES.items()):
    with grid[i % 2].container(border=True):
        st.markdown(f"**{name}**  \n:gray[{src['publisher']}]")
        st.markdown(src["summary"])
        tables = fed_by.loc[name, "tables"]
        st.markdown(f"Feeds {', '.join(f'`{t}`' for t in tables)} · "
                    f"{fed_by.loc[name, 'rows']:,.0f} rows · [source]({src['url']})")

st.subheader("Flights loaded per month")
st.caption("One BTS file per month, January 2023 to December 2025, with no gaps.")
per_month = (coverage.drop_duplicates(["year", "month"])
             .assign(month_start=lambda d: pd.to_datetime(d[["year", "month"]].assign(day=1)))
             [["month_start", "flights", "cancelled"]])
chart = alt.Chart(per_month).mark_bar(color=SERIES[0], cornerRadiusTopLeft=2, cornerRadiusTopRight=2,
                                      width=alt.RelativeBandSize(0.7)).encode(
    x=alt.X("yearmonth(month_start):T", title=None, axis=alt.Axis(format="%b %Y", labelAngle=0, grid=False)),
    y=alt.Y("flights:Q", title="Flights", axis=alt.Axis(format="~s", tickCount=5)),
    tooltip=[alt.Tooltip("yearmonth(month_start):T", title="Month", format="%B %Y"),
             alt.Tooltip("flights:Q", title="Flights", format=","),
             alt.Tooltip("cancelled:Q", title="Cancelled", format=",")],
)
show(chart)
with st.expander("Table view"):
    st.dataframe(per_month.assign(month=per_month["month_start"].dt.strftime("%Y-%m"))
                 [["month", "flights", "cancelled"]], hide_index=True, width="stretch")

st.info("Use the sidebar to go deeper: **Sources & pipeline** for how the data was collected, "
        "**Schema & joins** for how the tables connect, **Table explorer** for every column, "
        "**Coverage & quality** for what's missing, **Leakage safeguards** for how the design "
        "keeps the model honest, and **Live data** for the upcoming flights and forecasts the model will "
        "score.", icon=":material/arrow_back:")
