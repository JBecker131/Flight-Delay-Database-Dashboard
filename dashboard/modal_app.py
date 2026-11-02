"""Serve the dashboard on Modal.

    modal serve modal_app.py    # temporary URL that reloads as you edit
    modal deploy modal_app.py   # permanent URL: https://<workspace>--flight-delay-database-dashboard.modal.run

Reads the Supabase connection string from the Modal secret "flight-dashboard-db",
which holds DASHBOARD_DB_URL.
"""
import subprocess
from pathlib import Path

import modal

HERE = Path(__file__).parent
REMOTE = "/root/dashboard"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(requirements=[str(HERE / "requirements.txt")])
    # .env holds the database password locally; the deployed app gets it from the secret instead.
    .add_local_dir(HERE, REMOTE, ignore=[".env", "**/__pycache__", "publish.py"])
)

app = modal.App("flight-delay-database", image=image)


@app.function(
    secrets=[modal.Secret.from_name("flight-dashboard-db")],
    # Each Streamlit session lives on its own websocket, so sessions don't need to share a container. Two lets a
    # new deploy start while the old container is still holding open browser tabs; with one, it waited for them.
    max_containers=2,
    scaledown_window=15 * 60,  # stay warm for a quarter hour after the last visitor
    timeout=60 * 60,  # each browser tab holds one websocket; the 5-minute default cut it and forced a reload
)
@modal.concurrent(max_inputs=100)
@modal.web_server(8000, startup_timeout=60, label="flight-delay-database-dashboard")
def dashboard():
    subprocess.Popen(
        "streamlit run streamlit_app.py --server.port 8000 --server.address 0.0.0.0 "
        "--server.enableCORS=false --server.enableXsrfProtection=false",
        shell=True, cwd=REMOTE)
