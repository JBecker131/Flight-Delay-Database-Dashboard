"""Save one real AeroDataBox response as the parser's test fixture (costs 2 of the 400 free units).

    python -m live.capture_sample
"""
import json
import os
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from live import aerodatabox, store

OUT = Path(__file__).parent / "tests" / "fixtures" / "aerodatabox_clt_departures.json"


def api_key():
    key = os.environ.get("AERODATABOX_KEY")
    env = Path(__file__).with_name(".env")
    if not key and env.exists():
        for line in env.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "AERODATABOX_KEY":
                key = value.strip().strip("'\"")
    if not key:
        raise SystemExit("Put AERODATABOX_KEY=... in live/.env first")
    return key


def main():
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    start, end = datetime.combine(tomorrow, time(6, 0)), datetime.combine(tomorrow, time(11, 59))
    engine = store.get_engine()
    store.create_tables(engine)
    with engine.begin() as conn:  # count it against the shared quota like any other fetch
        if not store.reserve_units(conn, datetime.now(UTC).strftime("%Y-%m"), store.UNITS_PER_CALL,
                                   store.API_UNIT_LIMIT):
            raise SystemExit("Monthly quota already used up")
    payload = aerodatabox.fetch_window("CLT", start, end, aerodatabox.make_client(api_key()))
    OUT.write_text(json.dumps(payload, indent=1))
    print(f"{len(payload.get('departures', []))} departures saved to {OUT}")


if __name__ == "__main__":
    main()
