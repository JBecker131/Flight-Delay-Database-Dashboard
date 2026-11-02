import streamlit as st

from data import ROLE_LABELS, sample, table

columns = table("columns")
view = columns[columns["table_name"] == "flights_combined"].sort_values("ordinal")

st.title("Leakage safeguards")
st.markdown(
    "Leakage is when a model trains on information it wouldn't have at prediction time. It looks "
    "accurate on paper and fails on real flights. The database guards against three kinds.")

st.subheader("1 · Columns that record what already happened")
st.markdown(
    "A row in `flights` is written after the flight. Many columns, like actual departure time, "
    "taxi time, arrival delay and minutes blamed on each cause, are the answer in disguise. Every "
    "column of `flights_combined` is tagged with a role, and only the first two roles may be "
    "model inputs.")
roles = view["role"].map(ROLE_LABELS).value_counts().reindex(list(ROLE_LABELS.values()))
c = st.columns(4)
for col, (label, n) in zip(c, roles.items()):
    col.metric(label, n, border=True)
with st.expander("Which columns are outcomes or targets"):
    st.dataframe(view[view["role"].isin(["outcome", "target"])]
                 .assign(role=lambda d: d["role"].map(ROLE_LABELS))[["column_name", "role", "description"]],
                 hide_index=True, width="stretch",
                 column_config={"column_name": "Column", "role": "Role",
                                "description": st.column_config.TextColumn("Meaning", width="large")})

st.subheader("2 · Monthly statistics are always last month's")
st.markdown(
    "The delay-cause, hourly on-time and baggage tables are monthly totals. A February flight joined "
    "to February's totals would be learning from February's delays, including its own. So each "
    "monthly table has a `_prev_month` copy relabeled one month later, and `flights_combined` joins "
    "only to those. Here it is for American Airlines at Charlotte:")

st.markdown("**January 2024, as BTS published it** (`delay_cause_monthly`)")
st.dataframe(sample("example:source"), hide_index=True, width="stretch")
st.markdown("**↓ converted to rates and labeled February 2024** (`delay_cause_prev_month`)")
st.dataframe(sample("example:shifted"), hide_index=True, width="stretch",
             column_config={c: st.column_config.NumberColumn(format="%.3f")
                            for c in ["del15_rate", "cancel_rate", "carrier_rate", "weather_rate", "nas_rate",
                                      "late_aircraft_rate"]})
st.markdown("**↓ what AA flights leaving CLT on February 14, 2024 see** (`flights_combined`)")
st.dataframe(sample("example:flights"), hide_index=True, width="stretch",
             column_config={"origin_prev_del15_rate": st.column_config.NumberColumn(format="%.3f"),
                            "origin_prev_cancel_rate": st.column_config.NumberColumn(format="%.3f")})
st.caption("The flights carry January's numbers. Their own outcome, `dep_del15`, isn't part of "
           "any statistic they're joined to.")

st.subheader("3 · Weather is observed, not forecast")
st.markdown(
    "`weather_daily` is what actually happened that day (ERA5 reanalysis), including hours after "
    "departure. A traveler checking a flight in advance would only have a forecast. For training "
    "this is a reasonable stand-in, since forecasts a day out are usually close, but expect slightly "
    "lower accuracy on real future flights. The live app should feed in a forecast for the same fields.")

st.subheader("How to split for training")
st.markdown(
    "Split by time, not at random: train on 2023–2024 and test on 2025. A random split puts flights "
    "from the same day and airport, which share weather and congestion, on both sides, so the test "
    "score comes out better than the model really is.")
