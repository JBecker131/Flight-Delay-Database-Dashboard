"""The API end to end, with the real saved model and in-memory lookups (no Supabase, no network)."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from model.api import create_app
from model.lookups import InMemoryLookups
from model.service import Predictor, load_artifacts
from model.tests.conftest import AIRPORTS, META, STORM, SUNNY

FLIGHT = {"id": "row-1", "airline": "AA", "flight_number": "AA 5069", "origin": "CLT", "dest": "DFW",
          "departure": "2026-11-21T09:05", "arrival": "2026-11-21T11:20"}
FLIGHT_DAY = date(2026, 11, 21)


class FakeWeather:
    def __init__(self, by_airport):
        self.by_airport, self.prefetched = by_airport, []

    def prefetch(self, pairs):
        self.prefetched.extend(pairs)

    def get(self, airport, day):
        values = self.by_airport.get(airport)
        return values, ("forecast" if values else None)


@pytest.fixture(scope="module")
def artifacts():
    model, feature_meta, serving, _ = load_artifacts()
    return model, feature_meta, serving


@pytest.fixture
def make_client(artifacts, tables):
    def make(weather=None, **kw):
        predictor = Predictor(*artifacts, AIRPORTS, InMemoryLookups(tables, META),
                              weather or FakeWeather({"CLT": SUNNY, "DFW": SUNNY}))
        return TestClient(create_app(predictor, **kw)), predictor
    return make


def test_predict_returns_delay_risk_and_chances(make_client):
    client, predictor = make_client()
    r = client.post("/predict", json={"flights": [FLIGHT]})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    [p] = r.json()["predictions"]
    assert p["id"] == "row-1" and p["error"] is None
    assert p["operating_airline"] == "OH"
    assert 0 <= p["expected_delay_min"] < 120
    assert p["risk"] in {"low", "medium", "high"}
    assert 0 < p["chance_late_15"] < 1 and 0 < p["chance_late_60"] < p["chance_late_15"]
    assert "features" not in p
    assert predictor.weather.prefetched == [("CLT", FLIGHT_DAY), ("DFW", FLIGHT_DAY)]



def test_storms_raise_the_prediction(make_client):
    calm, _ = make_client()
    stormy, _ = make_client(FakeWeather({"CLT": STORM, "DFW": STORM}))
    a = calm.post("/predict", json={"flights": [FLIGHT]}).json()["predictions"][0]
    b = stormy.post("/predict", json={"flights": [FLIGHT]}).json()["predictions"][0]
    assert b["expected_delay_min"] > a["expected_delay_min"]
    assert b["chance_late_15"] >= a["chance_late_15"]


def test_batch_keeps_order_and_reports_bad_rows_without_failing(make_client):
    client, _ = make_client()
    flights = [FLIGHT, {**FLIGHT, "id": 2, "dest": "XXX"}, {**FLIGHT, "id": 3, "departure": "2026-11-21T19:05",
                                                           "arrival": None}]
    preds = client.post("/predict", json={"flights": flights, "include_features": True}).json()["predictions"]
    assert [p["id"] for p in preds] == ["row-1", 2, 3]
    assert preds[1] == {"id": 2, "error": "unknown airport XXX"}
    assert preds[2]["error"] is None and preds[2]["features"]["dep_hour"] == 19
    assert len(preds[0]["features"]) == 53
    assert preds[0]["features"]["origin_prev_hour_dep_ontime_pct"] == 85.0


def test_get_predict_scores_one_flight(make_client):
    client, _ = make_client()
    params = {k: v for k, v in FLIGHT.items() if k != "id"}
    r = client.get("/predict", params=params)
    assert r.status_code == 200 and r.json()["operating_airline"] == "OH"
    assert client.get("/predict", params={**params, "origin": "XXX"}).status_code == 422
    assert client.get("/predict", params={**params, "origin": "TOOLONG"}).status_code == 422


def test_request_validation(make_client):
    client, _ = make_client()
    assert client.post("/predict", json={"flights": []}).status_code == 422
    assert client.post("/predict", json={"flights": [FLIGHT] * 501}).status_code == 422
    assert client.post("/predict", json={"flights": [{"airline": "AA"}]}).status_code == 422


def test_api_key(make_client):
    client, _ = make_client(api_key="s3cret")
    body = {"flights": [FLIGHT]}
    assert client.post("/predict", json=body).status_code == 401
    assert client.post("/predict", json=body, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/predict", json=body, headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.post("/predict", json=body, headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200  # health and model info stay open
    assert client.get("/model").status_code == 200


def test_cors_allows_only_listed_origins(make_client):
    client, _ = make_client(allowed_origins=["https://flights.vercel.app"])
    ok = client.options("/predict", headers={"Origin": "https://flights.vercel.app",
                                              "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "https://flights.vercel.app"
    other = client.options("/predict", headers={"Origin": "https://evil.example",
                                                 "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in other.headers


def test_model_info(make_client):
    client, _ = make_client()
    info = client.get("/model").json()
    assert info["test"]["flights"] == 411_465 and len(info["features"]) == 53
    assert info["lookups"]["published_at"].startswith("2026-11-19")


def test_risk_labels_follow_the_test_set_thresholds(artifacts, tables):
    predictor = Predictor(*artifacts, AIRPORTS, InMemoryLookups(tables), FakeWeather({}))
    t = predictor.serving["risk_thresholds"]
    assert predictor.describe(0)["risk"] == "low"
    assert predictor.describe(t["medium"])["risk"] == "medium"
    assert predictor.describe(t["high"] + 50)["risk"] == "high"
    chances = [predictor.describe(v)["chance_late_15"] for v in (0, 10, 20, 40, 200)]
    assert chances == sorted(chances)
