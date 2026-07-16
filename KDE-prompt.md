Prompt 1 is **done** — `mundial-build`'s `pipeline/kde_risk.py` (+
`pipeline/fetch_population_points.py`) built the real `data/kde_risk.json`
and `data/hotspots.json`, already committed and pushed to the `mundial-data`
submodule. Its original text is kept below for context, but there's no need
to re-run it — see `pipeline/README.md`'s "KDE talent-production surface"
section for the full methodology (including why São Paulo intentionally
comes out near/below the global rate — a verified, deliberate property of
population normalization, not a bug the `mundial` session should "fix" by
second-guessing the data).

Prompt 2's "Inputs" section below has been **corrected against the actual
shipped files** — the original speculative contract had a field-name
mismatch (`log2_risk` vs. the real `log2Risk`) that would have silently
broken against real data. Paste Prompt 2 as-is into a `mundial` session now.

**Prompt 2 — for `mundial`:**

```
Add a new map layer to the existing World Cup birthplace map: a KDE
relative-risk surface as the base layer, with top-N hotspot cities overlaid as
small labeled dots. Explore the repo first and reuse the existing projection,
map component, and styling conventions.

Inputs (already built and committed to the mundial-data submodule — treat as
a fixed, verified contract, not a guess):
- data/kde_risk.json: { bandwidthKm, resolutionDeg, bbox: [-180,-90,180,90],
  nx: 1440, ny: 720, source, values }. values is a FLAT row-major array of
  nx*ny numbers (or null for masked cells) — row i (0..ny-1) is latitude
  bbox[1] + (i+0.5)*resolutionDeg, SOUTH TO NORTH; within a row, column j
  (0..nx-1) is longitude bbox[0] + (j+0.5)*resolutionDeg, WEST TO EAST. So
  values[i*nx + j] is the cell at that (lat, lon). Each value is
  log2(relative risk) — relative to the dataset's own GLOBAL player-per-
  population rate, not raw player_density/population_density — so 0 means
  "produces WC2026 talent exactly proportional to local population", +1 =
  2x the expected rate, -1 = half. null = masked (population too sparse
  nearby to be meaningful). JSON only, ~5.5MB uncompressed / ~390KB
  gzipped — no PNG sidecar exists, don't build a code path expecting one.
- data/hotspots.json: [ { name, country, lon, lat, players, log2Risk } ]
  (camelCase log2Risk, not log2_risk), 25 entries, sorted by log2Risk
  descending. country matches the birth-country naming already used
  elsewhere in this dataset (e.g. "Scotland" for a UK home nation, not
  "United Kingdom") — reuse whatever country-name/flag handling the
  existing map already has for that, don't reimplement it.

Rendering requirements:
1. Base layer: render the grid as a smooth raster (offscreen canvas reprojected
   to the map projection, or contour bands via d3-contour if that fits the stack
   better). Diverging color scale centered on log2Risk = 0: muted cool color
   below 0 (underproduces relative to population), warm/purple above. Masked
   cells fully transparent so the basemap shows through. No country fills under
   this layer — remove/disable the existing choropleth when this mode is active.
2. Overlay: hotspot dots, small fixed radius (not scaled by value), subtle halo,
   label = city name. Declutter labels (hide on collision, priority by
   log2Risk). Show players count + log2Risk in a tooltip.
3. UI: a toggle between the existing birth-city bubble view and this new
   "production intensity" view. Add a compact legend explaining the scale in
   plain language ("×2 more players than population predicts", "×4", ...) —
   convert log2 values to ×-factors for humans (2**log2Risk).
4. Keep it performant: the grid renders once per projection change, not per
   frame.
```

---

<details>
<summary>Original Prompt 1 (for reference only — already executed)</summary>

```
Add a build step that computes a "talent production" KDE relative-risk surface
from the World Cup players/coaches birthplace dataset, plus a top-N hotspot list.
Explore the repo first to find the dataset and follow existing build conventions.

What to compute:
1. Player intensity: 2D kernel density estimate over the ~1296 birth coordinates
   (Gaussian kernel, bandwidth ~75 km; make it a parameter). Compute on a regular
   lon/lat grid, ~0.25° resolution, world extent.
2. Population denominator: KDE-smooth a gridded world population dataset with the
   SAME kernel and grid. Use a public gridded product (GPWv4 or WorldPop
   downsampled); document the source. Total population is an acceptable proxy for
   birth cohort.
3. Ratio surface: risk = player_density / population_density. Mask cells where
   smoothed population density is below a threshold (parameter) to avoid absurd
   ratios in deserts/oceans — masked cells get null. Store log2(risk) so the
   scale is symmetric around 0 (= produces exactly proportionally to population).
4. Top-N hotspots (N=25, parameter): local maxima of the ratio surface, each
   snapped to the nearest actual birth city in the dataset, with player count.

Output contract (consumed by the 'mundial' front-end project):
- kde_risk.json: { "bbox": [-180,-90,180,90], "nx": ..., "ny": ...,
  "values": [...] }  — row-major log2-risk grid, null for masked cells.
  If the grid is large, also emit kde_risk.png (grayscale-encoded) with the same
  metadata in a sidecar JSON; keep both paths documented.
- hotspots.json: [ { "name", "country", "lon", "lat", "players", "log2_risk" } ]
  sorted by log2_risk descending.

Use Python (scipy/numpy; sklearn KernelDensity with haversine metric if
distortion at high latitudes matters). Add a CLI entry point consistent with the
existing build pipeline, and a short README section explaining parameters and
the masking rationale. Sanity check: Paris/São Paulo/Buenos Aires regions should
be strongly positive; Doha should NOT dominate after population normalization —
print the top 10 to the console for verification.
```

Actual implementation deviated from this text in a few places worth knowing
about if you're comparing against it: used a bounding-box/pseudo-population
shrinkage (`--smoothing-pop`) not specified here, since the raw ratio (as
literally speced) was dominated by 1-2-player Arctic towns rather than any
recognizable hotspot — see `pipeline/README.md` for why. Also skipped
`sklearn` (not needed — hand-rolled windowed haversine KDE in numpy/scipy)
and the PNG fallback (JSON alone was small enough gzipped).

</details>