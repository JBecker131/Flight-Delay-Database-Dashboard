"""Where the dashboard's summary tables live, shared by publish.py and the app.

DASHBOARD_DB_URL picks the database: your Supabase connection string in
production, or, when it is unset, a local SQLite file for trying things out.
It is read from the environment first, then from dashboard/.env.
"""
import os
from pathlib import Path

from sqlalchemy import create_engine

ENV_FILE = Path(__file__).parent / ".env"
LOCAL_URL = "sqlite:///" + str(Path(os.environ.get("FLIGHT_DATA_DIR", Path.home() / "flight_data"))
                               / "dashboard.db").replace("\\", "/")

# Supabase exposes the public schema through its REST API, so the summaries go
# in their own schema to keep them off it.
POSTGRES_SCHEMA = "dashboard"


def database_url():
    url = os.environ.get("DASHBOARD_DB_URL")
    if not url and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "DASHBOARD_DB_URL":
                url = value.strip().strip("'\"")
    url = url or LOCAL_URL
    # Supabase hands out postgresql:// URLs; SQLAlchemy needs to be told to use psycopg 3.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


def get_engine():
    return create_engine(database_url(), pool_pre_ping=True)


def schema_for(engine):
    return POSTGRES_SCHEMA if engine.dialect.name == "postgresql" else None
