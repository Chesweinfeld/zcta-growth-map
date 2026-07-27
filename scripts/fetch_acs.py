"""Fetch ACS 5-year population + housing-unit counts by ZCTA for two vintages.

Earliest ACS 5-year release that supports ZCTA geography is 2011 (2007-2011).
Latest available is 2024 (2020-2024). That is the widest window the ACS offers.

Writes data/raw/acs_<year>.csv with columns: zcta, pop, housing_units.
"""

import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

YEARS = [2011, 2024]
VARS = {"B01003_001E": "pop", "B25001_001E": "housing_units"}
BASE = "https://api.census.gov/data/{year}/acs/acs5"


def api_key() -> str:
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("CENSUS_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("Set CENSUS_API_KEY (env var or .env file). Free key: "
                 "https://api.census.gov/data/key_signup.html")
    return key


def get(year: int, var: str, key: str, attempts: int = 5) -> pd.DataFrame:
    """One variable for all ZCTAs. The API throttles bursts with a bogus
    'Invalid Key' HTML page, so retry on anything that isn't JSON."""
    params = {"get": var, "for": "zip code tabulation area:*", "key": key}
    for i in range(attempts):
        r = requests.get(BASE.format(year=year), params=params, timeout=120)
        try:
            rows = r.json()
        except ValueError:
            wait = 5 * (i + 1)
            print(f"  {year} {var}: throttled, retrying in {wait}s", flush=True)
            time.sleep(wait)
            continue
        df = pd.DataFrame(rows[1:], columns=rows[0])
        zcol = "zip code tabulation area"
        # 2011/2012 return ZCTA-within-state rows; a ZCTA that straddles a state
        # line appears once per state with the SAME nationwide total, so dedupe.
        df = df[[var, zcol]].rename(columns={var: VARS[var], zcol: "zcta"})
        df[VARS[var]] = pd.to_numeric(df[VARS[var]], errors="coerce")
        df = df.drop_duplicates(subset="zcta", keep="first")
        return df
    raise RuntimeError(f"{year} {var}: API kept failing after {attempts} tries")


def main() -> None:
    key = api_key()
    RAW.mkdir(parents=True, exist_ok=True)
    for year in YEARS:
        out = RAW / f"acs_{year}.csv"
        if out.exists():
            print(f"{out.name} exists, skipping")
            continue
        parts = []
        for var in VARS:
            print(f"fetching {year} {var} ({VARS[var]})...", flush=True)
            parts.append(get(year, var, key).set_index("zcta"))
            time.sleep(2)
        df = pd.concat(parts, axis=1).reset_index()
        df.to_csv(out, index=False)
        print(f"  wrote {out} ({len(df):,} ZCTAs)")


if __name__ == "__main__":
    main()
