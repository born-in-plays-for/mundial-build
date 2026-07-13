#!/usr/bin/env python3
"""
fetch_population_points.py — a population-weighted point cloud used as the
KDE denominator in pipeline/kde_risk.py's "talent production" relative-risk
surface (see pipeline/README.md's "KDE talent-production surface" section).

There's no true gridded population raster (GPWv4/WorldPop) in this pipeline
— those require an EOSDIS Earthdata login or a large (100s of MB-GB) file
this pipeline has no reason to carry, and pipeline/country_registry.py's own
country.population is only ONE number per country, useless for a spatial
density surface (smearing a country's total population uniformly across its
whole territory would badly distort the ratio near real cities — see
kde_risk.py's docstring). Population-weighted points from GeoNames' public,
no-login "cities1000" dump (every populated place with population >= 1000,
CC BY 4.0 licensed — https://www.geonames.org/) stand in instead: KDE-
smoothing this point cloud with the SAME kernel/bandwidth used for player
birthplaces reconstructs a reasonable population-density surface at roughly
the 75km scale this analysis cares about (dense cities vs. empty land),
without needing a true raster.

Caveat worth keeping in mind when interpreting kde_risk.json: summing this
dataset's population column gives ~4.4B, not true world population (~8B) —
it undercounts population that isn't concentrated in a town/city of >=1000
people (dispersed rural population, informal settlements GeoNames doesn't
have a node for). That's fine for a RELATIVE risk ratio (this script's
output is only ever used as a denominator normalized against its own total
mass, not as an absolute population figure) but would matter for anything
that needed accurate absolute density.

Writes pipeline/population_points.csv (lat, lon, population), committed —
same "hits an external source, not cheap to redo casually" reasoning as
other fetch_*.py scripts, though no API key is needed here.

Usage:
    python3 pipeline/fetch_population_points.py
"""
import csv
import io
import sys
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

PIPELINE   = Path(__file__).parent
OUT_PATH   = PIPELINE / "population_points.csv"
SOURCE_URL = "https://download.geonames.org/export/dump/cities1000.zip"
USER_AGENT = "mundial-build-population-fetch/1.0 (https://github.com/born-in-plays-for/mundial-build)"

# GeoNames' cities1000.txt column layout (tab-separated, no header) —
# https://download.geonames.org/export/dump/readme.txt
LAT_COL, LON_COL, POP_COL = 4, 5, 14


def main():
    req = Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=60) as resp:
        raw = resp.read()

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".txt"))
        text = zf.read(name).decode("utf-8")

    rows = []
    for line in text.splitlines():
        fields = line.split("\t")
        pop = int(fields[POP_COL]) if fields[POP_COL] else 0
        if pop <= 0:
            continue
        rows.append((float(fields[LAT_COL]), float(fields[LON_COL]), pop))

    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["lat", "lon", "population"])
        w.writerows(rows)

    total_pop = sum(r[2] for r in rows)
    print(f"Wrote {OUT_PATH}")
    print(f"  {len(rows)} populated places (population >= 1000), "
          f"{total_pop:,} total (proxy, not true world population)")


if __name__ == "__main__":
    main()
