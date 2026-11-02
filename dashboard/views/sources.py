import pandas as pd
import streamlit as st

import catalog
from data import has_live_data, human_bytes, live_query, meta, table

m = meta()
cat = table("catalog").set_index("table_name")

st.title("Sources & pipeline")
st.markdown("Three Python scripts turn four public datasets into one SQLite file. Every step can "
            "be re-run, and downloads that already exist are skipped.")

st.graphviz_chart("""
digraph {
  rankdir=TB; bgcolor="transparent"; nodesep=0.25; ranksep=0.3;
  node [shape=box, style="rounded,filled", fillcolor="#ffffff", color="#c3c2b7",
        fontname="Segoe UI", fontsize=11, fontcolor="#0b0b0b", margin="0.15,0.08"];
  edge [color="#898781", arrowsize=0.6];
  bts  [label="BTS On-Time\\n36 monthly zips"];
  dc   [label="BTS Delay Causes\\n1 CSV"];
  om   [label="Open-Meteo\\n1 JSON per airport"];
  atcr [label="DOT consumer reports\\n36 PDFs (browser)"];
  dl   [label="download_data.py", fillcolor="#e6f0fb", color="#2a78d6"];
  ex   [label="extract_consumer_reports.py\\npdfplumber → 3 CSVs", fillcolor="#e6f0fb", color="#2a78d6"];
  build [label="build_database.py", fillcolor="#e6f0fb", color="#2a78d6"];
  db   [label="flights.db\\n11 tables + flights_combined", shape=cylinder];
  pub  [label="publish.py\\nprofiles + samples", fillcolor="#e6f0fb", color="#2a78d6"];
  sb   [label="Supabase\\n(this dashboard's data)", shape=cylinder];
  app  [label="Streamlit on Modal"];
  bts -> dl; dc -> dl; om -> dl; atcr -> ex;
  dl -> build; ex -> build; build -> db -> pub -> sb -> app;
}
""")

st.code("""cd data_pipeline
python download_data.py            # flights, delay causes, weather (resumable)
python extract_consumer_reports.py # needs the ATCR PDFs saved by hand first
python build_database.py           # ~8 minutes -> ~/flight_data/flights.db""", language="bash")

st.subheader("Each source")
facts = {
    catalog.BTS_ONTIME: f"{m['ontime_zip_files']} zip files, {human_bytes(m['ontime_zip_bytes'])} compressed",
    catalog.BTS_CAUSES: f"{cat.loc['delay_cause_monthly', 'n_rows']:,} rows",
    catalog.OPEN_METEO: f"{m['weather_airports_downloaded']} of {m['weather_airports_needed']} airports downloaded",
    catalog.DOT_ATCR: f"{m['atcr_reports']} monthly reports",
}
for name, src in catalog.SOURCES.items():
    with st.container(border=True):
        left, right = st.columns([3, 1])
        left.markdown(f"#### {name}\n:gray[{src['publisher']}] · [{src['url']}]({src['url']})")
        right.markdown(f"**{facts[name]}**")
        st.markdown(f"**What it holds.** {src['summary']}")
        st.markdown(f"**How we got it.** {src['how']}")

st.subheader("Proposal vs. what we built")
st.markdown("The D1 proposal estimated each dataset's size. Here is what ended up in the database.")
quality = cat.loc[["atcr_airport_ontime_by_hour", "atcr_carrier_quality", "atcr_denied_boarding"]]
compare = pd.DataFrame([
    (catalog.BTS_ONTIME, "17 columns × 21 million rows (departures)",
     f"{cat.loc['flights', 'n_columns']} columns × {cat.loc['flights', 'n_rows']:,} rows",
     "Kept arrival, cancellation and cause columns too, so arrival delay and cause are available later"),
    (catalog.OPEN_METEO, "11 columns × 32,880 rows (daily)",
     f"{cat.loc['weather_daily', 'n_columns']} columns × {cat.loc['weather_daily', 'n_rows']:,} rows",
     "One row per airport per day, for every airport in the flights rather than a fixed list"),
    (catalog.BTS_CAUSES, "21 columns × 65,000 rows",
     f"{cat.loc['delay_cause_monthly', 'n_columns']} columns × {cat.loc['delay_cause_monthly', 'n_rows']:,} rows",
     "Includes December 2022 so January 2023 has a previous month"),
    (catalog.DOT_ATCR, "24 columns × 360 rows (airports × months)",
     " + ".join(f"{r.n_rows:,}" for r in quality.itertuples()) + " rows in 3 tables",
     "Split by grain: airport × hour, carrier × month, carrier × quarter"),
], columns=["Source", "Proposal (D1)", "Database", "Why it differs"])
st.table(compare.set_index("Source"))

st.subheader("Live sources: upcoming flights")
st.markdown("The four sources above are history: they train the model. To predict flights that haven't "
            "left yet, the [departures board](https://jbecke20--flight-departures-board.us-east.modal.run) "
            "adds two live sources. Neither was in the D1 proposal. They feed a separate `live` schema in "
            "Supabase that holds only today and tomorrow, and flights.db is never touched.")

st.graphviz_chart("""
digraph {
  rankdir=TB; bgcolor="transparent"; nodesep=0.25; ranksep=0.3;
  node [shape=box, style="rounded,filled", fillcolor="#ffffff", color="#c3c2b7",
        fontname="Segoe UI", fontsize=11, fontcolor="#0b0b0b", margin="0.15,0.08"];
  edge [color="#898781", arrowsize=0.6, fontname="Segoe UI", fontsize=9, fontcolor="#52514e"];
  fc   [label="Open-Meteo forecast\\n362 airports, 3 days"];
  adb  [label="AeroDataBox\\nairport departures, 12 h a request"];
  cron [label="nightly job (Modal cron)\\n09:00 UTC", fillcolor="#e6f0fb", color="#2a78d6"];
  api  [label="board API (Modal)\\nfirst open of an airport", fillcolor="#e6f0fb", color="#2a78d6"];
  live [label="Supabase live schema\\nforecast, departures, quota", shape=cylinder];
  board [label="Departures board"];
  page [label="This dashboard\\nLive data page"];
  fc -> cron [label="every night"];
  adb -> cron [label="Charlotte"];
  adb -> api [label="any other airport"];
  cron -> live; api -> live; live -> board; live -> page;
}
""")

live_facts = {}
if has_live_data():
    n = live_query(
        "SELECT (SELECT count(DISTINCT airport) FROM live.forecast_daily) AS forecast_airports,"
        " (SELECT max(fetched_at) FROM live.forecast_daily) AS forecast_at,"
        " (SELECT count(*) FROM live.departures) AS departures,"
        " (SELECT count(DISTINCT airport) FROM live.fetched_days) AS saved_airports").iloc[0]
    live_facts = {
        catalog.OPEN_METEO_FORECAST: f"{n['forecast_airports']} airports, refreshed {str(n['forecast_at'])[:16].replace('T', ' ')} UTC",
        catalog.AERODATABOX: f"{n['departures']:,} departures saved for {n['saved_airports']} airports",
    }
for name, src in catalog.LIVE_SOURCES.items():
    with st.container(border=True):
        left, right = st.columns([3, 1])
        left.markdown(f"#### {name}\n:gray[{src['publisher']}] · [{src['url']}]({src['url']})")
        right.markdown(f"**{live_facts.get(name, 'Not in this database')}**")
        st.markdown(f"**What it holds.** {src['summary']}")
        st.markdown(f"**How we get it.** {src['how']}")
st.caption("The **Live data** page shows what these sources currently hold, and the gaps between them and the "
           "training data.")
