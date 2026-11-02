"""Pull tables out of the DOT Air Travel Consumer Report PDFs into CSVs.

    python extract_consumer_reports.py

Reads raw/consumer_reports/ATCR_<report month>.pdf (downloaded through a browser,
since transportation.gov blocks scripted downloads) and writes to
raw/consumer_reports/extracted/:

  airport_ontime_by_hour.csv     Tables 3 and 4: % of arrivals / departures on time
                                 by airport and scheduled hour, 30 largest airports
  carrier_quality_monthly.csv    mishandled baggage and wheelchairs per operating carrier
  denied_boarding_quarterly.csv  voluntary and involuntary denied boardings per operating carrier

Each report covers data from about two months before it was published, so every
row is keyed by the data month printed in the table, not the report month. When
two reports cover the same period, the later (possibly revised) one wins.
"""
import re
import sys

import pandas as pd
import pdfplumber

from paths import RAW_DIR

REPORT_DIR = RAW_DIR / "consumer_reports"
OUT_DIR = REPORT_DIR / "extracted"

MONTHS = {m: i for i, m in enumerate(
    ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
     "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"], start=1)}

# Operating carrier names as printed in the reports -> BTS carrier codes used in the flights table.
CARRIER_CODES = {
    "ALASKA AIRLINES": "AS",
    "ALLEGIANT AIR": "G4",
    "AMERICAN AIRLINES": "AA",
    "DELTA AIR LINES": "DL",
    "ENDEAVOR AIR": "9E",
    "ENVOY AIR": "MQ",
    "FRONTIER AIRLINES": "F9",
    "HAWAIIAN AIRLINES": "HA",
    "HORIZON AIR": "QX",
    "JETBLUE AIRWAYS": "B6",
    "MESA AIRLINES": "YV",
    "PSA AIRLINES": "OH",
    "REPUBLIC AIRWAYS": "YX",
    "REPUBLIC AIRLINE": "YX",
    "SKYWEST AIRLINES": "OO",
    "SOUTHWEST AIRLINES": "WN",
    "SPIRIT AIRLINES": "NK",
    "SPIRIT AIR LINES": "NK",
    "UNITED AIRLINES": "UA",
    "UNITED AIR LINES": "UA",
}

NUM = r"[\d,]+(?:\.\d+)?"
PERIOD = re.compile(r"([A-Za-z]+)\s+(\d{4})")
QUARTER = re.compile(r"([A-Za-z]+)\s*-\s*([A-Za-z]+)\s+(\d{4})")
TIME_ROW = re.compile(r"^(\d{4}-\d{4}|TOTAL)\s+(.*)$")
RANK_ROW = re.compile(rf"^\d+\s+([A-Z][A-Z .&'/-]+?)\d*\s+((?:{NUM}\s*)+)")
HEADER_WORDS = {"ENPLANED"}  # column headers that wrap onto a line starting with "100"


def number(text):
    return float(text.replace(",", ""))


def data_month(line):
    month, year = PERIOD.search(line).groups()
    return int(year), MONTHS[month.upper()]


def carrier_rows(lines, n_values):
    """(code, first n numbers) for each ranked carrier row; the first n are the current period."""
    for i, line in enumerate(lines):
        m = RANK_ROW.match(line)
        if not m:
            continue
        name = m.group(1).strip()
        values = m.group(2).split()
        if name in HEADER_WORDS:
            continue
        if name not in CARRIER_CODES and i > 0 and lines[i - 1].isalpha():
            name = f"{lines[i - 1]} {name}"  # long names wrap: "SOUTHWEST" / "9 AIRLINES ..."
        if name not in CARRIER_CODES:
            raise ValueError(f"unknown carrier {name!r}; add it to CARRIER_CODES")
        if len(values) < n_values:
            raise ValueError(f"short row: {line!r}")
        yield CARRIER_CODES[name], [number(v) for v in values[:n_values]]


def parse_hourly(lines, report):
    """Table 3 or 4 page -> rows of (airport, time_block, pct). The all-airport column is 'ALL'."""
    header = next(l.split()[1:] for l in lines
                  if l.split()[0] in ("ARRIVAL", "DEPARTURE")
                  and all(re.fullmatch(r"[A-Z]{3}|TOTAL", t) for t in l.split()[1:]))
    header = ["ALL" if code == "TOTAL" else code for code in header]
    for line in lines:
        m = TIME_ROW.match(line)
        if not m:
            continue
        values = m.group(2).split()
        if len(values) != len(header):
            raise ValueError(f"{report}: {len(values)} values for {len(header)} airports: {line!r}")
        for airport, value in zip(header, values):
            yield airport, m.group(1), None if value in ("-", "N/A") else float(value)


