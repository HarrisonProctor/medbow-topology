# Snowy Range Terrain Grid Generator — Setup Guide

## What this does
Generates a CSV of 500m terrain grid cells covering the Medicine Bow / Snowy Range
above the Colorado border. Each cell has elevation, slope, aspect, and avalanche
risk flags. It also supports merging with SNOTEL data to create a composite risk score. 

## Step 1: Install dependencies

```bash
pip install rasterio numpy pandas
```

If rasterio gives you trouble on Ubuntu, you may need GDAL first:
```bash
sudo apt-get update
sudo apt-get install gdal-bin libgdal-dev python3-gdal
pip install rasterio numpy pandas
```

## Step 2: Download the DEM

Go to the USGS National Map Downloader:
**https://apps.nationalmap.gov/downloader/**

1. Click "Elevation Products (3DEP)"
2. Under "Subcategory", select **1/3 arc-second DEM**
3. Draw a bounding box over the Medicine Bow / Snowy Range area:
   - Roughly: 41.0°N to 41.45°N, 106.75°W to 106.10°W
4. Click "Search Products"
5. Download the GeoTIFF files. You'll likely need **two tiles**:
   - `USGS_13_n42w107` — covers 41°N to 42°N, 107°W to 106°W (most of the range)
   - `USGS_13_n42w106` — covers 41°N to 42°N, 106°W to 105°W (eastern edge)
6. Save them somewhere like `~/dem/`

**Tile naming**: USGS names tiles by the NW corner, so `n42w107` covers
latitudes 41-42 and longitudes 106-107.

**File size**: Each tile is ~300-500 MB. Make sure you have space.

## Step 3: Run the generator

```bash
# With two tiles:
python3 generate_terrain_grid.py \
    --dem ~/dem/USGS_13_n42w107*.tif ~/dem/USGS_13_n42w106*.tif \
    --output snowy_range_terrain_grid.csv

# If you only need the core Snowy Range (single tile covers most of it):
python3 generate_terrain_grid.py \
    --dem ~/dem/USGS_13_n42w107*.tif \
    --output snowy_range_terrain_grid.csv
```

## Step 4: Merge SNOTEL Weather Data

To calculate the `composite_risk_score` incorporating live weather trends (avalanche risk due to wind, new snow, or warming), run the `merge_risk.py` script:

```bash
python3 merge_risk.py --csv snowy_range_terrain_grid.csv --json brooklyn_lake_snotel.json --output final_snowy_range_ontology.csv
```

## Step 5: Check the output

The script prints a summary. You should see something like:
```
Total cells:        ~30,000 - 60,000
Elevation range:    9,500 - 12,013 ft
Avalanche terrain:  ~15-25% of cells
```

## Column Reference

| Column                 | Type    | Description                                      |
|------------------------|---------|--------------------------------------------------|
| cell_id                | STRING  | Unique ID (e.g., MBR-000001). Primary key.       |
| lat                    | DOUBLE  | Latitude of cell center                          |
| lon                    | DOUBLE  | Longitude of cell center                         |
| elevation_ft           | DOUBLE  | Elevation in feet                                |
| slope_deg              | DOUBLE  | Slope angle in degrees                           |
| aspect_deg             | DOUBLE  | Aspect in degrees (0=N, 90=E, 180=S, 270=W)      |
| aspect_cardinal        | STRING  | 8-point cardinal direction (N, NE, E, SE, etc.)  |
| elevation_band         | STRING  | below_treeline / near_treeline / above_treeline  |
| avy_slope              | BOOLEAN | True if slope is 30-45° (prime avalanche angle)  |
| north_facing           | BOOLEAN | True if aspect is N, NE, or NW                   |
| terrain_risk_score     | INTEGER | Base avalanche risk score based on terrain alone |
| geopoint               | STRING  | "lat,lon" format for Palantir maps               |
| geoshape               | STRING  | GeoJSON Polygon of the grid cell                 |
| composite_risk_score   | INTEGER | Final score (terrain + weather) (merge_risk.py)  |

## Troubleshooting

**"No cells generated"**: Your DEM doesn't cover the bounding box. Check that
your tiles cover 41.0-41.45°N, 106.10-106.75°W.

**Rasterio import errors**: Make sure GDAL is installed system-level first.

**Too many cells**: Increase --cell-size to 200. Too few? Decrease to 50.
For Foundry free tier, aim for under 100k rows.
