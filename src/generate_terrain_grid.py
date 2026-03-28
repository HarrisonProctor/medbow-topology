#!/usr/bin/env python3
"""
Snowy Range Terrain Grid Generator (500 m cells)
=================================================
Generates a CSV of terrain grid cells for avalanche risk assessment.
Produces a clean mountain-only footprint suitable for choropleth rendering
in Palantir Foundry — no plains, no valley floors, no visual noise.

Pipeline:
  1. Load USGS 1/3 arc-second DEM tiles and mosaic.
  2. Lay a 500 m grid; for each cell compute elevation, slope (MAX), aspect.
  3. Reject cells whose center falls outside the user-drawn boundary polygon.
  4. Reject cells below the elevation cutoff (default 7 800 ft).
  5. Remove small disconnected clusters (< 5 cells) for clean edges.
  6. Assign avalanche risk scores and Foundry-ready geometries.

Dependencies:
  pip install rasterio numpy pandas shapely

Usage:
  python generate_terrain_grid.py \\
      --dem data/dem/USGS_13_n42w107*.tif \\
      --output data/snowy_range_terrain_grid.csv
"""

import argparse
import glob
import json
import os
import sys

import numpy as np
import pandas as pd
import rasterio
from rasterio.merge import merge
from shapely.geometry import Point, Polygon, shape

# ── Boundary polygon (user-drawn, clamped to 41.00°N on the south) ──────────
# Vertices are (lat, lon); we clamp any lat < 41.00 to 41.00.
_RAW_BOUNDARY = [
    (41.0068, -106.4072),
    (41.2452, -106.6315),
    (41.5883, -106.6498),
    (41.6321, -106.2662),
    (41.4904, -106.0126),
    (41.2631, -105.9302),
    (41.0668, -105.9476),
    (40.9985, -106.0135),
]

WY_CO_BORDER = 41.00  # hard southern limit

BOUNDARY_POLY = Polygon([
    (lon, max(lat, WY_CO_BORDER))          # shapely uses (x, y) = (lon, lat)
    for lat, lon in _RAW_BOUNDARY
])

# ── Tunable constants ────────────────────────────────────────────────────────
CELL_SIZE_M = 500
ELEV_CUTOFF_FT = 7_800          # minimum elevation to keep
MIN_CLUSTER_SIZE = 5             # drop clusters smaller than this
NORTH_ASPECTS = {"N", "NE", "NW"}

# ── Elevation band thresholds (feet) ────────────────────────────────────────
TREELINE_LOW = 9_500
TREELINE_HIGH = 11_000

M_TO_FT = 3.28084


# ─────────────────────────────────────────────────────────────────────────────
# DEM I/O
# ─────────────────────────────────────────────────────────────────────────────
def load_dem(dem_paths: list[str]):
    """Load one or more USGS GeoTIFF DEMs and return a merged array."""
    if len(dem_paths) == 1:
        src = rasterio.open(dem_paths[0])
        dem = src.read(1)
        transform = src.transform
        crs = src.crs
        nodata = src.nodata
        src.close()
    else:
        datasets = [rasterio.open(p) for p in dem_paths]
        dem, transform = merge(datasets)
        dem = dem[0]                        # merge returns (bands, rows, cols)
        crs = datasets[0].crs
        nodata = datasets[0].nodata
        for ds in datasets:
            ds.close()
    return dem, transform, crs, nodata


# ─────────────────────────────────────────────────────────────────────────────
# Terrain derivatives
# ─────────────────────────────────────────────────────────────────────────────
def compute_slope_aspect(dem, transform, nodata):
    """Return (slope_degrees, aspect_degrees) arrays from the DEM."""
    z = dem.astype(np.float64)
    if nodata is not None:
        z[dem == nodata] = np.nan

    # Ground distances per pixel (metres)
    lat_center = (BOUNDARY_POLY.bounds[1] + BOUNDARY_POLY.bounds[3]) / 2
    dx = abs(transform[0]) * 111_320 * np.cos(np.radians(lat_center))
    dy = abs(transform[4]) * 110_540

    dz_dy, dz_dx = np.gradient(z, dy, dx)
    slope = np.degrees(np.arctan(np.sqrt(dz_dx**2 + dz_dy**2)))

    # Aspect: 0 = N, 90 = E, 180 = S, 270 = W
    aspect = np.degrees(np.arctan2(-dz_dx, dz_dy))
    aspect = (aspect + 360) % 360

    return slope, aspect


