import os
import sys
import re
import argparse
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import mapping

# Import our authorization helpers
sys.path.append(os.path.abspath('.'))
sys.path.append(os.path.abspath('extractor'))
from extractor_temporal import (
    get_credentials,
    get_cdse_token,
    get_clean_geometry
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# --- Constants ---
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

EVALSCRIPT_RVI = """
//VERSION=3
function setup() {
  return {
    input: ["VV", "VH"],
    output: { bands: 1, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  let denom = sample.VV + sample.VH;
  let rvi = denom > 0 ? (4.0 * sample.VH) / denom : 0.0;
  return [Math.max(0.0, Math.min(2.0, rvi))];
}
"""

def download_rvi_tif(token, geom_mapping, date_str, lote_id, output_dir):
    """Downloads a single S1 RVI GeoTIFF for a given date."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "image/tiff"
    }

    payload = {
        "input": {
            "bounds": {
                "geometry": geom_mapping,
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}
            },
            "data": [{
                "type": "sentinel-1-grd",
                "dataFilter": {
                    "timeRange": {"from": f"{date_str}T00:00:00Z", "to": f"{date_str}T23:59:59Z"},
                    "acquisitionMode": "IW",
                    "polarization": "DV",
                    "orbitDirection": "DESCENDING",
                    "resolution": "HIGH"
                },
                "processing": {
                    "backCoeff": "GAMMA0_TERRAIN",
                    "orthorectify": True
                }
            }]
        },
        "output": {
            "resx": 0.0001,
            "resy": 0.0001,
            "responses": [{
                "identifier": "default",
                "format": {"type": "image/tiff"}
            }]
        },
        "evalscript": EVALSCRIPT_RVI
    }

    filename = f"{lote_id}_{date_str}_RVI.tif"
    filepath = os.path.join(output_dir, filename)

    if os.path.exists(filepath) and os.path.getsize(filepath) > 1000:
        print(f"  ⏭️ Already exists (skipping): {filename}")
        return True

    print(f"  📥 Downloading: {filename}...")
    try:
        resp = requests.post(PROCESS_URL, headers=headers, json=payload, timeout=60)
        if resp.status_code == 200:
            with open(filepath, "wb") as f:
                f.write(resp.content)
            print(f"    ✅ Saved ({len(resp.content) / 1024:.1f} KB)")
            return True
        else:
            print(f"    ❌ Error {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"    ❌ Connection error: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Descargador de TIFs de RVI automático usando Processing API")
    parser.add_argument("--lote_id", type=str, required=True, help="ID completo del lote (ej: Lote_00_BELETTI_CAVALLO) o búsqueda parcial")
    parser.add_argument("--shp", type=str, default="data/lotes_mani.shp", help="Ruta al shapefile de lotes")
    parser.add_argument("--s1_csv", type=str, default="data/serie_temporal_s1_hybris.csv", help="CSV con las observaciones de radar")
    parser.add_argument("--output_dir", type=str, default="data/tifs_procesados", help="Carpeta de destino de los TIFs")
    args = parser.parse_args()

    print("=" * 72)
    print("🛰️  DESCARGADOR AUTOMÁTICO DE TIFs (PROCESSING API)")
    print("=" * 72)

    # 1. Load S1 CSV and find valid dates for target lot
    if not os.path.exists(args.s1_csv):
        print(f"[ERROR] CSV file not found: {args.s1_csv}. Please run extractor_hybris.py first.")
        return
    df_s1 = pd.read_csv(args.s1_csv)
    
    # Match lot_id (support partial search)
    unique_lotes = df_s1["lote_id"].unique()
    matched_lotes = [l for l in unique_lotes if args.lote_id.upper() in l.upper()]
    
    if not matched_lotes:
        print(f"[ERROR] No matches found for '{args.lote_id}' in {args.s1_csv}.")
        print("Available lots:", unique_lotes)
        return
        
    lote_id = matched_lotes[0]
    print(f"🎯 Matched Lot: {lote_id}")
    
    # Filter dates
    df_filtered = df_s1[(df_s1["lote_id"] == lote_id) & (df_s1["orbita"] == "DESCENDING")]
    dates = sorted(df_filtered["fecha"].unique().tolist())
    
    if not dates:
        print(f"[ERROR] No valid S1 dates found for {lote_id}.")
        return
    print(f"📅 Found {len(dates)} dates to download.")

    # 2. Get Geometry from Shapefile
    if not os.path.exists(args.shp):
        print(f"[ERROR] Shapefile not found: {args.shp}")
        return
    gdf = gpd.read_file(args.shp)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    # Match polygon index
    if "Mani_2425_L" in lote_id:
        match = re.search(r"_L(\d+)", lote_id)
        lote_num_str = match.group(1) if match else ""
        row = None
        for i, r in gdf.iterrows():
            if str(r.get("ID", "")).strip() == lote_num_str.strip():
                row = r
                break
    elif "Soja_2425_L" in lote_id:
        match = re.search(r"_L(\d+)", lote_id)
        lote_num_str = match.group(1) if match else ""
        row = None
        for i, r in gdf.iterrows():
            if str(r.get("id", "")).strip() == lote_num_str.strip():
                row = r
                break
    else:
        match = re.search(r"Lote_(\d+)_", lote_id)
        if match:
            idx = int(match.group(1))
            row = gdf.iloc[idx]
        else:
            row = None
            for i, r in gdf.iterrows():
                camp = str(r.get("Nomb_campo", "")).strip().replace(" ", "_")
                if camp and camp.upper() in lote_id.upper():
                    row = r
                    break
            if row is None:
                print("[ERROR] Could not find geometry in shapefile.")
                return

    geom = get_clean_geometry(row["geometry"])
    geom_mapping = mapping(geom)

    # 3. Authenticate
    username, password = get_credentials()
    if not username or not password:
        print("[ERROR] Credentials not found in .env.")
        return
    token = get_cdse_token(username, password)
    if not token:
        print("[ERROR] Could not authenticate.")
        return
    print("✅ Credentials verified.")

    # 4. Prepare Output Directory
    lote_output_dir = os.path.join(args.output_dir, lote_id)
    os.makedirs(lote_output_dir, exist_ok=True)

    # 5. Download loop
    success_count = 0
    for date in dates:
        if download_rvi_tif(token, geom_mapping, date, lote_id, lote_output_dir):
            success_count += 1

    print(f"\n🎉 Completed! Successfully downloaded {success_count}/{len(dates)} TIFs.")
    print(f"   -> Destination: {os.path.abspath(lote_output_dir)}")

if __name__ == "__main__":
    main()
