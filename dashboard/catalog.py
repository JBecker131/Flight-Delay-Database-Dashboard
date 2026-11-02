"""Hand-written descriptions of every table and column in flights.db.

publish.py stores these next to the computed profiles, so the dashboard reads
everything from one place.
"""

BTS_ONTIME = "BTS On-Time Reporting"
BTS_CAUSES = "BTS Delay Causes"
OPEN_METEO = "Open-Meteo"
DOT_ATCR = "DOT Air Travel Consumer Reports"
DERIVED = "Derived"
OPEN_METEO_FORECAST = "Open-Meteo forecast"
AERODATABOX = "AeroDataBox"

# Sources for the departures board's upcoming flights (live/), kept in Supabase's `live` schema, not flights.db
LIVE_SOURCES = {
    OPEN_METEO_FORECAST: {
        "publisher": "Open-Meteo weather forecast API",
        "url": "https://open-meteo.com/en/docs",
        "summary": "A 3-day forecast at every airport: the same daily variables as the historical "
                   "weather (weather code, high and low temperature, precipitation, snowfall, peak wind "
                   "and gusts) plus the chance of rain, and an hourly forecast for the board's weather card.",
        "how": "A Modal job every night at 09:00 UTC requests all 362 airports in two batches of up to "
               "200 locations and replaces the previous forecast. Free for non-commercial use. Using the "
               "same daily variables as the archive means the model sees the same features for upcoming "
               "flights as it was trained on.",
    },
    AERODATABOX: {
        "publisher": "AeroDataBox flight data API, via RapidAPI (Basic free plan)",
        "url": "https://aerodatabox.com/",
        "summary": "Real scheduled departures for today and tomorrow at an airport: airline, flight "
                   "number, destination, scheduled departure and arrival times, aircraft, and status.",
        "how": "Fetched the first time anyone opens an airport on the board, in 12-hour windows (the "
               "free plan's limit), then saved and shared with every later visitor. Charlotte is "
               "loaded every night. The free plan allows 400 units a month at 2 units a request, so a "
               "counter stops visitor fetches at 380. Codeshare, cargo and private flights are excluded, "
               "and only flights to airports in the BTS data are kept. BTS can't supply upcoming flights: "
               "it publishes them about two months after they fly.",
    },
}

SOURCES = {
    BTS_ONTIME: {
        "publisher": "Bureau of Transportation Statistics",
        "url": "https://www.transtats.bts.gov/ONTIME/",
        "summary": "Every domestic flight operated by a large U.S. carrier, with scheduled and actual "
                   "times, delays, cancellations, and the minutes of delay blamed on each cause.",
        "how": "36 monthly zip files (one per month, 2023–2025) from the BTS PREZIP download "
               "folder. Each holds about 110 columns; we keep the 39 that describe the schedule, "
               "what happened, and why.",
    },
    BTS_CAUSES: {
        "publisher": "Bureau of Transportation Statistics",
        "url": "https://www.transtats.bts.gov/ot_delay/ot_delaycause1.asp",
        "summary": "Monthly totals per carrier per arrival airport: how many flights arrived, how "
                   "many were 15+ minutes late, and how those delays split across five causes.",
        "how": "One CSV for December 2022 – December 2025. The site encodes its download query "
               "with a rotate-13 cipher, which download_data.py reproduces. December 2022 is "
               "included so January 2023 flights have a previous month to look back on.",
    },
    OPEN_METEO: {
        "publisher": "Open-Meteo historical weather archive (ERA5 reanalysis)",
        "url": "https://open-meteo.com/",
        "summary": "Daily weather at each airport's coordinates: weather code, high and low "
                   "temperature, precipitation, snowfall, and peak wind and gusts.",
        "how": "One request per airport for 2023–2025, using coordinates from OurAirports. The "
               "free tier allows about 125 airports a day, so the busiest airports were fetched "
               "first and the rest were filled in over the following days.",
    },
    DOT_ATCR: {
        "publisher": "U.S. Department of Transportation, Office of Aviation Consumer Protection",
        "url": "https://www.transportation.gov/individuals/aviation-consumer-protection/air-travel-consumer-reports",
        "summary": "Monthly report tables: on-time % by airport and scheduled hour (30 largest "
                   "airports), mishandled bags and wheelchairs per carrier, and quarterly denied "
                   "boardings per carrier.",
        "how": "36 monthly PDF reports (March 2023 – February 2026 issues, covering January 2023 – "
               "December 2025 data), saved through a browser because transportation.gov blocks "
               "scripted downloads. extract_consumer_reports.py reads the tables with pdfplumber, "
               "keeps the operating-carrier versions (marketing-carrier tables double count "
               "regional partners), and lets a later report's revised numbers win.",
    },
}

