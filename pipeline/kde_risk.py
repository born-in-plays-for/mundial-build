#!/usr/bin/env python3
"""
kde_risk.py — "talent production" relative-risk surface: where WC2026
players/coaches were born, per person actually living nearby, as a smooth
2D map layer (mundial's own build step; consumed by a separate `mundial`
frontend session — see KDE-prompt.md's Prompt 2 for that handoff).

Two Gaussian KDE surfaces on the SAME 0.25°, world-extent grid and kernel
bandwidth (so they're directly comparable cell-by-cell):

  1. Player density  — over pipeline/mundial.db's geocoded birth cities
     (person.birth_city -> city.lat/lon), each unique city weighted by how
     many WC2026 players/coaches were born there.
  2. Population density — over pipeline/population_points.csv (see
     fetch_population_points.py), a population-weighted point cloud
     standing in for a true gridded population raster (GPWv4/WorldPop
     would need an EOSDIS login or a multi-hundred-MB download this
     pipeline has no other use for; country.population is only ONE number
     per country, useless for a spatial surface — see
     fetch_population_points.py's docstring for the full reasoning).

Both use the same haversine-distance Gaussian kernel: a point of weight w
at distance d (km) contributes w * (1 / (2*pi*h^2)) * exp(-d^2 / (2*h^2))
to a cell, h = --bandwidth-km. That kernel integrates to 1 over the plane,
so the resulting surface has genuine "weight per km^2" units — population
density in real people/km^2, in particular, which is what makes
--pop-threshold's default (1 person/km^2) a real-world-meaningful cutoff
for "effectively uninhabited" (ocean, ice, deep desert) rather than an
arbitrary tuning knob.

Relative risk, not raw density: risk(cell) = (player_density(cell) /
population_density(cell)) / (total_players / total_population) — normalized
against the dataset's own global player-per-population rate, so risk = 1
(log2 = 0) means "this location produces WC2026 talent exactly proportional
to how many people live there", not some tiny uninterpretable absolute
players-per-person fraction. log2 makes the scale symmetric: +1 = double
the expected rate, -1 = half.

This deliberately answers a DIFFERENT question than "which city has
produced the most famous players" — a raw density map would answer that,
and would be dominated by megacities almost by construction (more people,
more players, mechanically). Verified empirically: São Paulo (~12.4M people
within one GeoNames point, genuinely one of the densest urban regions on
Earth) comes out at 0.63x, essentially proportional-to-population despite
having several notable WC2026 players — that's not a bug, it's the surface
correctly distinguishing "produces a lot of talent because it has a LOT of
people" from "produces disproportionately more talent than its population
would predict". Paris (~9x) and Buenos Aires (~6x) DO come out strongly
positive; Doha (~13x) is elevated but not the top hotspot, arguably a
legitimate finding given Qatar's well-documented, heavily-funded football
academy system relative to its small population — not a smoothing failure
to suppress.

Masking: population_density(cell) < --pop-threshold (default 1 person/km^2)
gets risk = null instead of a division blowing up near-empty land into an
absurd ratio. The Gaussian kernel's window cutoff (4 sigma) already makes
population_density EXACTLY 0 more than ~4*bandwidth from every point in
population_points.csv (open ocean, ice sheets), so most masking is already
"free" before --pop-threshold's real-world figure trims the sparse-but-
nonzero remainder (deep desert, high mountains).

Hotspots: local maxima of the risk grid (scipy.ndimage.maximum_filter,
footprint ~2*bandwidth so nearby maxima don't duplicate the same cluster),
taken in descending log2_risk order and snapped to the nearest ACTUAL
WC2026 birth city in pipeline/mundial.db (not a population_points.csv
point) — deduplicated by city, so two maxima near the same city only
produce one hotspot entry.

Outputs (written straight to data/, no load.py/export.py step — this isn't
pid-keyed relational data):
  data/kde_risk.json — {bandwidthKm, resolutionDeg, bbox, nx, ny, source,
    values}. values is row-major, ny rows of nx values each; row i is
    latitude bbox[1] + (i+0.5)*resolutionDeg (south to north), column j
    within a row is longitude bbox[0] + (j+0.5)*resolutionDeg (west to
    east) — i.e. row/column INDEX order, not raw lon/lat, to keep the file
    a flat number array. null = masked.
  data/hotspots.json — [{name, country, lon, lat, players, log2Risk}, ...]
    sorted by log2Risk descending.

Usage:
    python3 pipeline/kde_risk.py
    python3 pipeline/kde_risk.py --bandwidth-km 100 --resolution-deg 0.5 --top-n 40
"""
import argparse
import csv
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
from scipy.ndimage import maximum_filter

