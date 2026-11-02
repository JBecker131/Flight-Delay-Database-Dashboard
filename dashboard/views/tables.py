import altair as alt
import streamlit as st

import catalog
from charts import bars, show
from data import ROLE_LABELS, catalog_row, sample, table

cat = table("catalog").sort_values("display_order")
columns = table("columns")
histograms = table("histograms")

st.title("Table explorer")

name = st.selectbox(
    "Table", cat["table_name"],
    format_func=lambda t: f"{t}  ({'view' if catalog_row(t)['kind'] == 'view' else catalog_row(t)['source']})")
info = catalog_row(name)
cols = columns[columns["table_name"] == name].sort_values("ordinal")
has_roles = cols["role"].notna().any()

source = catalog.SOURCES.get(info["source"])
source_link = f"[{info['source']}]({source['url']})" if source else info["source"]
st.markdown(f"{info['description']}  \n"
            f"**Source:** {source_link} · "
            f"**Grain:** {info['grain']} · **Key:** `{info['keys']}`")
m = st.columns(3)
m[0].metric("Rows", f"{info['n_rows']:,}", border=True)
m[1].metric("Columns", info["n_columns"], border=True)
m[2].metric("Cells missing", f"{(cols['n_nulls'].sum() / (info['n_rows'] * len(cols))):.1%}", border=True,
            help="Share of all cells that are empty. The Missing column below shows which columns drive it.")

dictionary, distributions, rows = st.tabs(["Data dictionary", "Column distributions", "Sample rows"])

with dictionary:
    shown = cols.assign(role=cols["role"].map(ROLE_LABELS))[
        ["column_name", "sql_type"] + (["role"] if has_roles else []) +
        ["null_pct", "n_distinct", "description", "min_value", "median", "max_value", "top_values"]]
    st.dataframe(shown, hide_index=True, width="stretch", height=min(38 + 35 * len(shown), 720),
                 column_config={
                     "column_name": st.column_config.TextColumn("Column", pinned=True),
                     "sql_type": "Type",
                     "role": st.column_config.TextColumn("Role", help="Whether a model may use it"),
                     "description": st.column_config.TextColumn("Meaning", width="large"),
                     "null_pct": st.column_config.ProgressColumn("Missing", format="%.1f%%", min_value=0, max_value=100),
                     "n_distinct": st.column_config.NumberColumn("Distinct", format="localized"),
                     "min_value": "Min",
                     "median": st.column_config.NumberColumn("Median", format="%.4g"),
                     "max_value": "Max",
                     "top_values": st.column_config.TextColumn("Most common (share of rows)", width="large"),
                 })
    if has_roles:
        st.caption("Roles: **Known before departure** can be used as a feature; **Joined context** "
                   "comes from other tables, already shifted to last month; **Target** is what the "
                   "model predicts; **Outcome** describes what happened after departure and must be "
                   "left out of a model.")

with distributions:
    col = st.selectbox("Column", cols["column_name"], key=f"col-{name}")
    p = cols[cols["column_name"] == col].iloc[0]
    st.markdown(f"**{col}**: {p['description']}")
    median = "–" if p["median"] is None or p["median"] != p["median"] else f"{p['median']:.4g}"
    st.markdown(f"Missing **{p['null_pct']:.1f}%** · Distinct values **{p['n_distinct']:,}** · "
                f"Min **{p['min_value'] or '–'}** · Median **{median}** · Max **{p['max_value'] or '–'}**")

    h = histograms[(histograms["table_name"] == name) & (histograms["column_name"] == col)].sort_values("bucket")
    if h.empty:
        st.caption("No non-missing values.")
    else:
        h = h.assign(share=h["count"] / info["n_rows"])
        tooltip = [alt.Tooltip("label:N", title=col), alt.Tooltip("count:Q", title="Rows", format=","),
                   alt.Tooltip("share:Q", title="Share of all rows", format=".1%")]
        show(bars(h, "label", "count", col, "Rows", tooltip, sort=list(h["label"]),
                  label_angle=-45 if len(h) > 12 else 0), height=300)
        if h["lo"].notna().any() and len(h) == 30:
            st.caption("Binned into 30 equal ranges between the 0.5th and 99.5th percentiles; the "
                       "rarest extremes are folded into the first and last bars.")
        elif col.endswith("_time") and len(h) == 24:
            st.caption("Grouped by hour of the day (local time).")
        elif h["label"].str.startswith("Other").any():
            st.caption("The 15 most common values; everything else is grouped as Other.")
        with st.expander("Table view"):
            st.dataframe(h[["label", "count", "share"]], hide_index=True, width="stretch",
                         column_config={"label": col, "count": st.column_config.NumberColumn("Rows", format="localized"),
                                        "share": st.column_config.NumberColumn("Share", format="percent")})

with rows:
    df = sample(name)
    total = info["n_rows"]
    st.caption(f"{len(df):,} randomly chosen rows" + (f" out of {total:,}" if len(df) < total else " (the whole table)"))
    st.dataframe(df, hide_index=True, width="stretch", height=480)