# Display order in the dashboard.
TABLES = {
    "flights": {
        "kind": "table", "source": BTS_ONTIME,
        "grain": "One scheduled flight",
        "description": "The core table: one row per domestic flight scheduled by a reporting "
                       "carrier from January 2023 to December 2025.",
        "keys": "flight_date, airline, flight_number, origin",
    },
    "flights_combined": {
        "kind": "view", "source": DERIVED,
        "grain": "One scheduled flight",
        "description": "The modeling view. Each flight joined to the airline's name, weather at both "
                       "airports on the flight date, and the previous month's delay-cause rates, "
                       "hourly on-time % and baggage rate.",
        "keys": "same as flights",
    },
    "weather_daily": {
        "kind": "table", "source": OPEN_METEO,
        "grain": "Airport × local date",
        "description": "Daily weather at each airport, in the airport's own time zone so it lines "
                       "up with flight_date.",
        "keys": "airport, date",
    },
    "delay_cause_monthly": {
        "kind": "table", "source": BTS_CAUSES,
        "grain": "Carrier × arrival airport × month",
        "description": "Raw monthly counts and minutes of arrival delay by cause, exactly as BTS "
                       "publishes them.",
        "keys": "carrier, airport, year, month",
    },
    "delay_cause_prev_month": {
        "kind": "table", "source": DERIVED,
        "grain": "Carrier × airport × month",
        "description": "delay_cause_monthly turned into rates and relabeled with the following "
                       "month, so a flight in month M joins to the stats from month M−1.",
        "keys": "carrier, airport, year, month",
    },
    "atcr_airport_ontime_by_hour": {
        "kind": "table", "source": DOT_ATCR,
        "grain": "Airport × month × scheduled hour",
        "description": "Consumer report Tables 3 and 4: the share of arrivals and departures that "
                       "were on time, for each scheduled hour, at the 30 largest airports.",
        "keys": "airport, year, month, time_block",
    },
    "atcr_ontime_by_hour_prev_month": {
        "kind": "table", "source": DERIVED,
        "grain": "Airport × month × scheduled hour",
        "description": "atcr_airport_ontime_by_hour relabeled with the following month, for the "
                       "same no-peeking reason as delay_cause_prev_month.",
        "keys": "airport, year, month, time_block",
    },
    "atcr_carrier_quality": {
        "kind": "table", "source": DOT_ATCR,
        "grain": "Carrier × month",
        "description": "Mishandled checked bags and mishandled wheelchairs and scooters per "
                       "operating carrier.",
        "keys": "carrier, year, month",
    },
    "atcr_carrier_quality_prev_month": {
        "kind": "table", "source": DERIVED,
        "grain": "Carrier × month",
        "description": "atcr_carrier_quality relabeled with the following month.",
        "keys": "carrier, year, month",
    },
    "atcr_denied_boarding": {
        "kind": "table", "source": DOT_ATCR,
        "grain": "Carrier × quarter",
        "description": "Passengers bumped from oversold flights, voluntarily and involuntarily. "
                       "Kept for reference; not joined into flights_combined.",
        "keys": "carrier, year, quarter",
    },
    "airlines": {
        "kind": "table", "source": DERIVED,
        "grain": "One carrier",
        "description": "Carrier code to carrier name, taken from the delay-cause file.",
        "keys": "carrier",
    },
    "airports": {
        "kind": "table", "source": DERIVED,
        "grain": "One airport",
        "description": "Every airport that appears in the flights, with its city and, where weather "
                       "has been downloaded, its coordinates, time zone and elevation.",
        "keys": "code",
    },
}

# Roles say whether a column could be known before the flight leaves, which is
# what decides if a model may use it.
SCHEDULE, TARGET, OUTCOME, CONTEXT = "schedule", "target", "outcome", "context"

