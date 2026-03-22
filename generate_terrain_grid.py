#!/usr/bin/env python3
"""
Snowy Range Terrain Grid Generator (500m cells)
Generates a CSV of terrain cells for avalanche risk assessment in Palantir AIP.
"""

import argparse
import numpy as np
import pandas as pd
import rasterio
from rasterio.merge import merge
import os
import sys

# Bounds defined by the prompt
BOUNDS = {
    "south": 41.00,
    "north": 41.45,
    "west": -106.75,
    "east": -106.10,
}

CELL_SIZE_M = 500
MIN_ELEVATION_FT = 9500

NORTH_ASPECTS = ["N", "NE", "NW"]

def load_dem(dem_paths):
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
        dem = dem[0]
        crs = datasets[0].crs
        nodata = datasets[0].nodata
        for ds in datasets:
            ds.close()
    return dem, transform, crs, nodata

def compute_slope_aspect(dem, transform, nodata):
    dem_float = dem.astype(np.float64)
    if nodata is not None:
        dem_float[dem == nodata] = np.nan
        
    lat_center = (BOUNDS["south"] + BOUNDS["north"]) / 2
    cellsize_x = abs(transform[0]) * 111320 * np.cos(np.radians(lat_center))
    cellsize_y = abs(transform[4]) * 110540
    
    dz_dy, dz_dx = np.gradient(dem_float, cellsize_y, cellsize_x)
    slope = np.degrees(np.arctan(np.sqrt(dz_dx**2 + dz_dy**2)))
    
    aspect = np.degrees(np.arctan2(-dz_dx, dz_dy))
    aspect = (aspect + 360) % 360
    return slope, aspect

def degrees_to_cardinal(aspect_deg):
    if np.isnan(aspect_deg):
        return "N"
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int(((aspect_deg + 22.5) % 360) / 45) % 8
    return dirs[idx]

def classify_elevation_band(elev_ft):
    if elev_ft < 9500:
        return "below_treeline"
    elif elev_ft <= 11000:
        return "near_treeline"
    else:
        return "above_treeline"

def compute_terrain_risk(slope, cardinal, elev):
    if slope < 25:
        return 1
    elif 25 <= slope < 30:
        return 2
    elif 30 <= slope <= 45:
        if cardinal in NORTH_ASPECTS:
            if elev > 11000:
                return 5
            return 4
        return 3
    else:
        # > 45 degrees: typically less dangerous for slabs but still risky. We'll assign it 3 as a baseline if not specified.
        # But if it's north facing, we will give it 4.
        if cardinal in NORTH_ASPECTS:
            return 4
        return 3

def meters_to_feet(m):
    return m * 3.28084

