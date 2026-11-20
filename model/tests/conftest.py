import pytest

from model.features import PREV_FIELDS, Lookups

CLT, DFW, BOI = (35.214, -80.9431, "America/New_York"), (32.8968, -97.038, "America/Chicago"), \
    (43.5644, -116.2228, "America/Boise")
SUNNY = {"weather_code": 1, "temp_max_f": 60.0, "temp_min_f": 40.0, "precip_in": 0.0, "rain_in": 0.0,
         "snowfall_in": 0.0, "precip_hours": 0.0, "wind_max_mph": 8.0, "gust_max_mph": 15.0}
STORM = {**SUNNY, "weather_code": 95, "precip_in": 1.2, "rain_in": 1.2, "precip_hours": 6.0,
         "wind_max_mph": 30.0, "gust_max_mph": 55.0}


AIRPORTS = {"CLT": CLT, "DFW": DFW, "BOI": BOI}
META = {"published_at": "2026-11-19T15:40:28+00:00"}


@pytest.fixture
def tables():
    """CLT, DFW and BOI, with November lookups for American and its regional PSA (OH)."""
    stats = dict(zip(PREV_FIELDS, [500.0, 0.2, 0.01, 60.0, 0.06, 0.005, 0.05, 0.08]))
    return Lookups(
        operator={("AA", 5069, "CLT"): "OH", ("AA", 5069, "*"): "MQ"},
        delay_cause={(c, a, 11): stats for c in ("AA", "OH") for a in ("CLT", "DFW")},
        ontime={("CLT", 11, "0900-0959"): (80.0, 85.0), ("DFW", 11, "1100-1159"): (78.0, 82.0),
                ("CLT", 11, "2300-0559"): (70.0, 75.0)},
        bags={("AA", 11): 0.6, ("OH", 11): 0.4},
        congestion_day={("CLT", 11, 6): 600.0},
        congestion_hour={("CLT", 11, 6, 9): 45.0},
    )


def sunny(airport, day):
    return SUNNY, "forecast"
