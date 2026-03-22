#!/usr/bin/env python3
"""
Snowy Range / Medicine Bow Terrain Grid Generator
Generates a CSV of 100m terrain cells for avalanche risk assessment.

Requires:
    pip install rasterio numpy pandas

DEM Data:
    Download USGS 3DEP 1/3 arc-second DEM tiles from:
    https://apps.nationalmap.gov/downloader/
    
    You need tiles covering 41.0N-41.45N, 106.75W-105.95W
    Search for "1/3 arc-second DEM" and download the GeoTIFF files.
    
    The tiles you likely need:
      - USGS_13_n42w107_20230612.tif  (covers most of the Snowy Range)
      - USGS_13_n42w106_20230612.tif  (covers eastern portion)
    
    Note: USGS names tiles by their NW corner. n42w107 covers 41N-42N, 107W-106W.

Usage:
    python3 generate_terrain_grid.py --dem path/to/dem1.tif [path/to/dem2.tif ...]
    
    Or if you have a merged/VRT file:
    python3 generate_terrain_grid.py --dem merged_dem.tif
"""

import argparse
import numpy as np
import pandas as pd
import rasterio
from rasterio.merge import merge
from rasterio.warp import transform_bounds
import os
import sys


# =============================================================================
# CONFIGURATION — Adjust these for your needs
# =============================================================================

# Bounding box: Medicine Bow / Snowy Range, above CO border
BOUNDS = {
    "south": 41.00,     # Just above CO border (~40.99)
    "north": 41.45,     # North end near Elk Mountain
    "west": -106.75,    # West of Kennaday Peak
    "east": -106.10,    # East side of range
}

# Grid resolution in meters
CELL_SIZE_M = 100

# Minimum elevation to include (feet) — filters out low-elevation non-avy terrain
MIN_ELEVATION_FT = 9500

# Elevation band thresholds (feet) — specific to Medicine Bow / Snowy Range
TREELINE_LOWER = 9500   # Below this = below_treeline (but filtered out anyway)
TREELINE_UPPER = 11000  # Above this = above_treeline
# Between lower and upper = near_treeline

# Avalanche slope angle range (degrees)
AVY_SLOPE_MIN = 30
AVY_SLOPE_MAX = 45

# Aspect classifications
NORTH_ASPECTS = ["N", "NE", "NW"]


# =============================================================================
# CORE FUNCTIONS
# =============================================================================

def load_dem(dem_paths):
    """Load and merge DEM tiles, return array and metadata."""
    if len(dem_paths) == 1:
        src = rasterio.open(dem_paths[0])
        dem = src.read(1)
        transform = src.transform
        crs = src.crs
        nodata = src.nodata
        src.close()
    else:
        # Merge multiple tiles
        datasets = [rasterio.open(p) for p in dem_paths]
        dem, transform = merge(datasets)
        dem = dem[0]  # merge returns (bands, rows, cols)
        crs = datasets[0].crs
        nodata = datasets[0].nodata
        for ds in datasets:
            ds.close()
    
    print(f"DEM loaded: {dem.shape[0]} rows x {dem.shape[1]} cols")
    print(f"CRS: {crs}")
    print(f"Resolution: {abs(transform[0]):.6f}° x {abs(transform[4]):.6f}°")
    
    return dem, transform, crs, nodata