def generate_grid(dem, transform, crs, nodata, slope, aspect):
    inv_transform = ~transform
    
    col_west, row_north = inv_transform * (BOUNDS["west"], BOUNDS["north"])
    col_east, row_south = inv_transform * (BOUNDS["east"], BOUNDS["south"])
    
    col_west, col_east = int(col_west), int(col_east)
    row_north, row_south = int(row_north), int(row_south)
    
    col_west = max(0, col_west)
    col_east = min(dem.shape[1] - 1, col_east)
    row_north = max(0, row_north)
    row_south = min(dem.shape[0] - 1, row_south)
    
    lat_center = (BOUNDS["south"] + BOUNDS["north"]) / 2
    pixel_size_m_x = abs(transform[0]) * 111320 * np.cos(np.radians(lat_center))
    pixel_size_m_y = abs(transform[4]) * 110540
    
    step_x = max(1, int(round(CELL_SIZE_M / pixel_size_m_x)))
    step_y = max(1, int(round(CELL_SIZE_M / pixel_size_m_y)))
    
    rows = []
    cell_id = 0
    
    half_x = step_x // 2
    half_y = step_y // 2
    
    for r in range(row_north, row_south, step_y):
        for c in range(col_west, col_east, step_x):
            r_start = max(0, r - half_y)
            r_end = min(dem.shape[0], r + half_y + 1)
            c_start = max(0, c - half_x)
            c_end = min(dem.shape[1], c + half_x + 1)
            
            block_elev = dem[r_start:r_end, c_start:c_end].astype(np.float64)
            block_slope = slope[r_start:r_end, c_start:c_end]
            block_aspect = aspect[r_start:r_end, c_start:c_end]
            
            if nodata is not None:
                mask = block_elev != nodata
                if not mask.any():
                    continue
                block_elev = block_elev[mask]
                block_slope = block_slope[mask]
                block_aspect = block_aspect[mask]
            
            if len(block_elev) == 0:
                continue
            
            x, y = transform * (c, r)
            
            elev_m = float(np.nanmean(block_elev))
            elev_ft = meters_to_feet(elev_m)
            
            if elev_ft < MIN_ELEVATION_FT:
                continue
            
            slp = float(np.nanmax(block_slope)) # Max slope for SAR worst-case
            asp = float(np.nanmean(block_aspect))
            
            if np.isnan(slp) or np.isnan(asp):
                continue
                
            cell_id += 1
            cardinal = degrees_to_cardinal(asp)
            band = classify_elevation_band(elev_ft)
            risk_score = compute_terrain_risk(slp, cardinal, elev_ft)
            
            # Compute corners for 500m square
            # we know step_x and step_y in pixels, which is cell_size_x and cell_size_y
            x_min, y_min = transform * (c - half_x, r + half_y)
            x_max, y_max = transform * (c + half_x, r - half_y)
            
            # Ensure coordinates array closes on itself
            polygon_coords = [[[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max], [x_min, y_min]]]
            geoshape = f'{{"type":"Polygon","coordinates":{polygon_coords}}}'.replace("'", '"')
            
            rows.append({
                "cell_id": f"MBR-{cell_id:06d}",
                "lat": round(y, 6),
                "lon": round(x, 6),
                "elevation_ft": round(elev_ft, 0),
                "slope_deg": round(slp, 1),
                "aspect_deg": round(asp, 0),
                "aspect_cardinal": cardinal,
                "elevation_band": band,
                "avy_slope": 30 <= slp <= 45,
                "north_facing": cardinal in NORTH_ASPECTS,
                "terrain_risk_score": risk_score,
                "geopoint": f"{y:.6f},{x:.6f}",
                "geoshape": geoshape
            })
            
    df = pd.DataFrame(rows)
    return df

def main():
    parser = argparse.ArgumentParser(description="Generate 500m Terrain Grid for Snowy Range")
    parser.add_argument("--dem", nargs="+", required=True, help="Path(s) to USGS DEM GeoTIFF file(s)")
    parser.add_argument("--output", default="snowy_range_terrain_grid.csv", help="Output CSV path")
    args = parser.parse_args()
    
    import glob
    dem_files = []
    for path in args.dem:
        expanded = glob.glob(path)
        if expanded:
            dem_files.extend(expanded)
        else:
            dem_files.append(path)
            
    for path in dem_files:
        if not os.path.exists(path):
            print(f"ERROR: DEM file not found: {path}")
            sys.exit(1)
            
    dem, transform, crs, nodata = load_dem(dem_files)
    slope, aspect = compute_slope_aspect(dem, transform, nodata)
    df = generate_grid(dem, transform, crs, nodata, slope, aspect)
    
    if len(df) == 0:
        print("ERROR: No cells generated. Check your bounds and DEM coverage.")
        sys.exit(1)
        
    print(f"Total cells: {len(df)}")
    print(f"Elevation range: {df.elevation_ft.min():.0f} - {df.elevation_ft.max():.0f} ft")
    print("\nElevation bands:")
    print(df.elevation_band.value_counts())
    print(f"\nAvalanche terrain (30-45°): {df.avy_slope.sum()} cells")
    print("\nTerrain Risk Score breakdown:")
    print(df.terrain_risk_score.value_counts().sort_index())
    
    df.to_csv(args.output, index=False)
    print(f"Saved {args.output}")

if __name__ == "__main__":
    main()
