"""The fixture is a real response for CLT and DEN, captured once with:
    httpx.get(forecast.request_url([CLT, DEN])).json()
"""
import json
from pathlib import Path

import pytest

from live import forecast
from live.airports import by_code

PAYLOAD = json.loads((Path(__file__).parent / "fixtures" / "openmeteo_two_airports.json").read_text())
AIRPORTS = [by_code()["CLT"], by_code()["DEN"]]


def test_parse_two_airports():
    daily, hourly = forecast.parse(PAYLOAD, AIRPORTS)
    assert len(daily) == 6 and len(hourly) == 144
    assert [r["airport"] for r in daily[:3]] == ["CLT"] * 3
    assert {r["airport"] for r in daily[3:]} == {"DEN"}
    assert isinstance(daily[0]["temp_max_f"], float)
    raw_vis = PAYLOAD[0]["hourly"]["visibility"][0]
    assert hourly[0]["visibility_mi"] == pytest.approx(raw_vis / 5280)
    assert hourly[0]["time_local"] == PAYLOAD[0]["hourly"]["time"][0]
    assert hourly[0]["local_date"] == hourly[0]["time_local"][:10]


def test_parse_single_location_dict():
    daily, hourly = forecast.parse(PAYLOAD[0], AIRPORTS[:1])
    assert len(daily) == 3 and len(hourly) == 72


def test_request_url_has_all_daily_vars():
    url = forecast.request_url(AIRPORTS)
    for name in forecast.DAILY_VARS:
        assert name in url
    assert "forecast_days=3" in url and "35.21" in url
