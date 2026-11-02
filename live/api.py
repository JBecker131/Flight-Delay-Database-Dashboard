"""The board's API, plus the board itself as static files.

    GET /api/airports                 every airport in the BTS data
    GET /api/departures?airport=CLT   today's and tomorrow's departures, fetched first if nobody has yet
"""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from live import schedules, store
from live.airports import by_code, load

HIDDEN = {"id", "fetched_at", "airport", "local_date"}


def _instant(value):
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _arrival_hour(flight):
    """The destination's local hour the flight lands in, to match an hourly forecast row, or None."""
    when = _instant(flight["arr_utc"])
    if when is None:
        when = _instant(flight["dep_utc"])
        if when is None:
            return None
        when += timedelta(hours=2)  # no usable arrival time: assume a typical domestic flight
    return when.astimezone(ZoneInfo(by_code()[flight["dest"]]["tz"])).strftime("%Y-%m-%dT%H:00")


def departures_payload(engine, airport, days):
    iso_days = [d.isoformat() for d in days]
    with engine.begin() as conn:
        rows = conn.execute(select(store.departures)
                            .where(store.departures.c.airport == airport,
                                   store.departures.c.local_date.in_(iso_days))
                            .order_by(store.departures.c.dep_utc)).mappings().all()
        flights = [{k: v for k, v in r.items() if k not in HIDDEN} for r in rows]
        dests = {f["dest"] for f in flights}
        fd, fh = store.forecast_daily, store.forecast_hourly
        daily = {r["local_date"]: dict(r) for r in conn.execute(
            select(fd).where(fd.c.airport == airport)).mappings()}
        hourly = {(r["airport"], r["time_local"]): dict(r) for r in conn.execute(
            select(fh).where(fh.c.airport.in_(dests))).mappings()}
    for f in flights:
        f["origin_weather"] = daily.get((f["dep_local"] or "")[:10])
        f["dest_weather"] = hourly.get((f["dest"], _arrival_hour(f)))
    return flights


def create_app(engine, fetcher, board_dir=None, now=lambda: datetime.now(UTC)):
    app = FastAPI(title="Live departures", docs_url=None, redoc_url=None)

    @app.get("/api/airports")
    def airports(response: Response):
        response.headers["Cache-Control"] = "no-store"
        rows = [{k: a[k] for k in ("code", "city", "board", "tz")} for a in load()["airports"]]
        return sorted(rows, key=lambda a: a["city"])

    @app.get("/api/departures")
    def departures(airport: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        code = airport.strip().upper()
        if code not in by_code():
            raise HTTPException(404, f"Unknown airport {code}")
        current = now()
        status = schedules.ensure(engine, code, fetcher, current)
        days = schedules.local_days(code, current)
        return {"airport": code, "status": status, "days": [d.isoformat() for d in days],
                "flights": departures_payload(engine, code, days)}

    if board_dir:
        # Only the board's own files: the project folder also holds .env files with secrets.
        board_dir = Path(board_dir)
        for folder in ("css", "js"):
            if (board_dir / folder).is_dir():
                app.mount(f"/{folder}", StaticFiles(directory=board_dir / folder), name=folder)

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(board_dir / "index.html")
    return app
