import os
import sys
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import mapping

# Import authorization and geometry helpers from our existing extractor
from extractor_temporal import (
    get_credentials,
    get_cdse_token,
    get_clean_geometry,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# --- Configuration ---
SHP_PATH = "lotes_mani.shp"
LOTE_OBJETIVO = "BELETTI_CAVALLO"
OUTPUT_DIR = "tiff_belleti_60"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

# Define default fallback products to download (overridden dynamically for S2 and S1)
S2_DATES = ["2026-03-29", "2026-05-03", "2026-05-08", "2026-05-18"]
S1_DATES = ["2026-05-02", "2026-05-14", "2026-05-26"]

# Evalscripts
EVALSCRIPT_TCI = """
//VERSION=3
function setup() {
  return {
    input: ["B04", "B03", "B02"],
    output: { bands: 3, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  // Return raw reflectance (0-1). Specialist can scale or stretch it.
  return [sample.B04, sample.B03, sample.B02];
}
"""

EVALSCRIPT_NDVI = """
//VERSION=3
function setup() {
  return {
    input: ["B08", "B04"],
    output: { bands: 1, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  let denom = sample.B08 + sample.B04;
  return denom > 0 ? [(sample.B08 - sample.B04) / denom] : [0.0];
}
"""

EVALSCRIPT_MSAVI = """
//VERSION=3
function setup() {
  return {
    input: ["B08", "B04"],
    output: { bands: 1, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  let nir = sample.B08;
  let red = sample.B04;
  let inner = Math.pow(2.0 * nir + 1.0, 2.0) - 8.0 * (nir - red);
  let msavi = inner >= 0 ? (2.0 * nir + 1.0 - Math.sqrt(inner)) / 2.0 : 0.0;
  return [msavi];
}
"""

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

EVALSCRIPT_MULTIBAND = """
//VERSION=3
function setup() {
  return {
    input: ["B01","B02","B03","B04","B05","B06","B07","B08","B8A","B09","B11","B12"],
    output: { bands: 12, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(s) {
  return [s.B01, s.B02, s.B03, s.B04, s.B05, s.B06, s.B07, s.B08, s.B8A, s.B09, s.B11, s.B12];
}
"""

EVALSCRIPT_SAR_POLARIZED = """
//VERSION=3
function setup() {
  return {
    input: ["VV", "VH"],
    output: { bands: 2, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  return [sample.VV, sample.VH];
}
"""



def download_tif(token, geom_mapping, sensor_type, date_str, product_name, evalscript):
    """Call CDSE Process API to download a clipped GeoTIFF."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "image/tiff"
    }

    # Setup the data sources based on the sensor
    if sensor_type == "S2L2A":
        data_source = {
            "type": "S2L2A",
            "dataFilter": {
                "timeRange": {
                    "from": f"{date_str}T00:00:00Z",
                    "to": f"{date_str}T23:59:59Z"
                }
            }
        }
    elif sensor_type == "S1GRD":
        data_source = {
            "type": "sentinel-1-grd",
            "dataFilter": {
                "timeRange": {
                    "from": f"{date_str}T00:00:00Z",
                    "to": f"{date_str}T23:59:59Z"
                },
                "acquisitionMode": "IW",
                "polarization": "DV",
                "orbitDirection": "DESCENDING",
                "resolution": "HIGH"
            },
            "processing": {
                "backCoeff": "GAMMA0_TERRAIN",
                "orthorectify": True
            }
        }
    else:
        raise ValueError(f"Unknown sensor: {sensor_type}")

    payload = {
        "input": {
            "bounds": {
                "geometry": geom_mapping,
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}
            },
            "data": [data_source]
        },
        "output": {
            "resx": 0.0001,
            "resy": 0.0001,
            "responses": [{
                "identifier": "default",
                "format": {"type": "image/tiff"}
            }]
        },
        "evalscript": evalscript
    }

    filename = f"{LOTE_OBJETIVO}_{date_str}_{product_name}.tif"
    filepath = os.path.join(OUTPUT_DIR, filename)

    # Check if file already exists and is valid
    if os.path.exists(filepath) and os.path.getsize(filepath) > 1000:
        print(f"  ⏭️ Already exists (skipping): {filename}")
        return True

    print(f"  📥 Downloading: {filename}...")
    try:
        resp = requests.post(PROCESS_URL, headers=headers, json=payload, timeout=60)
        if resp.status_code == 200:
            # Check if we got an empty TIFF or error in body
            if len(resp.content) < 1000:
                print(f"    [WARN] Output file is unusually small ({len(resp.content)} bytes). S2 pass might not cover the lot on this day.")
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
    print("=" * 72)
    print("CDSE GEOTIFF CLIP & DOWNLOADER")
    print("=" * 72)

    # 1. Credentials
    username, password = get_credentials()
    if not username or not password:
        print("[ERROR] Credentials not found.")
        return
    print(f"🔑 Authenticating as: {username[:4]}***...")
    token = get_cdse_token(username, password)
    if not token:
        print("[ERROR] Could not authenticate.")
        return
    print("✅ Authentication successful.")

    # 2. Destination Directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 3. Load Shapefile and Find Lot
    if not os.path.exists(SHP_PATH):
        print(f"[ERROR] Shapefile not found: {SHP_PATH}")
        return
    gdf = gpd.read_file(SHP_PATH)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    fila_objetivo = None
    for idx, row in gdf.iterrows():
        camp_name = str(row.get("Nomb_campo", "Unknown")).strip().replace(" ", "_")
        lote_num = str(row.get("Lote", "None")).strip()
        lote_id = f"Lote_{idx:02d}_{camp_name}"
        if lote_num and lote_num not in ("None", "nan"):
            lote_id += f"_L{lote_num}"
        if LOTE_OBJETIVO.upper() in lote_id.upper():
            fila_objetivo = (lote_id, row)
            break

    if fila_objetivo is None:
        print(f"[ERROR] Lot contaning '{LOTE_OBJETIVO}' not found in Shapefile.")
        return

    lote_id, row = fila_objetivo
    print(f"🎯 Target Lot: {lote_id}")
    geom = get_clean_geometry(row["geometry"])
    geom_mapping = mapping(geom)

    # 4. Load S2 Dates dynamically from bandas_indices_beletti.csv if available
    csv_path = "bandas_indices_beletti.csv"
    global S2_DATES
    if os.path.exists(csv_path):
        try:
            df_csv = pd.read_csv(csv_path)
            S2_DATES = sorted(df_csv["fecha"].unique().tolist())
            print(f"Loaded {len(S2_DATES)} S2 dates dynamically from {csv_path}.")
        except Exception as e:
            print(f"[WARN] Error reading {csv_path}: {e}. Using default S2 dates.")
    else:
        print(f"[WARN] {csv_path} not found. Using default S2 dates.")

    # 4b. Load S1 Dates dynamically from serie_temporal_completa_beletti_60.csv if available
    s1_csv_path = "serie_temporal_completa_beletti_60.csv"
    global S1_DATES
    if os.path.exists(s1_csv_path):
        try:
            df_s1 = pd.read_csv(s1_csv_path)
            df_s1_filtered = df_s1[df_s1["sensor"].str.lower() == "sentinel-1"]
            S1_DATES = sorted(df_s1_filtered["fecha"].unique().tolist())
            print(f"Loaded {len(S1_DATES)} S1 dates dynamically from {s1_csv_path}.")
        except Exception as e:
            print(f"[WARN] Error reading {s1_csv_path}: {e}. Using default S1 dates.")
    else:
        print(f"[WARN] {s1_csv_path} not found. Using default S1 dates.")

    # 5. Download Sentinel-2 products
    print(f"\n📸 [Sentinel-2] Requesting {len(S2_DATES)} dates...")
    s2_products = [
        ("TCI", EVALSCRIPT_TCI),
        ("NDVI", EVALSCRIPT_NDVI),
        ("MSAVI", EVALSCRIPT_MSAVI),
        ("MULTIBANDA", EVALSCRIPT_MULTIBAND)
    ]
    for date in S2_DATES:
        print(f"📅 Date: {date}")
        for prod_name, script in s2_products:
            download_tif(token, geom_mapping, "S2L2A", date, prod_name, script)

    # 6. Download Sentinel-1 products
    print(f"\n📡 [Sentinel-1 SAR] Requesting {len(S1_DATES)} dates...")
    for date in S1_DATES:
        print(f"📅 Date: {date}")
        download_tif(token, geom_mapping, "S1GRD", date, "RVI", EVALSCRIPT_RVI)
        download_tif(token, geom_mapping, "S1GRD", date, "VV_VH", EVALSCRIPT_SAR_POLARIZED)

    # 7. Copy CSVs to output directory
    import shutil
    for csv_f in [csv_path, s1_csv_path]:
        if os.path.exists(csv_f):
            try:
                shutil.copy(csv_f, os.path.join(OUTPUT_DIR, csv_f))
                print(f"Copied {csv_f} to {OUTPUT_DIR}")
            except Exception as e:
                print(f"[WARN] Could not copy {csv_f} to {OUTPUT_DIR}: {e}")
                print("Please make sure the file is not open in Excel or another program.")

    print("\n🎉 Process complete. Check the directory:", os.path.abspath(OUTPUT_DIR))

if __name__ == "__main__":
    main()