def extract(path):
    hourly, baggage, wheelchairs, denied = [], [], [], []
    report = path.stem.removeprefix("ATCR_")
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            lines = [l.strip() for l in (page.extract_text() or "").splitlines() if l.strip()]
            title_idx = next((i for i, l in enumerate(lines[:5])
                              if re.search(r"TABLE [34]\. PERCENTAGE|RANKING OF|DENIED BOARDING BY", l)), None)
            if title_idx is None:
                continue
            title = re.sub(r"\s+", " ", lines[title_idx])
            period = lines[title_idx + 1]
            if "OPERATING" not in title and "OPEARTING" not in title:
                continue  # marketing-carrier versions double count regional partners

            if title.startswith(("TABLE 3.", "TABLE 4.")):
                year, month = data_month(period)
                kind = "arr_ontime_pct" if title.startswith("TABLE 3.") else "dep_ontime_pct"
                for airport, block, pct in parse_hourly(lines, report):
                    hourly.append({"year": year, "month": month, "airport": airport,
                                   "time_block": block, "kind": kind, "pct": pct, "report": report})
            elif "(MONTHLY" in title and "MISHANDLED" in title:
                year, month = data_month(period)
                target = baggage if "BAGGAGE" in title else wheelchairs
                for code, (enplaned, mishandled, per_100) in carrier_rows(lines, 3):
                    target.append({"year": year, "month": month, "carrier": code, "enplaned": enplaned,
                                   "mishandled": mishandled, "per_100": per_100, "report": report})
            elif "DENIED BOARDING" in title and "YTD" not in title and "MONTHLY" not in title:
                q = QUARTER.search(period)
                if not q:
                    continue
                start, end, year = q.groups()
                quarter = (MONTHS[end.upper()] - 1) // 3 + 1
                for code, (voluntary, involuntary, enplaned, per_10k) in carrier_rows(lines, 4):
                    denied.append({"year": int(year), "quarter": quarter, "carrier": code,
                                   "voluntary_db": voluntary, "involuntary_db": involuntary,
                                   "enplaned_passengers": enplaned, "involuntary_db_per_10k": per_10k,
                                   "report": report})
    return hourly, baggage, wheelchairs, denied


def latest(df, keys):
    """Keep the row from the most recent report for each key."""
    return df.sort_values("report").drop_duplicates(keys, keep="last")


def main():
    OUT_DIR.mkdir(exist_ok=True)
    parts = [[], [], [], []]
    reports = sorted(REPORT_DIR.glob("ATCR_*.pdf"))
    for path in reports:
        for collected, new in zip(parts, extract(path)):
            collected.extend(new)
        print(f"  {path.name}: " + ", ".join(str(len(p)) for p in parts))
    hourly, baggage, wheelchairs, denied = (pd.DataFrame(p) for p in parts)

    hourly = (latest(hourly, ["year", "month", "airport", "time_block", "kind"])
              .pivot_table(index=["year", "month", "airport", "time_block"], columns="kind",
                           values="pct", dropna=False)
              .reset_index())
    hourly.columns.name = None
    hourly.to_csv(OUT_DIR / "airport_ontime_by_hour.csv", index=False)

    keys = ["year", "month", "carrier"]
    quality = latest(baggage, keys).drop(columns="report").rename(columns={
        "enplaned": "bags_enplaned", "mishandled": "bags_mishandled", "per_100": "bags_mishandled_per_100"})
    chairs = latest(wheelchairs, keys).drop(columns="report").rename(columns={
        "enplaned": "wheelchairs_enplaned", "mishandled": "wheelchairs_mishandled",
        "per_100": "wheelchairs_mishandled_per_100"})
    quality = quality.merge(chairs, on=keys, how="outer").sort_values(keys)
    quality.to_csv(OUT_DIR / "carrier_quality_monthly.csv", index=False)

    denied = latest(denied, ["year", "quarter", "carrier"]).drop(columns="report").sort_values(
        ["year", "quarter", "carrier"])
    denied.to_csv(OUT_DIR / "denied_boarding_quarterly.csv", index=False)

    print(f"{len(reports)} reports -> {len(hourly):,} airport-hour rows, "
          f"{len(quality):,} carrier-months, {len(denied):,} carrier-quarters")


if __name__ == "__main__":
    sys.exit(main())
