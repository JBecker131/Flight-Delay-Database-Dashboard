"""Shared locations and date range for the data pipeline.

The data lives outside OneDrive because the raw flight files are several GB.
Set FLIGHT_DATA_DIR to put it somewhere else.
"""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("FLIGHT_DATA_DIR", Path.home() / "flight_data"))
RAW_DIR = DATA_DIR / "raw"
ONTIME_DIR = RAW_DIR / "ontime"
DELAY_CAUSE_DIR = RAW_DIR / "delay_cause"
WEATHER_DIR = RAW_DIR / "weather"
DB_PATH = DATA_DIR / "flights.db"

YEARS = [2023, 2024, 2025]
START_DATE = "2023-01-01"
END_DATE = "2025-12-31"


def ontime_zip(year, month):
    return ONTIME_DIR / f"On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
