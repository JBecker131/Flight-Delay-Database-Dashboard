"""Serve the delay model's API on Modal, for the Vercel site.

    python model/export_serving.py            # once, and after retraining: copies the model into artifacts/
    python -m modal serve model/modal_app.py  # temporary URL that reloads as you edit
    python -m modal deploy model/modal_app.py # permanent URL: https://<workspace>--flight-delay-predict.modal.run

Secrets:
  "flight-dashboard-db"  DASHBOARD_DB_URL, the Supabase pooler URI (already used by the dashboard and board)
  "flight-delay-api"     PREDICT_API_KEY and ALLOWED_ORIGINS (see model/api.py), created with
      python -m modal secret create flight-delay-api PREDICT_API_KEY=<long random string> \
          ALLOWED_ORIGINS=https://<your-app>.vercel.app,http://localhost:3000
"""
from pathlib import Path

import modal

HERE = Path(__file__).parent

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(requirements=[str(HERE / "requirements-serving.txt")])
    # Requests are scored a few rows at a time, so one thread each keeps concurrent requests from fighting.
    .env({"OMP_NUM_THREADS": "1"})
    .add_local_dir(HERE, "/root/model", ignore=["tests", "tests/**", "**/__pycache__", "*.ipynb", "**/.env",
                                                "publish_supabase.py", "export_serving.py", "requirements.txt"])
)

app = modal.App("flight-delay-model", image=image)


@app.function(
    secrets=[modal.Secret.from_name("flight-dashboard-db"), modal.Secret.from_name("flight-delay-api")],
    cpu=1.0,
    memory=1024,
    max_containers=3,
    scaledown_window=10 * 60,  # stay warm 10 minutes after the last request; a cold start reads the lookups again
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app(label="flight-delay-predict")
def api():
    from model.api import app_from_env

    return app_from_env()
