"""Serve the live departures board on Modal and run the nightly batch.

    python -m modal run live/modal_app.py::nightly_job   # run the nightly batch once, now
    python -m modal deploy live/modal_app.py             # board + nightly schedule

Secrets: "flight-dashboard-db" (DASHBOARD_DB_URL, the Supabase pooler URI) and "aerodatabox" (AERODATABOX_KEY).
"""
import os
from datetime import UTC, datetime
from pathlib import Path

import modal

HERE = Path(__file__).parent
ROOT = HERE.parent

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(requirements=[str(HERE / "requirements.txt")])
    # .env holds the key and database password locally; the deployed app gets them from secrets instead.
    .add_local_dir(HERE, "/root/live", ignore=[".env", "**/.env", "tests", "tests/**", "**/__pycache__",
                                               "capture_sample.py", "export_airports.py", "dev_server.py"])
    .add_local_file(ROOT / "index.html", "/root/board/index.html")
    .add_local_dir(ROOT / "css", "/root/board/css")
    .add_local_dir(ROOT / "js", "/root/board/js")
)

app = modal.App("flight-departures", image=image)
secrets = [modal.Secret.from_name("flight-dashboard-db"), modal.Secret.from_name("aerodatabox")]


@app.function(
    secrets=secrets,
    max_containers=1,  # one container, so the per-airport locks stop duplicate fetches
    scaledown_window=15 * 60,
)
@modal.concurrent(max_inputs=100)
@modal.asgi_app(label="flight-departures-board")
def board():
    from live import aerodatabox, store
    from live.api import create_app

    engine = store.get_engine()
    store.create_tables(engine)
    return create_app(engine, aerodatabox.make_fetcher(os.environ["AERODATABOX_KEY"]), board_dir="/root/board")


@app.function(secrets=secrets, schedule=modal.Cron("0 9 * * *"), timeout=600)  # 4-5 AM Eastern
def nightly_job():
    from live import aerodatabox, nightly, store

    result = nightly.run(store.get_engine(), aerodatabox.make_fetcher(os.environ["AERODATABOX_KEY"]),
                         datetime.now(UTC))
    print(result)