def compute_slope_aspect(dem, transform, nodata):
    """
    Compute slope (degrees) and aspect (degrees, 0=N clockwise) from DEM.
    Uses numpy gradient — accounts for cell size in meters.
    """
    # Replace nodata with NaN
    dem_float = dem.astype(np.float64)
    if nodata is not None:
        dem_float[dem == nodata] = np.nan
    
    # Cell size in meters (approximate at this latitude)
    lat_center = (BOUNDS["south"] + BOUNDS["north"]) / 2
    cellsize_x = abs(transform[0]) * 111320 * np.cos(np.radians(lat_center))  # meters
    cellsize_y = abs(transform[4]) * 110540  # meters
    
    print(f"Cell size: {cellsize_x:.1f}m x {cellsize_y:.1f}m")
    
    # Compute gradients (dz/dx, dz/dy)
    # np.gradient returns gradient in row (y) and col (x) directions
    dz_dy, dz_dx = np.gradient(dem_float, cellsize_y, cellsize_x)
    
    # Slope in degrees
    slope = np.degrees(np.arctan(np.sqrt(dz_dx**2 + dz_dy**2)))
    
    # Aspect in degrees (0 = North, clockwise)
    # np.arctan2 gives angle from positive x-axis, counterclockwise
    # We need to convert to compass bearing
    aspect = np.degrees(np.arctan2(-dz_dx, dz_dy))
    aspect = (aspect + 360) % 360  # Normalize to 0-360
    
    return slope, aspect


def degrees_to_cardinal(aspect_deg):
    """Convert aspect in degrees to 8-point cardinal direction."""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int(((aspect_deg + 22.5) % 360) / 45)
    return dirs[idx]


def classify_elevation_band(elev_ft):
    """Classify elevation into bands."""
    if elev_ft < TREELINE_LOWER:
        return "below_treeline"
    elif elev_ft <= TREELINE_UPPER:
        return "near_treeline"
    else:
        return "above_treeline"


def meters_to_feet(m):
    return m * 3.28084


def generate_grid(dem, transform, crs, nodata, slope, aspect):
    """
    Generate 100m grid cells by sampling/averaging the DEM at regular intervals.
    """
    # Figure out the pixel coordinates of our bounding box
    # transform maps pixel (col, row) -> (x, y) in CRS coordinates
    inv_transform = ~transform
    
    # Get pixel bounds
    col_west, row_north = inv_transform * (BOUNDS["west"], BOUNDS["north"])
    col_east, row_south = inv_transform * (BOUNDS["east"], BOUNDS["south"])
    
    col_west, col_east = int(col_west), int(col_east)
    row_north, row_south = int(row_north), int(row_south)
    
    # Clamp to array bounds
    col_west = max(0, col_west)
    col_east = min(dem.shape[1] - 1, col_east)
    row_north = max(0, row_north)
    row_south = min(dem.shape[0] - 1, row_south)
    
    print(f"Pixel bounds: rows [{row_north}:{row_south}], cols [{col_west}:{col_east}]")
    
    # Calculate step size: how many DEM pixels per 100m cell
    lat_center = (BOUNDS["south"] + BOUNDS["north"]) / 2
    pixel_size_m_x = abs(transform[0]) * 111320 * np.cos(np.radians(lat_center))
    pixel_size_m_y = abs(transform[4]) * 110540
    
    step_x = max(1, int(round(CELL_SIZE_M / pixel_size_m_x)))
    step_y = max(1, int(round(CELL_SIZE_M / pixel_size_m_y)))
    
    print(f"Sampling every {step_y} rows x {step_x} cols (~{CELL_SIZE_M}m cells)")
    
    # Sample the grid
    rows = []
    cell_id = 0
    
    # Use block averaging for smoother results
    half_x = step_x // 2
    half_y = step_y // 2
    
    for r in range(row_north, row_south, step_y):
        for c in range(col_west, col_east, step_x):
            # Average over the cell area
            r_start = max(0, r - half_y)
            r_end = min(dem.shape[0], r + half_y + 1)
            c_start = max(0, c - half_x)
            c_end = min(dem.shape[1], c + half_x + 1)
            
            block_elev = dem[r_start:r_end, c_start:c_end].astype(np.float64)
            block_slope = slope[r_start:r_end, c_start:c_end]
            block_aspect = aspect[r_start:r_end, c_start:c_end]
            
            # Skip nodata
            if nodata is not None:
                mask = block_elev != nodata
                if not mask.any():
                    continue
                block_elev = block_elev[mask]
                block_slope = block_slope[mask]
                block_aspect = block_aspect[mask]
            
            if len(block_elev) == 0:
                continue
            
            # Get center coordinates
            x, y = transform * (c, r)
            
            elev_m = float(np.nanmean(block_elev))
            elev_ft = meters_to_feet(elev_m)
            
            # Filter: skip cells below minimum elevation
            if elev_ft < MIN_ELEVATION_FT:
                continue
            
            slp = float(np.nanmax(block_slope))
            asp = float(np.nanmean(block_aspect))  # Circular mean would be better but fine for demo
            
            cell_id += 1
            cardinal = degrees_to_cardinal(asp)
            band = classify_elevation_band(elev_ft)
            
            rows.append({
                "cell_id": f"MBR-{cell_id:06d}",
                "lat": round(y, 6),
                "lon": round(x, 6),
                "elevation_ft": round(elev_ft, 0),
                "slope_deg": round(slp, 1),
                "aspect_deg": round(asp, 0),
                "aspect_cardinal": cardinal,
                "elevation_band": band,
                "avy_slope": AVY_SLOPE_MIN <= slp <= AVY_SLOPE_MAX,
                "north_facing": cardinal in NORTH_ASPECTS,
            })
    
    df = pd.DataFrame(rows)
    print(f"\nGenerated {len(df):,} cells")
    
    return df


