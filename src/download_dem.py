import urllib.request
import json
import os
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

url = "https://tnmaccess.nationalmap.gov/api/v1/products?datasets=National%20Elevation%20Dataset%20(NED)%201/3%20arc-second&bbox=-106.75,41.0,-106.10,41.45&prodFormats=GeoTIFF"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
response = urllib.request.urlopen(req, context=ctx)
data = json.loads(response.read())

os.makedirs('dem', exist_ok=True)

# Keep track of unique tiles (e.g. n42w107) to avoid downloading multiple versions of the same tile
tiles_seen = set()

items = data.get('items', [])
for item in items:
    dl_url = item.get('downloadURL')
    if not dl_url:
        continue
    
    filename = dl_url.split('/')[-1]
    
    # Simple heuristic to extract tile name like n42w107
    parts = filename.split('_')
    tile_name = None
    for p in parts:
        if p.startswith('n') and 'w' in p:
            tile_name = p
            break
            
    if not tile_name:
        # If we can't parse it, just download
        tile_name = filename
        
    if tile_name in tiles_seen:
        continue
        
    tiles_seen.add(tile_name)
    
    out_path = os.path.join('dem', filename)
    if not os.path.exists(out_path):
        print(f"Downloading {filename} from {dl_url}...")
        try:
            with urllib.request.urlopen(urllib.request.Request(dl_url, headers={'User-Agent': 'Mozilla/5.0'}), context=ctx) as response_dl:
                with open(out_path, 'wb') as f:
                    # chunk based download
                    while True:
                        chunk = response_dl.read(8192 * 10)
                        if not chunk:
                            break
                        f.write(chunk)
            print(f"Downloaded {filename}")
        except Exception as e:
            print(f"Failed to download {filename}: {e}")
    else:
        print(f"Skipping {filename}, already exists")
