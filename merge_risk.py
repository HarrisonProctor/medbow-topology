#!/usr/bin/env python3
"""
Merge Terrain & Weather Risk for Palantir AIP
Takes terrain grid CSV and SNOTEL JSON as inputs, computes composite risk score,
and outputs a final dataset for Palantir Foundry.
"""

import argparse
import pandas as pd
import json
import os
import sys

# Wind loading chart (Wind blows FROM a direction, loading the OPPOSITE aspects)
WIND_LOAD_MAP = {
    "N": ["S", "SE", "SW"],
    "NE": ["SW", "W", "S"],
    "E": ["W", "NW", "SW"],
    "SE": ["NW", "N", "W"],
    "S": ["N", "NE", "NW"],
    "SW": ["NE", "N", "E"],
    "W": ["E", "NE", "SE"],
    "NW": ["SE", "E", "S"]
}

def compute_composite_risk(row, snotel_data):
    base_risk = row.get("terrain_risk_score", 1)
    
    # If there is no avalanche terrain (slope < 25), risk stays 1 or low regardless of weather (typically)
    # But we'll follow prompt rules: start with terrain score, add bumps, cap at 5.
    risk = int(base_risk)
    
    # Extract weather trends
    trends = snotel_data.get("trends", {})
    conditions = snotel_data.get("current_conditions", {})
    
    new_snow = trends.get("new_snow_48hr_in", 0)
    warming = trends.get("warming_trend", False)
    
    wind_speed = conditions.get("wind_speed_mph", 0)
    wind_dir = conditions.get("wind_direction_cardinal", "W")
    
    # 1. Snow bump
    if new_snow > 1.0:
        risk += 1
        
    # 2. Wind bump
    loaded_aspects = WIND_LOAD_MAP.get(wind_dir, [])
    if wind_speed > 25.0 and row.get("aspect_cardinal") in loaded_aspects:
        risk += 1
        
    # 3. Warming trend bump (wet slide concern for near/below treeline)
    elev_band = row.get("elevation_band", "above_treeline")
    if warming and elev_band in ["below_treeline", "near_treeline"]:
        risk += 1
        
    # Cap at 5
    return min(risk, 5)


def main():
    parser = argparse.ArgumentParser(description="Merge SNOTEL data into Terrain Grid for composite risk")
    parser.add_argument("--csv", default="snowy_range_terrain_grid.csv", help="Input terrain CSV")
    parser.add_argument("--json", default="brooklyn_lake_snotel.json", help="Input weather JSON")
    parser.add_argument("--output", default="final_snowy_range_ontology.csv", help="Output composite CSV")
    args = parser.parse_args()
    
    if not os.path.exists(args.csv):
        print(f"ERROR: Terrain CSV not found: {args.csv}")
        sys.exit(1)
        
    if not os.path.exists(args.json):
        print(f"ERROR: SNOTEL JSON not found: {args.json}")
        sys.exit(1)
        
    print(f"Loading terrain grid from {args.csv}...")
    df = pd.read_csv(args.csv)
    
    print(f"Loading SNOTEL data from {args.json}...")
    with open(args.json, 'r') as f:
        snotel = json.load(f)
        
    print("Computing composite risk scores...")
    df["composite_risk_score"] = df.apply(lambda row: compute_composite_risk(row, snotel), axis=1)
    
    # Print summary
    print("\nComposite Risk Score breakdown:")
    print(df["composite_risk_score"].value_counts().sort_index())
    
    num_max_risk = len(df[df["composite_risk_score"] == 5])
    print(f"\nCells shifted to Extreme (5) risk: {num_max_risk}")
    
    df.to_csv(args.output, index=False)
    print(f"\nSaved final output to {args.output}")

if __name__ == "__main__":
    main()