def print_summary(df):
    """Print summary statistics."""
    print("\n" + "=" * 60)
    print("TERRAIN GRID SUMMARY")
    print("=" * 60)
    print(f"Total cells:        {len(df):,}")
    print(f"Elevation range:    {df.elevation_ft.min():.0f} - {df.elevation_ft.max():.0f} ft")
    print(f"Slope range:        {df.slope_deg.min():.1f} - {df.slope_deg.max():.1f}°")
    print(f"Lat range:          {df.lat.min():.4f} - {df.lat.max():.4f}")
    print(f"Lon range:          {df.lon.min():.4f} - {df.lon.max():.4f}")
    print(f"\nElevation bands:")
    for band, count in df.elevation_band.value_counts().items():
        pct = count / len(df) * 100
        print(f"  {band:20s} {count:>6,} cells ({pct:.1f}%)")
    print(f"\nAvalanche terrain (30-45°): {df.avy_slope.sum():,} cells ({df.avy_slope.mean()*100:.1f}%)")
    print(f"North-facing (N/NE/NW):    {df.north_facing.sum():,} cells ({df.north_facing.mean()*100:.1f}%)")
    print(f"High-risk (avy slope + north): {((df.avy_slope) & (df.north_facing)).sum():,} cells")
    print(f"\nCSV size estimate: ~{len(df) * 80 / 1024:.0f} KB")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Generate Snowy Range terrain grid for avalanche SAR tool")
    parser.add_argument("--dem", nargs="+", required=True, help="Path(s) to USGS DEM GeoTIFF file(s)")
    parser.add_argument("--output", default="snowy_range_terrain_grid.csv", help="Output CSV path")
    parser.add_argument("--cell-size", type=int, default=100, help="Grid cell size in meters (default: 100)")
    parser.add_argument("--min-elev", type=float, default=9500, help="Minimum elevation in feet (default: 9500)")
    args = parser.parse_args()
    
    global CELL_SIZE_M, MIN_ELEVATION_FT
    CELL_SIZE_M = args.cell_size
    MIN_ELEVATION_FT = args.min_elev
    
    # Validate inputs
    for path in args.dem:
        if not os.path.exists(path):
            print(f"ERROR: DEM file not found: {path}")
            sys.exit(1)
    
    print("Loading DEM...")
    dem, transform, crs, nodata = load_dem(args.dem)
    
    print("\nComputing slope and aspect...")
    slope, aspect = compute_slope_aspect(dem, transform, nodata)
    
    print("\nGenerating grid cells...")
    df = generate_grid(dem, transform, crs, nodata, slope, aspect)
    
    if len(df) == 0:
        print("ERROR: No cells generated. Check your bounds and DEM coverage.")
        sys.exit(1)
    
    print_summary(df)
    
    # Save
    df.to_csv(args.output, index=False)
    print(f"\nSaved to: {args.output}")


if __name__ == "__main__":
    main()