FLIGHT_COLUMNS = {
    "flight_date": (SCHEDULE, "Scheduled date of the flight, in the origin airport's local time"),
    "year": (SCHEDULE, "Year of flight_date"),
    "month": (SCHEDULE, "Month of flight_date (1–12)"),
    "day_of_month": (SCHEDULE, "Day of the month of flight_date"),
    "day_of_week": (SCHEDULE, "Day of the week, 1 = Monday … 7 = Sunday"),
    "airline": (SCHEDULE, "Two-character code of the carrier that operated the flight"),
    "tail_number": (SCHEDULE, "Aircraft registration number"),
    "flight_number": (SCHEDULE, "Flight number assigned by the carrier"),
    "origin": (SCHEDULE, "Departure airport (IATA code)"),
    "origin_city": (SCHEDULE, "Departure city and state as BTS writes it"),
    "origin_state": (SCHEDULE, "Departure state abbreviation"),
    "dest": (SCHEDULE, "Arrival airport (IATA code)"),
    "dest_city": (SCHEDULE, "Arrival city and state as BTS writes it"),
    "dest_state": (SCHEDULE, "Arrival state abbreviation"),
    "sched_dep_time": (SCHEDULE, "Scheduled departure time, local, as hhmm (e.g. 1430 = 2:30 pm)"),
    "dep_time": (OUTCOME, "Actual gate departure time, local hhmm"),
    "dep_delay": (TARGET, "Minutes between scheduled and actual departure; negative means early. "
                          "Target for predicting how late"),
    "dep_del15": (TARGET, "1 if the flight left 15 or more minutes late, else 0. The main target. "
                          "Empty for cancelled flights"),
    "dep_time_block": (SCHEDULE, "Hour-long block containing the scheduled departure"),
    "taxi_out": (OUTCOME, "Minutes from leaving the gate to wheels off"),
    "wheels_off": (OUTCOME, "Takeoff time, local hhmm"),
    "wheels_on": (OUTCOME, "Landing time, local hhmm"),
    "taxi_in": (OUTCOME, "Minutes from wheels on to arriving at the gate"),
    "sched_arr_time": (SCHEDULE, "Scheduled arrival time, local hhmm"),
    "arr_time": (OUTCOME, "Actual gate arrival time, local hhmm"),
    "arr_delay": (OUTCOME, "Minutes late arriving; negative means early"),
    "arr_del15": (OUTCOME, "1 if the flight arrived 15 or more minutes late"),
    "cancelled": (OUTCOME, "1 if the flight was cancelled"),
    "cancellation_code": (OUTCOME, "Why it was cancelled: A carrier, B weather, C national air "
                                   "system, D security"),
    "diverted": (OUTCOME, "1 if the flight landed somewhere other than its destination"),
    "sched_elapsed_time": (SCHEDULE, "Scheduled gate-to-gate minutes"),
    "actual_elapsed_time": (OUTCOME, "Actual gate-to-gate minutes"),
    "air_time": (OUTCOME, "Minutes in the air"),
    "distance": (SCHEDULE, "Miles between the two airports"),
    "carrier_delay": (OUTCOME, "Minutes of arrival delay the airline caused (crew, maintenance, "
                               "cleaning…). Filled only when arrival delay is 15+"),
    "weather_delay": (OUTCOME, "Minutes of arrival delay from significant weather"),
    "nas_delay": (OUTCOME, "Minutes of arrival delay from the national air system (traffic "
                           "volume, air traffic control, non-extreme weather)"),
    "security_delay": (OUTCOME, "Minutes of arrival delay from security (evacuations, long lines)"),
    "late_aircraft_delay": (OUTCOME, "Minutes of arrival delay because the plane arrived late "
                                     "from its previous flight"),
}

WEATHER_COLUMNS = {
    "airport": "Airport (IATA code)",
    "date": "Local calendar date at the airport",
    "weather_code": "Most severe WMO weather code of the day: 0 clear, 1–3 cloud, 45–48 fog, "
                    "51–57 drizzle, 61–67 rain, 71–77 snow, 80–86 showers, 95–99 thunderstorm",
    "temp_max_f": "Daily high, °F",
    "temp_min_f": "Daily low, °F",
    "precip_in": "Total precipitation (rain plus melted snow), inches",
    "rain_in": "Rain, inches",
    "snowfall_in": "Snowfall, inches",
    "precip_hours": "Hours with any precipitation",
    "wind_max_mph": "Highest sustained wind at 10 m, mph",
    "gust_max_mph": "Strongest gust at 10 m, mph",
}

DELAY_CAUSE_COLUMNS = {
    "year": "Year",
    "month": "Month (1–12)",
    "carrier": "Carrier code",
    "carrier_name": "Carrier name",
    "airport": "Arrival airport (IATA code)",
    "airport_name": "Arrival airport's full name",
    "arr_flights": "Flights that arrived at this airport this month",
    "arr_del15": "Of those, how many arrived 15+ minutes late",
    "carrier_ct": "Delayed flights blamed on the carrier. Fractional: a delay with two causes "
                  "is split between them",
    "weather_ct": "Delayed flights blamed on extreme weather",
    "nas_ct": "Delayed flights blamed on the national air system",
    "security_ct": "Delayed flights blamed on security",
    "late_aircraft_ct": "Delayed flights blamed on a late-arriving aircraft",
    "arr_cancelled": "Flights cancelled",
    "arr_diverted": "Flights diverted",
    "arr_delay": "Total minutes of arrival delay",
    "carrier_delay": "Minutes of delay blamed on the carrier",
    "weather_delay": "Minutes of delay blamed on extreme weather",
    "nas_delay": "Minutes of delay blamed on the national air system",
    "security_delay": "Minutes of delay blamed on security",
    "late_aircraft_delay": "Minutes of delay blamed on a late-arriving aircraft",
}

