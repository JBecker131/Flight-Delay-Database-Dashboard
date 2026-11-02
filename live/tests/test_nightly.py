from datetime import UTC, datetime

from sqlalchemy import func, insert, select

from live import nightly, store

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def test_nightly_preloads_clt_and_purges(engine, monkeypatch):
    monkeypatch.setattr(nightly.forecast, "refresh", lambda engine: 362)
    calls = []

    def fetcher(airport, start, end):
        calls.append(airport)
        return []

    with engine.begin() as conn:
        conn.execute(insert(store.departures).values(airport="ATL", local_date="2026-10-31", dep_utc="x"))
    result = nightly.run(engine, fetcher, NOW)
    assert calls == ["CLT"] * 4
    assert result == {"forecast_airports": 362, "clt": "ok"}
    with engine.begin() as conn:
        assert conn.scalar(select(func.count()).select_from(store.departures)
                           .where(store.departures.c.local_date == "2026-10-31")) == 0
