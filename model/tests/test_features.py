import math

import pytest

from model import features as feat
from model.tests.conftest import AIRPORTS, STORM, sunny

FIT = {"intercept": 47.0, "per_mile": 0.12}


def build(tables, weather=sunny, **kw):
    flight = {"airline": "AA", "origin": "CLT", "dest": "DFW", "departure": "2026-11-21T09:05",
              "arrival": "2026-11-21T11:20", **kw}
    s = feat.schedule(feat.Flight(**flight), AIRPORTS, FIT)
    return feat.build(s, feat.operating_carrier(s, tables.operator), tables, weather)


def test_schedule_features_use_local_times_at_each_end(tables):
    row, _ = build(tables)
    # Saturday 21 November 2026: BTS counts Monday as 1, so Saturday is 6.
    assert (row["month"], row["day_of_week"], row["day_of_month"]) == (11, 6, 21)
    assert (row["dep_hour"], row["arr_hour"]) == (9, 11)
    assert row["sched_elapsed_time"] == 195  # 09:05 Eastern to 11:20 Central
    assert row["distance"] == pytest.approx(936, abs=3)  # BTS lists CLT-DFW as 936 miles


def test_times_with_an_offset_are_converted_to_local(tables):
    row, _ = build(tables, departure="2026-11-21T14:05Z", arrival="2026-11-21T17:20Z")
    assert (row["dep_hour"], row["arr_hour"], row["sched_elapsed_time"]) == (9, 11, 195)


def test_missing_arrival_is_estimated_from_distance(tables):
    row, info = build(tables, arrival=None)
    assert row["sched_elapsed_time"] == round(47 + 0.12 * 936.4)
    assert row["arr_hour"] == 10  # 09:05 Eastern + 2h39m = 10:44 Central
    assert any("no arrival time" in n for n in info["notes"])


def test_lookups_match_training_keys(tables):
    row, _ = build(tables)
    assert row["origin_prev_del15_rate"] == 0.2 and row["dest_prev_avg_delay_min"] == 60.0
    assert row["origin_prev_hour_dep_ontime_pct"] == 85.0   # departure on-time % for the departure hour
    assert row["dest_prev_hour_arr_ontime_pct"] == 78.0     # arrival on-time % for the arrival hour
    assert row["airline_prev_bags_mishandled_per_100"] == 0.6
    assert (row["origin_day_flights"], row["origin_hour_flights"]) == (600.0, 45.0)


def test_late_night_hours_use_the_overnight_block(tables):
    row, _ = build(tables, departure="2026-11-21T23:30", arrival="2026-11-22T01:00")
    assert row["origin_prev_hour_dep_ontime_pct"] == 75.0
    assert feat.time_block(0) == feat.time_block(5) == feat.time_block(23) == "2300-0559"
    assert feat.time_block(6) == "0600-0659" and feat.time_block(22) == "2200-2259"


def test_brand_flight_number_is_scored_as_the_regional_that_flies_it(tables):
    for number in (5069, "5069", "AA 5069", "AA5069"):
        row, info = build(tables, flight_number=number)
        assert row["airline"] == info["operating_airline"] == "OH"
    assert row["airline_prev_bags_mishandled_per_100"] == 0.4
    # Elsewhere the number falls back to the airline that flew it most; unknown numbers stay mainline.
    assert build(tables, flight_number=5069, origin="DFW", dest="CLT")[0]["airline"] == "MQ"
    assert build(tables, flight_number=1812)[0]["airline"] == "AA"
    assert build(tables, airline="WN", flight_number=5069)[0]["airline"] == "WN"


def test_weather_and_flags(tables):
    row, info = build(tables, weather=lambda a, d: (STORM, "open-meteo") if a == "DFW" else (None, None))
    assert row["dest_precip_in"] == 1.2 and row["dest_storm"] == 1.0 and row["dest_snow"] == 0.0
    assert math.isnan(row["origin_temp_max_f"]) and math.isnan(row["origin_storm"])
    assert info["weather"] == {"origin": None, "dest": "open-meteo"}
    assert any("no weather forecast for the origin" in n for n in info["notes"])
    assert feat.snow(73) == feat.snow(86) == 1.0 and feat.snow(80) == 0.0


def test_missing_lookups_are_nan_and_congestion_counts_the_flight_itself(tables):
    row, _ = build(tables, origin="BOI", departure="2026-11-21T09:05", arrival=None)
    assert math.isnan(row["origin_prev_del15_rate"]) and math.isnan(row["origin_prev_hour_dep_ontime_pct"])
    assert row["origin_hour_flights"] == row["origin_day_flights"] == 1.0


@pytest.mark.parametrize("kw, message", [
    ({"origin": "XXX"}, "unknown airport XXX"),
    ({"dest": "CLT"}, "same airport"),
    ({"departure": "tomorrow"}, "isn't an ISO date"),
    ({"arrival": "2026-11-21T07:00"}, "arrival must be after departure"),
])
def test_unscorable_flights_raise(tables, kw, message):
    with pytest.raises(feat.FlightError, match=message):
        build(tables, **kw)


def test_table_keys_name_the_rows_build_reads(tables):
    s = feat.schedule(feat.Flight("AA", "CLT", "DFW", "2026-11-21T09:05", "2026-11-21T11:20", 5069), AIRPORTS, FIT)
    assert feat.operator_keys(s) == [("AA", 5069, "CLT"), ("AA", 5069, "*")]
    keys = feat.table_keys(s, "OH")
    assert keys["delay_cause"] == [("OH", "CLT", 11), ("OH", "DFW", 11)]
    assert keys["ontime"] == [("CLT", 11, "0900-0959"), ("DFW", 11, "1100-1159")]
    assert keys["bags"] == [("OH", 11)]
    assert keys["congestion_hour"] == [("CLT", 11, 6, 9)]
    assert feat.operator_keys(feat.schedule(feat.Flight("WN", "CLT", "DFW", "2026-11-21T09:05",
                                                        flight_number=12), AIRPORTS, FIT)) == []


def test_frame_has_model_columns_and_maps_unseen_codes_to_other(tables):
    row, _ = build(tables)
    levels = {"airline": ["AA", "OTHER"], "origin": ["CLT", "OTHER"], "dest": ["OTHER"]}
    df = feat.to_frame([row], list(row), levels)
    assert list(df.columns) == list(row)
    assert df.loc[0, "dest"] == "OTHER" and str(df["origin"].dtype) == "category"
    assert list(df["airline"].cat.categories) == ["AA", "OTHER"]