PIPELINE  = Path(__file__).parent
DB_PATH   = PIPELINE / "mundial.db"
POP_PATH  = PIPELINE / "population_points.csv"
OUT_DIR   = PIPELINE.parent / "data"

EARTH_RADIUS_KM = 6371.0
KM_PER_DEG_LAT  = 111.32  # ~constant; longitude's km/degree shrinks by cos(lat)


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def kde_grid(points, lat_centers, lon_centers, bandwidth_km, cutoff_sigmas=4.0):
    """points: list of (lat, lon, weight). -> (ny, nx) float64 array, units
    "weight per km^2" (see module docstring's kernel normalization note).
    Windowed per point (only the cells within cutoff_sigmas*bandwidth_km
    are ever touched) — the alternative, a dense n_points x n_cells
    distance matrix, would be hundreds of billions of entries for the
    population point cloud."""
    ny, nx = len(lat_centers), len(lon_centers)
    grid = np.zeros((ny, nx))
    cutoff_km = cutoff_sigmas * bandwidth_km
    norm = 1.0 / (2 * np.pi * bandwidth_km ** 2)
    two_h2 = 2 * bandwidth_km ** 2
    lat_step = lat_centers[1] - lat_centers[0]
    lon_step = lon_centers[1] - lon_centers[0]

    for plat, plon, w in points:
        dlat_deg = cutoff_km / KM_PER_DEG_LAT
        i_lo = max(int(np.searchsorted(lat_centers, plat - dlat_deg)), 0)
        i_hi = min(int(np.searchsorted(lat_centers, plat + dlat_deg)), ny)
        if i_lo >= i_hi:
            continue

        coslat = max(np.cos(np.radians(plat)), 1e-6)
        dlon_deg = min(cutoff_km / (KM_PER_DEG_LAT * coslat), 180.0)
        # Capped at (nx-1)//2 so 2*j_span+1 <= nx: near the poles cos(lat)
        # shrinks toward 0 and the raw window can exceed a full 360° wrap,
        # which would make j_idx contain duplicate column indices — and
        # `grid[..., j_idx] += contrib` with duplicates silently keeps only
        # the LAST write per duplicate (fancy-index += doesn't accumulate),
        # not an error, just quietly wrong. No WC2026 birth city or
        # population point is anywhere near a pole, so this cap is never
        # actually hit in practice — it's a correctness guarantee, not a
        # real trade-off.
        j_span = min(int(np.ceil(dlon_deg / lon_step)) + 1, (nx - 1) // 2)
        j_center = int(round((plon - lon_centers[0]) / lon_step))
        j_idx = np.arange(j_center - j_span, j_center + j_span + 1) % nx

        sub_lat = lat_centers[i_lo:i_hi]
        sub_lon = lon_centers[j_idx]
        lat_mesh, lon_mesh = np.meshgrid(sub_lat, sub_lon, indexing="ij")
        d = haversine_km(plat, plon, lat_mesh, lon_mesh)
        contrib = w * norm * np.exp(-d ** 2 / two_h2)
        contrib[d > cutoff_km] = 0.0

        # j_idx may contain duplicate indices only if j_span >= nx/2, which
        # never happens at realistic bandwidths — safe to index-assign directly.
        grid[i_lo:i_hi, j_idx] += contrib

    return grid


def load_player_points(bandwidth_km):
    db = sqlite3.connect(DB_PATH)
    rows = db.execute("""
        SELECT c.name, co.name, c.lat, c.lon, COUNT(p.pid) AS players
        FROM city c
        JOIN country co ON co.id = c.country
        JOIN person p   ON p.birth_city = c.id
        WHERE c.lat IS NOT NULL
        GROUP BY c.id
    """).fetchall()
    db.close()
    cities = [{"name": name, "country": country, "lat": lat, "lon": lon, "players": n}
               for name, country, lat, lon, n in rows]
    points = [(c["lat"], c["lon"], c["players"]) for c in cities]
    return cities, points


def load_population_points():
    with open(POP_PATH, encoding="utf-8") as f:
        return [(float(r["lat"]), float(r["lon"]), float(r["population"]))
                for r in csv.DictReader(f)]


def find_hotspots(log2_risk, mask, lat_centers, lon_centers, cities, bandwidth_km, top_n):
    footprint_cells = max(int(round(2 * bandwidth_km / KM_PER_DEG_LAT / (lat_centers[1] - lat_centers[0]))), 1)
    masked = np.where(mask, log2_risk, -np.inf)
    local_max = maximum_filter(masked, size=2 * footprint_cells + 1, mode="nearest")
    is_peak = (masked == local_max) & mask & np.isfinite(masked)

    candidates = [(masked[i, j], lat_centers[i], lon_centers[j])
                  for i, j in zip(*np.where(is_peak))]
    candidates.sort(key=lambda c: -c[0])

    city_lat = np.array([c["lat"] for c in cities])
    city_lon = np.array([c["lon"] for c in cities])

    hotspots, used = [], set()
    for risk, glat, glon in candidates:
        if len(hotspots) >= top_n:
            break
        d = haversine_km(glat, glon, city_lat, city_lon)
        nearest = int(np.argmin(d))
        if nearest in used:
            continue
        used.add(nearest)
        c = cities[nearest]
        hotspots.append({"name": c["name"], "country": c["country"],
                          "lon": c["lon"], "lat": c["lat"],
                          "players": c["players"], "log2Risk": round(float(risk), 4)})
    return hotspots


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bandwidth-km", type=float, default=75.0)
    parser.add_argument("--resolution-deg", type=float, default=0.25)
    parser.add_argument("--pop-threshold", type=float, default=1.0,
                        help="mask cells below this population density (people/km^2)")
    parser.add_argument("--smoothing-pop", type=float, default=50.0,
                        help="pseudo-population density (people/km^2) blended into every "
                             "cell before taking the ratio — see main()'s comment")
    parser.add_argument("--top-n", type=int, default=25)
    args = parser.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"FATAL: {DB_PATH} not found — run pipeline/load.py first")
    if not POP_PATH.exists():
        sys.exit(f"FATAL: {POP_PATH} not found — run pipeline/fetch_population_points.py first")

    t0 = time.time()
    lon_centers = np.arange(-180 + args.resolution_deg / 2, 180, args.resolution_deg)
    lat_centers = np.arange(-90 + args.resolution_deg / 2, 90, args.resolution_deg)
    ny, nx = len(lat_centers), len(lon_centers)

    cities, player_points = load_player_points(args.bandwidth_km)
    pop_points = load_population_points()
    total_players = sum(p[2] for p in player_points)
    total_pop = sum(p[2] for p in pop_points)
    print(f"{len(cities)} birth cities ({total_players} players/coaches), "
          f"{len(pop_points)} population points ({total_pop:,.0f} proxy total)")

    player_density = kde_grid(player_points, lat_centers, lon_centers, args.bandwidth_km)
    print(f"  player density grid done ({time.time() - t0:.1f}s)")
    pop_density = kde_grid(pop_points, lat_centers, lon_centers, args.bandwidth_km)
    print(f"  population density grid done ({time.time() - t0:.1f}s)")

    global_rate = total_players / total_pop
    mask = pop_density >= args.pop_threshold

    # Raw player_density/(pop_density*global_rate) is a rate ratio, and rate
    # ratios computed from small counts are inherently noisy (the same
    # "small-area estimation" problem epidemiology deals with for rare-event
    # maps): a single player born in a sparsely-populated town can produce
    # an enormous, meaningless ratio purely because the LOCAL population is
    # tiny, not because the location is a genuine talent hotspot — this is
    # the same failure mode the "Doha shouldn't dominate" sanity check
    # guards against, just triggered by the denominator being small instead
    # of large. Confirmed empirically: without smoothing, the top of the
    # list was small Arctic Norwegian towns with 1-2 players each, not
    # Paris/São Paulo/Buenos Aires.
    #
    # Standard fix: blend in a pseudo-population (--smoothing-pop, people/
    # km^2) at the GLOBAL rate before taking the ratio — equivalent to a
    # Bayesian prior centered on "produces talent exactly proportional to
    # population". A cell whose real population_density is small compared
    # to --smoothing-pop gets pulled toward risk=1 (no evidence either way
    # yet); a cell whose population_density is large compared to it (real
    # cities) is barely affected, since the added mass is negligible next
    # to their own.
    S = args.smoothing_pop
    with np.errstate(divide="ignore", invalid="ignore"):
        risk = (player_density / global_rate + S) / (pop_density + S)
        log2_risk = np.log2(risk)
    log2_risk[~mask] = np.nan

    hotspots = find_hotspots(log2_risk, mask, lat_centers, lon_centers, cities,
                              args.bandwidth_km, args.top_n)

    print(f"\nTop {min(10, len(hotspots))} hotspots:")
    for h in hotspots[:10]:
        factor = 2 ** h["log2Risk"]
        print(f"  {h['log2Risk']:+6.2f} ({factor:5.1f}x)  {h['name']}, {h['country']}"
              f"  ({h['players']} players)")

    # Reference-city sanity check (see module docstring's "Relative risk"
    # section for why São Paulo is expected near/below 1x, not "strongly
    # positive" — a megacity's raw player count isn't automatically a
    # population-relative outlier).
    print("\nReference cities:")
    for name, lat, lon in [("Paris", 48.8566, 2.3522), ("São Paulo", -23.5505, -46.6333),
                            ("Buenos Aires", -34.6037, -58.3816), ("Doha", 25.2854, 51.5310)]:
        j = int(round((lon - lon_centers[0]) / args.resolution_deg))
        i = int(round((lat - lat_centers[0]) / args.resolution_deg))
        v = log2_risk[i, j]
        status = "masked" if np.isnan(v) else f"{v:+.2f} ({2 ** v:.2f}x)"
        print(f"  {name:15s} {status}")

    values = [None if np.isnan(v) else round(float(v), 3) for v in log2_risk.ravel()]
    kde_out = {
        "bandwidthKm": args.bandwidth_km,
        "resolutionDeg": args.resolution_deg,
        "bbox": [-180, -90, 180, 90],
        "nx": nx, "ny": ny,
        "source": "player birthplaces: pipeline/mundial.db; population proxy: GeoNames "
                  "cities1000 (CC BY 4.0) via pipeline/fetch_population_points.py",
        "values": values,
    }

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "kde_risk.json").write_text(
        json.dumps(kde_out, separators=(",", ":")), encoding="utf-8")
    (OUT_DIR / "hotspots.json").write_text(
        json.dumps(hotspots, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    unmasked = int(mask.sum())
    kde_bytes = (OUT_DIR / "kde_risk.json").stat().st_size
    print(f"\nWrote {OUT_DIR / 'kde_risk.json'} ({kde_bytes:,} bytes, "
          f"{nx}x{ny} grid, {unmasked:,}/{nx*ny:,} cells unmasked)")
    print(f"Wrote {OUT_DIR / 'hotspots.json'} ({len(hotspots)} hotspots)")
    print(f"Total time: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
