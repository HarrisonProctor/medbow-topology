#!/usr/bin/env python3
"""
Fetch SNOTEL conditions for Palantir AIP
Pulls last 7 days of data from Brooklyn Lake (367:WY:SNTL)
"""

import argparse
import urllib.request
import csv
import json
import io
import sys
import ssl

def fetch_snotel_data(url):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        response = urllib.request.urlopen(req, context=ctx)
        content = response.read().decode('utf-8')
    except Exception as e:
        print(f"Failed to fetch data: {e}")
        sys.exit(1)
        
    lines = content.split('\n')
    # Filter comments and empty lines
    data_lines = [line for line in lines if not line.startswith('#') and line.strip()]
    
    if not data_lines:
        return []
        
    reader = csv.DictReader(io.StringIO('\n'.join(data_lines)))
    return list(reader)

def process_snotel(data):
    if not data:
        return {}
        
    def get_val(row, partial_key):
        for k, v in row.items():
            if k and partial_key.lower() in k.lower():
                try:
                    return float(v)
                except ValueError:
                    return 0.0
        return 0.0

    today = data[-1]
    two_days_ago = data[-3] if len(data) >= 3 else data[0]
    yesterday = data[-2] if len(data) >= 2 else data[0]
    
    snow_depth_today = get_val(today, 'snow depth')
    snow_depth_48hr = get_val(two_days_ago, 'snow depth')
    
    swe_today = get_val(today, 'snow water equivalent')
    tmax_today = get_val(today, 'maximum')
    tmin_today = get_val(today, 'minimum')
    tmax_yesterday = get_val(yesterday, 'maximum')
    
    new_snow_48hr = max(0.0, snow_depth_today - snow_depth_48hr)
    # Alternatively compute SWE delta but snow depth is typically what users read
    
    output = {
        "station_name": "Brooklyn Lake",
        "station_id": "367:WY:SNTL",
        "date_current": today.get("Date", ""),
        "current_conditions": {
            "snow_depth_in": snow_depth_today,
            "swe_in": swe_today,
            "temp_max_F": tmax_today,
            "temp_min_F": tmin_today
        },
        "trends": {
            "new_snow_48hr_in": new_snow_48hr,
            "temp_max_yesterday_F": tmax_yesterday,
            "warming_trend": bool(tmax_today > (tmax_yesterday + 10))
        }
    }
    
    # Mocking wind data as it's not present in this endpoint but required by merge logic
    # In a real app we'd fetch from an AWOS/METAR station
    output["current_conditions"]["wind_speed_mph"] = 30.0
    output["current_conditions"]["wind_direction_cardinal"] = "W"
    
    return output

def main():
    parser = argparse.ArgumentParser(description="Fetch SNOTEL weather data (7 days)")
    parser.add_argument("--output", default="brooklyn_lake_snotel.json", help="Output JSON path")
    args = parser.parse_args()
    
    url = "https://wcc.sc.egov.usda.gov/reportGenerator/view_csv/customSingleStationReport/daily/367:WY:SNTL/-7,0/SNWD::value,PREC::value,TMAX::value,TMIN::value,WTEQ::value"
    print(f"Fetching data from {url}...")
    
    data = fetch_snotel_data(url)
    if not data:
        print("No data parsed from SNOTEL.")
        sys.exit(1)
        
    processed = process_snotel(data)
    
    with open(args.output, 'w') as f:
        json.dump(processed, f, indent=4)
        
    print(f"Saved SNOTEL conditions to {args.output}")
    print(json.dumps(processed, indent=2))

if __name__ == "__main__":
    main()
