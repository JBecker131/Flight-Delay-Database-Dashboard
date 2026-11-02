from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import insert, update

from live import store
from live.api import create_app

NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)


def fake_fetcher(airport, start, end):
    day = start.date().isoformat()
    return [{"airline": "AA", "airline_name": "American", "flight_number": f"AA {start.hour}{i}", "dest": "ATL",
             "dep_local": f"{day}T08:00", "dep_utc": f"{day}T13:0{i}Z",
             "arr_local": f"{day}T09:30", "arr_utc": f"{day}T14:30Z"} for i in range(2)]


def client(engine, now=NOW):
    return TestClient(create_app(engine, fake_fetcher, board_dir=None, now=lambda: now))


def test_departures_first_open(engine):
    r = client(engine).get("/api/departures", params={"airport": "CLT"})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ok"
    assert body["days"] == ["2026-11-02", "2026-11-03"]
    assert len(body["flights"]) == 8
    assert r.headers["cache-control"] == "no-store"


def test_lowercase_code_ok_unknown_404(engine):
    c = client(engine)
    assert c.get("/api/departures", params={"airport": "clt"}).status_code == 200
    assert c.get("/api/departures", params={"airport": "XXX"}).status_code == 404


def test_quota_exhausted_still_returns_saved_days(engine):
    client(engine).get("/api/departures", params={"airport": "CLT"})
    with engine.begin() as conn:
        conn.execute(update(store.api_usage).values(units_used=store.API_UNIT_LIMIT))
    body = client(engine, NOW + timedelta(days=1)).get("/api/departures", params={"airport": "CLT"}).json()
    assert body["status"] == "quota_exhausted"
    assert {f["dep_local"][:10] for f in body["flights"]} == {"2026-11-03"}


def test_dest_weather_joined(engine):
    with engine.begin() as conn:
        conn.execute(insert(store.forecast_hourly).values(airport="ATL", time_local="2026-11-02T09:00",
                                                          local_date="2026-11-02", temp_f=61.0, weather_code=3))
        conn.execute(insert(store.forecast_daily).values(airport="CLT", local_date="2026-11-02", temp_max_f=70.0))
    flights = client(engine).get("/api/departures", params={"airport": "CLT"}).json()["flights"]
    first = flights[0]
    assert first["dest_weather"]["temp_f"] == 61.0
    assert first["origin_weather"]["temp_max_f"] == 70.0
    assert flights[-1]["dest_weather"] is None  # nothing seeded for tomorrow


def test_airports_list(engine):
    airports = client(engine).get("/api/airports").json()
    assert len(airports) == 362
    assert airports[0]["city"] == min(a["city"] for a in airports)
    assert set(airports[0]) == {"code", "city", "board", "tz"}


def test_board_serves_only_board_files(engine, tmp_path):
    (tmp_path / "index.html").write_text("<h1>board</h1>")
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "app.js").write_text("// app")
    (tmp_path / "live").mkdir()
    (tmp_path / "live" / ".env").write_text("AERODATABOX_KEY=secret")
    c = TestClient(create_app(engine, fake_fetcher, board_dir=tmp_path, now=lambda: NOW))
    assert "board" in c.get("/").text
    assert c.get("/js/app.js").status_code == 200
    assert c.get("/live/.env").status_code == 404


def test_bad_saved_row_does_not_break_the_airport(engine):
    def fetcher(airport, start, end):
        return [{"airline": "AA", "flight_number": "AA 9", "dest": "ATL", "dep_local": f"{start:%Y-%m-%d}T08:00",
                 "dep_utc": f"{start:%Y-%m-%d}T13:00:00Z", "arr_utc": "not a time"}]
    r = TestClient(create_app(engine, fetcher, now=lambda: NOW)).get("/api/departures", params={"airport": "CLT"})
    assert r.status_code == 200 and r.json()["flights"][0]["dest_weather"] is None