# ─────────────────────────────────────────────────────────────────────────────
# Classification helpers
# ─────────────────────────────────────────────────────────────────────────────
def deg_to_cardinal(deg: float) -> str:
    """Convert an aspect angle to one of 8 cardinal directions."""
    if np.isnan(deg):
        return "N"
    dirs = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    return dirs[int(((deg + 22.5) % 360) / 45) % 8]


def classify_band(elev_ft: float) -> str:
    if elev_ft < TREELINE_LOW:
        return "below_treeline"
    elif elev_ft <= TREELINE_HIGH:
        return "near_treeline"
    else:
        return "above_treeline"


def terrain_risk(slope: float, cardinal: str, elev_ft: float) -> int:
    """
    Terrain-only avalanche risk score (1-5).

    1  Low          slope < 25°
    2  Moderate     slope 25-29° OR slope 30-45° with S/SW/SE aspect
    3  Considerable slope 30-45° with E/W aspect
    4  High         slope 30-45° with N/NE/NW aspect
    5  Extreme      slope 30-45° with N/NE/NW aspect AND above treeline
    """
    if slope < 25:
        return 1

    if slope < 30:
        return 2

    if 30 <= slope <= 45:
        if cardinal in NORTH_ASPECTS:
            return 5 if elev_ft > TREELINE_HIGH else 4
        if cardinal in {"S", "SW", "SE"}:
            return 2
        # E or W
        return 3

    # > 45°: steep rock / cliff — treat similarly to 30-45° band
    if cardinal in NORTH_ASPECTS:
        return 4
    return 3


# ─────────────────────────────────────────────────────────────────────────────
# Cluster cleaning (connected-component labelling on a grid)
# ─────────────────────────────────────────────────────────────────────────────
def remove_small_clusters(df: pd.DataFrame, min_size: int) -> pd.DataFrame:
    """
    Drop cells that belong to connected clusters smaller than *min_size*.

    Uses simple flood-fill on the (grid_row, grid_col) indices.  Neighbours
    are the 8 surrounding cells (Moore neighbourhood) so that diagonal
    ridgelines stay connected.
    """
    if df.empty or min_size < 2:
        return df

    # Build a lookup set of occupied (row, col) positions
    coords = set(zip(df["_gr"].values, df["_gc"].values))
    visited: set[tuple[int, int]] = set()
    labels: dict[tuple[int, int], int] = {}
    label_id = 0

    offsets = [(-1, -1), (-1, 0), (-1, 1),
               ( 0, -1),          ( 0, 1),
               ( 1, -1), ( 1, 0), ( 1, 1)]

    for seed in coords:
        if seed in visited:
            continue
        label_id += 1
        stack = [seed]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            labels[node] = label_id
            r, c = node
            for dr, dc in offsets:
                nb = (r + dr, c + dc)
                if nb in coords and nb not in visited:
                    stack.append(nb)

    # Count cluster sizes
    from collections import Counter
    cluster_sizes = Counter(labels.values())

    keep_labels = {lid for lid, cnt in cluster_sizes.items() if cnt >= min_size}
    mask = df.apply(lambda row: labels.get((row["_gr"], row["_gc"]), 0) in keep_labels, axis=1)
    return df[mask].copy()


# ─────────────────────────────────────────────────────────────────────────────
# GeoJSON cell polygon
# ─────────────────────────────────────────────────────────────────────────────
def cell_geojson(lon_center: float, lat_center: float,
                 half_dx_deg: float, half_dy_deg: float) -> str:
    """Return a GeoJSON Polygon string for a 500 m square cell."""
    w = lon_center - half_dx_deg
    e = lon_center + half_dx_deg
    s = lat_center - half_dy_deg
    n = lat_center + half_dy_deg
    coords = [[[w, s], [e, s], [e, n], [w, n], [w, s]]]
    return json.dumps({"type": "Polygon", "coordinates": coords})


