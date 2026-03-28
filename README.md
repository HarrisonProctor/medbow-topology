# Snowy Range Terrain Grid — Avalanche Risk Assessment

## What this does
Generates a clean, mountain-only CSV of 500 m terrain grid cells covering the
Medicine Bow / Snowy Range in southeastern Wyoming. Designed for Albany County
SAR and rendered as a choropleth in Palantir Foundry.

The output grid uses three filters to produce a visually clean mountain
silhouette on the basemap:

1. **Boundary polygon** — only cells inside the user-drawn outline
2. **Elevation cutoff** — only cells ≥ 7,800 ft (removes plains & valley floors)
3. **Cluster cleaning** — removes disconnected groups of < 5 cells (no stray dots)

## Project Structure

```
medbow-topology/
├── README.md
├── .gitignore
├── src/
│   ├── generate_terrain_grid.py   # ← main script (this README documents it)
│   ├── download_dem.py            # Download DEM tiles from USGS
│   ├── fetch_snotel.py            # Fetch SNOTEL weather station data
│   └── merge_risk.py              # Merge terrain + weather into risk scores
└── data/
    ├── dem/                       # DEM GeoTIFF files (gitignored)
    ├── brooklyn_lake_snotel.json
    ├── snowy_range_terrain_grid.csv
    ├── final_snowy_range_ontology.csv
    └── test_500m.csv
```

## Quick Start

### 1. Install dependencies

```bash
pip install rasterio numpy pandas shapely
```

### 2. Download the DEM

Go to **https://apps.nationalmap.gov/downloader/**

1. Select **Elevation Products (3DEP)** → **1/3 arc-second DEM**
2. Draw a bounding box over the Snowy Range (≈ 41.0–41.65°N, 106.65–105.93°W)
3. Download the GeoTIFF tiles into `data/dem/`

You'll need at least `USGS_13_n42w107` (covers most of the range).

### 3. Generate the terrain grid

```bash
python src/generate_terrain_grid.py \
    --dem data/dem/USGS_13_n42w107*.tif \
    --output data/snowy_range_terrain_grid.csv
```

**Optional tuning flags:**

| Flag              | Default | Description                                    |
|-------------------|---------|------------------------------------------------|
| `--elev-cutoff`   | 7800    | Min elevation (ft) to include                  |
| `--min-cluster`   | 5       | Drop disconnected clusters smaller than this   |

```bash
# Tighter mountain shape (more aggressive filtering)
python src/generate_terrain_grid.py \
    --dem data/dem/*.tif \
    --output data/snowy_range_terrain_grid.csv \
    --elev-cutoff 8200 --min-cluster 10
```

### 4. (Optional) Merge SNOTEL weather data

```bash
python src/merge_risk.py \
    --csv data/snowy_range_terrain_grid.csv \
    --json data/brooklyn_lake_snotel.json \
    --output data/final_snowy_range_ontology.csv
```

## Column Reference

| Column                 | Type    | Description                                            |
|------------------------|---------|--------------------------------------------------------|
| cell_id                | STRING  | Unique ID (`MBR-000001`). Primary key.                 |
| lat                    | FLOAT   | Latitude of cell center                                |
| lon                    | FLOAT   | Longitude of cell center                               |
| elevation_ft           | FLOAT   | Mean elevation in feet                                 |
| slope_deg              | FLOAT   | **MAX** slope angle in degrees within the cell         |
| aspect_deg             | FLOAT   | Dominant aspect (0=N, 90=E, 180=S, 270=W)              |
| aspect_cardinal        | STRING  | 8-point direction (N, NE, E, SE, S, SW, W, NW)        |
| elevation_band         | STRING  | `below_treeline` / `near_treeline` / `above_treeline`  |
| avy_slope              | BOOL    | True if max slope is 30–45°                            |
| north_facing           | BOOL    | True if aspect is N, NE, or NW                         |
| terrain_risk_score     | INT     | 1–5 terrain-only risk (see below)                      |
| composite_risk_score   | INT     | 1–5 final score (weather adjustments via merge_risk)   |
| geopoint               | STRING  | `"lat,lon"` for Foundry GeoPoint                       |
| geoshape               | STRING  | GeoJSON Polygon for Foundry choropleth rendering       |

## Terrain Risk Score

| Score | Label         | Criteria                                             |
|-------|---------------|------------------------------------------------------|
| 1     | Low           | Slope < 25°                                          |
| 2     | Moderate      | Slope 25–29° **or** 30–45° with S/SW/SE aspect       |
| 3     | Considerable  | Slope 30–45° with E/W aspect                         |
| 4     | High          | Slope 30–45° with N/NE/NW aspect                     |
| 5     | Extreme       | Slope 30–45° with N/NE/NW aspect **and** above treeline |

## Boundary Polygon

The script uses a hardcoded boundary polygon drawn around the Snowy Range
study area. The southern edge is clamped to the WY/CO border at 41.00°N.

```
[41.007, -106.407] → [41.245, -106.632] → [41.588, -106.650]
    → [41.632, -106.266] → [41.490, -106.013] → [41.263, -105.930]
    → [41.067, -105.948] → [40.999, -106.014] (clamped to 41.00°N)
```

## Troubleshooting

- **"No cells generated"** — DEM doesn't cover the bounding box. Ensure your
  tiles cover ≈ 41.0–41.65°N, 105.93–106.65°W.
- **Shape looks too expansive** — raise `--elev-cutoff` to 8000 or 8200.
- **Gaps between ridgelines** — lower `--elev-cutoff` to 7500.
- **Stray dots on edges** — raise `--min-cluster` to 10 or 15.
- **Rasterio import errors** — install GDAL system-level first.
