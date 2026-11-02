"""Cached reads of the summary tables that publish.py writes."""
from io import StringIO

import pandas as pd
import streamlit as st
from sqlalchemy import inspect, text

from store import get_engine, schema_for

ROLE_LABELS = {
    "schedule": "Known before departure",
    "context": "Joined context",
    "target": "Target",
    "outcome": "Outcome: exclude",
}


@st.cache_resource
def engine():
    return get_engine()


@st.cache_data(ttl=600, show_spinner=False)
def table(name):
    e = engine()
    return pd.read_sql_table(name, e, schema=schema_for(e))


def has_live_data():
    """Whether this database has the live board's tables (Supabase's `live` schema, see live/store.py)."""
    e = engine()
    return e.dialect.name == "postgresql" and inspect(e).has_table("departures", schema="live")


@st.cache_data(ttl=300, show_spinner=False)
def live_query(sql, **params):
    """Run a query against the live board's tables in Supabase and return only its result.

    Filtering and counting happen in the database, so the app never pulls whole tables. Results are
    cached for five minutes, so the page is never further behind the board than that.
    """
    with engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def meta():
    m = table("meta")
    return dict(zip(m["key"], m["value"]))


@st.cache_data(ttl=600, show_spinner=False)
def sample(name):
    samples = table("samples")
    data = samples.loc[samples["table_name"] == name, "data_json"].iloc[0]
    return pd.read_json(StringIO(data), orient="split", dtype=False, convert_dates=False)


def catalog_row(name):
    cat = table("catalog")
    return cat[cat["table_name"] == name].iloc[0]


def human(n):
    n = float(n)
    for size, suffix in [(1e9, "B"), (1e6, "M"), (1e3, "K")]:
        if n >= size:
            return f"{n / size:.1f}{suffix}"
    return f"{n:,.0f}"


def human_bytes(n):
    n = float(n)
    for size, suffix in [(1 << 30, "GB"), (1 << 20, "MB"), (1 << 10, "KB")]:
        if n >= size:
            return f"{n / size:.1f} {suffix}"
    return f"{n:,.0f} bytes"
