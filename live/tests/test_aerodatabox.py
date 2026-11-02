import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from live import aerodatabox
from live.schedules import QuotaExhausted

FIXTURE = Path(__file__).parent / "fixtures" / "aerodatabox_clt_departures.json"


def item(dest="ATL", **overrides):
    """One departure in AeroDataBox's FIDS format."""
    base = {
        "departure": {"scheduledTime": {"utc": "2026-11-03 11:00Z", "local": "2026-11-03 06:00-05:00"},
                      "terminal": "B", "gate": "B5"},
        "arrival": {"airport": {"iata": dest, "name": "Somewhere"},
                    "scheduledTime": {"utc": "2026-11-03 12:25Z", "local": "2026-11-03 07:25-05:00"}},
        "number": "AA 1234", "status": "Expected", "codeshareStatus": "IsOperator", "isCargo": False,
        "aircraft": {"model": "Airbus A321"}, "airline": {"name": "American", "iata": "AA", "icao": "AAL"},
    }
    base.update(overrides)
    return base


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=aerodatabox.BASE)


def test_parse_maps_fields():
    [row] = aerodatabox.parse({"departures": [item()]}, "CLT")
    assert row == {"airline": "AA", "airline_name": "American", "flight_number": "AA 1234", "dest": "ATL",
                   "dep_local": "2026-11-03T06:00", "dep_utc": "2026-11-03T11:00Z",
                   "arr_local": "2026-11-03T07:25", "arr_utc": "2026-11-03T12:25Z",
                   "aircraft": "Airbus A321", "terminal": "B", "gate": "B5", "status": "Expected"}


def test_parse_normalizes_flight_number():
    [row] = aerodatabox.parse({"departures": [item(number="AA1234")]}, "CLT")
    assert row["flight_number"] == "AA 1234"


def test_parse_drops_unknown_destinations():
    rows = aerodatabox.parse({"departures": [item("LHR"), item("ATL")]}, "CLT")
    assert [r["dest"] for r in rows] == ["ATL"]


def test_parse_tolerates_missing_fields():
    bare = item()
    del bare["aircraft"]
    bare["departure"] = {"scheduledTime": bare["departure"]["scheduledTime"]}
    del bare["arrival"]["scheduledTime"]
    [row] = aerodatabox.parse({"departures": [bare]}, "CLT")
    assert row["aircraft"] is None and row["gate"] is None and row["terminal"] is None
    assert row["arr_utc"] is None and row["arr_local"] is None


def test_parse_drops_flights_without_departure_time():
    no_time = item()
    no_time["departure"] = {}
    assert aerodatabox.parse({"departures": [no_time]}, "CLT") == []


def test_parse_fixture():
    if not FIXTURE.exists():
        pytest.skip("run live/capture_sample.py with an AeroDataBox key first")
    rows = aerodatabox.parse(json.loads(FIXTURE.read_text()), "CLT")
    assert rows
    for row in rows:
        assert len(row["dest"]) == 3
        datetime.strptime(row["dep_utc"], "%Y-%m-%dT%H:%MZ")
        code, _, digits = row["flight_number"].partition(" ")
        assert code and digits.isdigit()


def test_client_raises_quota_error_on_429():
    client = client_for(lambda request: httpx.Response(429, json={"message": "quota exceeded"}))
    with pytest.raises(QuotaExhausted):
        aerodatabox.fetch_window("CLT", datetime(2026, 11, 2), datetime(2026, 11, 2, 11, 59), client, min_interval=0)


def test_client_treats_204_as_empty():
    client = client_for(lambda request: httpx.Response(204))
    payload = aerodatabox.fetch_window("CLT", datetime(2026, 11, 2), datetime(2026, 11, 2, 11, 59), client,
                                       min_interval=0)
    assert payload == {"departures": []}


def test_request_path_and_params():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"departures": []})

    aerodatabox.fetch_window("CLT", datetime(2026, 11, 2), datetime(2026, 11, 2, 11, 59), client_for(handler),
                             min_interval=0)
    [request] = seen
    assert request.url.path == "/flights/airports/iata/CLT/2026-11-02T00:00/2026-11-02T11:59"
    assert request.url.params["direction"] == "Departure"
    assert request.url.params["withCodeshared"] == "false"
    assert request.url.params["withCancelled"] == "true"


def test_parse_keeps_digits_in_airline_codes():
    numbers = {"B6 123": "B6 123", "F9 1001": "F9 1001", "9E 5012": "9E 5012", "G4 22": "G4 22", "AA1234": "AA 1234"}
    for raw, expected in numbers.items():
        code = raw[:2]
        [row] = aerodatabox.parse({"departures": [item(number=raw, airline={"name": "X", "iata": code})]}, "CLT")
        assert row["flight_number"] == expected, raw


def test_parse_drops_rows_without_number_or_local_time():
    no_number = item()
    del no_number["number"]
    no_local = item()
    no_local["departure"]["scheduledTime"] = {"utc": "2026-11-03 11:00Z"}
    assert aerodatabox.parse({"departures": [no_number, no_local]}, "CLT") == []


def test_rate_limit_429_retries_then_errors():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"message": "You have exceeded the rate limit per second for your plan"})

    with pytest.raises(aerodatabox.RateLimited):
        aerodatabox.fetch_window("CLT", datetime(2026, 11, 2), datetime(2026, 11, 2, 11, 59), client_for(handler),
                                 min_interval=0, retry_wait=0)
    assert len(calls) == 2


def test_monthly_quota_429_by_header():
    client = client_for(lambda request: httpx.Response(429, headers={"x-ratelimit-requests-remaining": "0"},
                                                       json={"message": "Too many requests"}))
    with pytest.raises(QuotaExhausted):
        aerodatabox.fetch_window("CLT", datetime(2026, 11, 2), datetime(2026, 11, 2, 11, 59), client,
                                 min_interval=0, retry_wait=0)


def test_unknown_airport_404_is_an_empty_day():
    client = client_for(lambda request: httpx.Response(404, json={"message": "Airport not found"}))
    assert aerodatabox.fetch_window("PIR", datetime(2026, 11, 2), datetime(2026, 11, 2, 11, 59), client,
                                    min_interval=0) == {"departures": []}


def test_fetcher_refuses_payload_it_cannot_read():
    """Departures to BTS airports came back but none parsed: the format changed, so don't save an empty day."""
    odd = {"departures": [{"number": "AA 1", "arrival": {"airport": {"iata": "ATL"}}, "departure": {"when": "?"}}]}
    fetch = aerodatabox.make_fetcher("key", client=client_for(lambda request: httpx.Response(200, json=odd)),
                                     min_interval=0)
    with pytest.raises(aerodatabox.UnreadablePayload):
        fetch("CLT", datetime(2026, 11, 2), datetime(2026, 11, 2, 11, 59))
