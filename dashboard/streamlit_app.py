"""Flight Delay Database: a guided tour of the project's data.

    streamlit run streamlit_app.py
"""
import streamlit as st

from data import meta

st.set_page_config(page_title="Flight Delay Database", page_icon=":material/flight_takeoff:", layout="wide")

pages = st.navigation([
    st.Page("views/overview.py", title="Overview", icon=":material/home:", default=True),
    st.Page("views/sources.py", title="Sources & pipeline", icon=":material/download:"),
    st.Page("views/schema.py", title="Schema & joins", icon=":material/account_tree:"),
    st.Page("views/tables.py", title="Table explorer", icon=":material/table_view:"),
    st.Page("views/coverage.py", title="Coverage & quality", icon=":material/fact_check:"),
    st.Page("views/leakage.py", title="Leakage safeguards", icon=":material/shield:"),
    st.Page("views/live.py", title="Live data", icon=":material/sensors:"),
])

with st.sidebar:
    st.caption(f"Data snapshot from {meta()['published_at']}  \n"
               "Group 1 · Diane Han, Jeremy Becker, Marcus Pohlabel, Nash Bachman, Soham Chansoriya")

pages.run()
