"""The airports and airlines in the BTS data, from airports.json (see export_airports.py)."""
import json
from functools import cache
from pathlib import Path


@cache
def load():
    return json.loads(Path(__file__).with_name("airports.json").read_text())


@cache
def by_code():
    return {a["code"]: a for a in load()["airports"]}