PREV_MONTH_COLUMNS = {
    "year": "Year the stats are used FOR (one month after they happened)",
    "month": "Month the stats are used FOR (one month after they happened)",
    "carrier": "Carrier code",
    "airport": "Airport (IATA code)",
    "arr_flights": "Arriving flights in the previous month",
    "del15_rate": "Share of those flights that arrived 15+ minutes late",
    "cancel_rate": "Share cancelled",
    "avg_delay_min": "Average minutes late per delayed flight",
    "carrier_rate": "Share of flights delayed by the carrier",
    "weather_rate": "Share delayed by extreme weather",
    "nas_rate": "Share delayed by the national air system",
    "late_aircraft_rate": "Share delayed by a late-arriving aircraft",
}

HOURLY_COLUMNS = {
    "year": "Year",
    "month": "Month (1–12)",
    "airport": "Airport (IATA code); ALL is every reporting airport combined",
    "time_block": "Scheduled hour, e.g. 0600-0659. 2300-0559 is one overnight block; TOTAL is the "
                  "whole day",
    "arr_ontime_pct": "% of flights scheduled to arrive in this hour that arrived on time",
    "dep_ontime_pct": "% of flights scheduled to leave in this hour that left on time",
}

QUALITY_COLUMNS = {
    "year": "Year",
    "month": "Month (1–12)",
    "carrier": "Carrier code",
    "bags_enplaned": "Checked bags carried",
    "bags_mishandled": "Bags lost, damaged, delayed or stolen",
    "bags_mishandled_per_100": "Mishandled bags per 100 checked",
    "wheelchairs_enplaned": "Wheelchairs and scooters carried in the hold",
    "wheelchairs_mishandled": "Wheelchairs and scooters lost, damaged or delayed",
    "wheelchairs_mishandled_per_100": "Mishandled wheelchairs and scooters per 100 carried",
}

DENIED_COLUMNS = {
    "year": "Year",
    "quarter": "Quarter (1–4)",
    "carrier": "Carrier code",
    "voluntary_db": "Passengers who gave up their seat voluntarily, usually for compensation",
    "involuntary_db": "Passengers bumped against their will",
    "enplaned_passengers": "Passengers boarded",
    "involuntary_db_per_10k": "Involuntary bumps per 10,000 passengers",
}


def shift_note(columns):
    return {**columns, "year": PREV_MONTH_COLUMNS["year"], "month": PREV_MONTH_COLUMNS["month"]}


COLUMNS = {
    "flights": {c: d for c, (_, d) in FLIGHT_COLUMNS.items()},
    "weather_daily": WEATHER_COLUMNS,
    "delay_cause_monthly": DELAY_CAUSE_COLUMNS,
    "delay_cause_prev_month": PREV_MONTH_COLUMNS,
    "atcr_airport_ontime_by_hour": HOURLY_COLUMNS,
    "atcr_ontime_by_hour_prev_month": shift_note(HOURLY_COLUMNS),
    "atcr_carrier_quality": QUALITY_COLUMNS,
    "atcr_carrier_quality_prev_month": shift_note(QUALITY_COLUMNS),
    "atcr_denied_boarding": DENIED_COLUMNS,
    "airlines": {"carrier": "Carrier code", "carrier_name": "Carrier name"},
    "airports": {
        "code": "Airport (IATA code)",
        "city": "City and state as BTS writes it",
        "state": "State abbreviation",
        "latitude": "Latitude, from OurAirports",
        "longitude": "Longitude, from OurAirports",
        "timezone": "IANA time zone, reported by Open-Meteo",
        "elevation_m": "Elevation in meters, reported by Open-Meteo",
    },
}


def combined_column(name):
    """(role, description) for a flights_combined column."""
    if name in FLIGHT_COLUMNS:
        return FLIGHT_COLUMNS[name]
    if name == "airline_name":
        return CONTEXT, "Carrier name, from airlines"
    if name == "origin_prev_hour_dep_ontime_pct":
        return CONTEXT, ("Last month's on-time departure % at the origin for the scheduled "
                         "departure hour (30 largest airports only)")
    if name == "dest_prev_hour_arr_ontime_pct":
        return CONTEXT, ("Last month's on-time arrival % at the destination for the scheduled "
                         "arrival hour (30 largest airports only)")
    if name == "airline_prev_bags_mishandled_per_100":
        return CONTEXT, "Last month's mishandled bags per 100 for this carrier"
    side, _, rest = name.partition("_")
    where = "origin" if side == "origin" else "destination"
    if rest.startswith("prev_"):
        base = PREV_MONTH_COLUMNS[rest.removeprefix("prev_")]
        return CONTEXT, f"This carrier at the {where}, previous month: {base[0].lower()}{base[1:]}"
    base = WEATHER_COLUMNS[rest]
    return CONTEXT, f"Weather at the {where} on the flight date: {base[0].lower()}{base[1:]}"