# ─────────────────────────────────────────────────────────────────────────────
# Main grid generation
# ─────────────────────────────────────────────────────────────────────────────
def generate_grid(dem, transform, crs, nodata, slope, aspect):
    """
    Walk the DEM in 500 m steps, apply polygon + elevation + cluster filters,
    and return a DataFrame ready for Foundry.
    """
    inv = ~transform               # pixel ↔ geographic coordinate transform

    # Bounding box of the boundary polygon (lon, lat order from shapely)
    b_west, b_south, b_east, b_north = BOUNDARY_POLY.bounds

    # Convert geographic bounds → pixel indices
    col_w, row_n = inv * (b_west, b_north)
    col_e, row_s = inv * (b_east, b_south)

    col_w = max(0, int(col_w))
    col_e = min(dem.shape[1] - 1, int(col_e))
    row_n = max(0, int(row_n))
    row_s = min(dem.shape[0] - 1, int(row_s))

    # Pixel steps for 500 m cells
    lat_mid = (b_south + b_north) / 2
    px_m_x = abs(transform[0]) * 111_320 * np.cos(np.radians(lat_mid))
    px_m_y = abs(transform[4]) * 110_540

    step_x = max(1, int(round(CELL_SIZE_M / px_m_x)))
    step_y = max(1, int(round(CELL_SIZE_M / px_m_y)))

    half_x = step_x // 2
    half_y = step_y // 2

    # Half-cell size in degrees (for GeoJSON polygons)
    half_dx_deg = abs(transform[0]) * half_x
    half_dy_deg = abs(transform[4]) * half_y

    rows = []
    grid_row = -1

    for r in range(row_n, row_s, step_y):
        grid_row += 1
        grid_col = -1
        for c in range(col_w, col_e, step_x):
            grid_col += 1

            # ── Cell centre in geographic coordinates ────────────────────
            lon, lat = transform * (c, r)

            # ── 1) Polygon test ──────────────────────────────────────────
            if not BOUNDARY_POLY.contains(Point(lon, lat)):
                continue

            # ── 2) Clamp at WY/CO border ─────────────────────────────────
            if lat < WY_CO_BORDER:
                continue

            # ── Extract the DEM block for this cell ──────────────────────
            r0 = max(0, r - half_y)
            r1 = min(dem.shape[0], r + half_y + 1)
            c0 = max(0, c - half_x)
            c1 = min(dem.shape[1], c + half_x + 1)

            blk_elev = dem[r0:r1, c0:c1].astype(np.float64)
            blk_slope = slope[r0:r1, c0:c1]
            blk_aspect = aspect[r0:r1, c0:c1]

            if nodata is not None:
                valid = blk_elev != nodata
                if not valid.any():
                    continue
                blk_elev = blk_elev[valid]
                blk_slope = blk_slope[valid]
                blk_aspect = blk_aspect[valid]

            if len(blk_elev) == 0:
                continue

            # ── Aggregate statistics ─────────────────────────────────────
            with np.errstate(all="ignore"):
                elev_m = float(np.nanmean(blk_elev))
                if np.isnan(elev_m):
                    continue
                elev_ft = elev_m * M_TO_FT

                # 3) Elevation cutoff
                if elev_ft < ELEV_CUTOFF_FT:
                    continue

                slp = float(np.nanmax(blk_slope))   # MAX slope for SAR
                asp = float(np.nanmean(blk_aspect))  # dominant aspect

            if np.isnan(slp):
                slp = 0.0
            if np.isnan(asp):
                asp = 0.0

            # ── Derived fields ───────────────────────────────────────────
            cardinal = deg_to_cardinal(asp)
            band = classify_band(elev_ft)
            risk = terrain_risk(slp, cardinal, elev_ft)

            rows.append({
                "lat":            round(lat, 6),
                "lon":            round(lon, 6),
                "elevation_ft":   round(elev_ft, 0),
                "slope_deg":      round(slp, 1),
                "aspect_deg":     round(asp, 0),
                "aspect_cardinal": cardinal,
                "elevation_band": band,
                "avy_slope":      30 <= slp <= 45,
                "north_facing":   cardinal in NORTH_ASPECTS,
                "terrain_risk_score": risk,
                "composite_risk_score": risk,      # weather adjustments later
                "geopoint":       f"{lat:.6f},{lon:.6f}",
                "geoshape":       cell_geojson(lon, lat, half_dx_deg, half_dy_deg),
                "_gr":            grid_row,         # temp — for cluster cleaning
                "_gc":            grid_col,
            })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # ── 4) Remove small disconnected clusters ────────────────────────────
    before = len(df)
    df = remove_small_clusters(df, MIN_CLUSTER_SIZE)
    removed = before - len(df)
    if removed:
        print(f"  Cluster cleaning: removed {removed} isolated cell(s)")

    # ── Assign sequential cell IDs and drop temp columns ─────────────────
    df = df.drop(columns=["_gr", "_gc"]).reset_index(drop=True)
    df.insert(0, "cell_id", [f"MBR-{i+1:06d}" for i in range(len(df))])

    return df


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main():
    global ELEV_CUTOFF_FT, MIN_CLUSTER_SIZE

    parser = argparse.ArgumentParser(
        description="Generate 500 m terrain grid (mountain-only) for the Snowy Range"
    )
    parser.add_argument("--dem", nargs="+", required=True,
                        help="Path(s) to USGS 1/3 arc-second DEM GeoTIFF(s)")
    parser.add_argument("--output", default="snowy_range_terrain_grid.csv",
                        help="Output CSV path")
    parser.add_argument("--elev-cutoff", type=int, default=ELEV_CUTOFF_FT,
                        help="Elevation cutoff in feet (default 7800)")
    parser.add_argument("--min-cluster", type=int, default=MIN_CLUSTER_SIZE,
                        help="Min connected cells to keep (default 5)")
    args = parser.parse_args()

    ELEV_CUTOFF_FT = args.elev_cutoff
    MIN_CLUSTER_SIZE = args.min_cluster

    # ── Resolve DEM paths (support glob patterns) ────────────────────────
    dem_files = []
    for pat in args.dem:
        expanded = glob.glob(pat)
        dem_files.extend(expanded if expanded else [pat])

    for path in dem_files:
        if not os.path.exists(path):
            print(f"ERROR: DEM file not found: {path}")
            sys.exit(1)

    print(f"Loading {len(dem_files)} DEM tile(s)...")
    dem, transform, crs, nodata = load_dem(dem_files)
    print(f"  DEM shape: {dem.shape}  CRS: {crs}")

    print("Computing slope & aspect...")
    slope_arr, aspect_arr = compute_slope_aspect(dem, transform, nodata)

    print(f"Generating grid (cell={CELL_SIZE_M} m, "
          f"elev>={ELEV_CUTOFF_FT} ft, cluster>={MIN_CLUSTER_SIZE})...")
    df = generate_grid(dem, transform, crs, nodata, slope_arr, aspect_arr)

    if df.empty:
        print("ERROR: No cells generated. Check DEM coverage and bounds.")
        sys.exit(1)

    # ── Summary ──────────────────────────────────────────────────────────
    area_km2 = len(df) * (CELL_SIZE_M ** 2) / 1_000_000
    print("\n" + "=" * 56)
    print("  SNOWY RANGE TERRAIN GRID - SUMMARY")
    print("=" * 56)
    print(f"  Total cells:        {len(df):,}")
    print(f"  Approximate area:   {area_km2:,.1f} sq km")
    print(f"  Elevation range:    {df.elevation_ft.min():,.0f} - "
          f"{df.elevation_ft.max():,.0f} ft")
    print(f"  Elevation cutoff:   {ELEV_CUTOFF_FT:,} ft")
    print()
    print("  Elevation bands:")
    for band, cnt in df.elevation_band.value_counts().items():
        pct = cnt / len(df) * 100
        print(f"    {band:20s}  {cnt:>6,}  ({pct:5.1f}%)")
    print()
    print("  Terrain risk score distribution:")
    labels = {1: "Low", 2: "Moderate", 3: "Considerable", 4: "High", 5: "Extreme"}
    for score in sorted(df.terrain_risk_score.unique()):
        cnt = (df.terrain_risk_score == score).sum()
        pct = cnt / len(df) * 100
        print(f"    {score} ({labels.get(score, '?'):13s})  {cnt:>6,}  ({pct:5.1f}%)")
    print()
    avy = df.avy_slope.sum()
    north = df.north_facing.sum()
    print(f"  Avalanche terrain (30-45 deg):  {avy:,} cells "
          f"({avy / len(df) * 100:.1f}%)")
    print(f"  North-facing cells:          {north:,} cells "
          f"({north / len(df) * 100:.1f}%)")
    print("=" * 56)

    df.to_csv(args.output, index=False)
    print(f"\nSaved -> {args.output}  ({os.path.getsize(args.output) / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
