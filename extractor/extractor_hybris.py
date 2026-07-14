import os
import sys
import time
import math
import argparse
import pandas as pd
import geopandas as gpd
from shapely.geometry import mapping

# Import credentials and geometry helpers from our existing extractor
sys.path.append(os.path.abspath('.'))
sys.path.append(os.path.abspath('extractor'))
from extractor_temporal import (
    get_credentials,
    get_cdse_token,
    get_clean_geometry,
    query_stats
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# --- Configuration ---
SHP_PATH = "data/lotes_mani.shp"
OUTPUT_S2_CSV = "data/serie_temporal_s2_hybris.csv"
OUTPUT_S1_CSV = "data/serie_temporal_s1_hybris.csv"
START_DATE = "2025-10-01"
END_DATE = "2026-06-30"

# S2 Evalscript: retrieves B02 (Blue), B04 (Red), B08 (NIR), B11 (SWIR1), and SCL
EVALSCRIPT_S2 = """
//VERSION=3
function setup() {
  return {
    input: ["B02", "B04", "B08", "B11", "SCL", "dataMask"],
    output: [
      {id: "default", bands: 5, sampleType: "FLOAT32"},
      {id: "dataMask", bands: 1}
    ]
  };
}
function evaluatePixel(s) {
  let isCloud = s.SCL === 3 || s.SCL === 8 || s.SCL === 9 || s.SCL === 10;
  let mask = s.dataMask && !isCloud ? 1 : 0;
  return {
    default: [s.B02, s.B04, s.B08, s.B11, s.SCL],
    dataMask: [mask]
  };
}
"""

# S1 Evalscript: retrieves VV and VH linear power
EVALSCRIPT_S1 = """
//VERSION=3
function setup() {
  return {
    input: ["VV", "VH", "dataMask"],
    output: [
      {id: "default", bands: 2, sampleType: "FLOAT32"},
      {id: "dataMask", bands: 1}
    ]
  };
}
function evaluatePixel(s) {
  return {
    default: [s.VV, s.VH],
    dataMask: [s.dataMask]
  };
}
"""

def build_s2_payload(geom_mapping, start_date, end_date):
    """Build Statistical API payload for Sentinel-2 (S2L2A) bands."""
    return {
        "input": {
            "bounds": {
                "geometry": geom_mapping,
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}
            },
            "data": [{
                "type": "S2L2A",
                "dataFilter": {"timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"}}
            }]
        },
        "aggregation": {
            "timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"},
            "aggregationInterval": {"of": "P1D"},
            "evalscript": EVALSCRIPT_S2,
            "resx": 0.0001, "resy": 0.0001
        },
        "calculations": {
            "default": {
                "statistics": {"default": {"stDev": True, "mean": True, "min": True, "max": True}}
            }
        }
    }

def build_s1_payload(geom_mapping, start_date, end_date, orbit_direction):
    """Build Statistical API payload for Sentinel-1 GRD (SAR) bands."""
    return {
        "input": {
            "bounds": {
                "geometry": geom_mapping,
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}
            },
            "data": [{
                "type": "sentinel-1-grd",
                "dataFilter": {
                    "timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"},
                    "acquisitionMode": "IW",
                    "polarization": "DV",
                    "orbitDirection": orbit_direction,
                    "resolution": "HIGH"
                },
                "processing": {
                    "backCoeff": "GAMMA0_TERRAIN",
                    "orthorectify": True
                }
            }]
        },
        "aggregation": {
            "timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"},
            "aggregationInterval": {"of": "P1D"},
            "evalscript": EVALSCRIPT_S1,
            "resx": 0.0001, "resy": 0.0001
        },
        "calculations": {
            "default": {
                "statistics": {"default": {"stDev": True, "mean": True, "min": True, "max": True}}
            }
        }
    }

def process_s2_results(data, lote_id):
    """Parse Sentinel-2 Statistical API output into structured records."""
    records = []
    if not data:
        return records

    for item in data:
        interval = item.get("interval", {})
        start_time = interval.get("from")
        if not start_time:
            continue
        date_str = start_time.split("T")[0]

        bands_out = item.get("outputs", {}).get("default", {}).get("bands", {})
        
        # B0: B02, B1: B04, B2: B08, B3: B11, B4: SCL
        # Check reference B0 statistics to see if observation is valid
        b0_stats = bands_out.get("B0", {}).get("stats", {})
        mean_b0 = b0_stats.get("mean")
        if mean_b0 is None or mean_b0 == "NaN" or math.isnan(float(mean_b0)):
            continue

        sample_count = float(b0_stats.get("sampleCount", 0.0))
        no_data_count = float(b0_stats.get("noDataCount", 0.0))
        valid_pixels = sample_count - no_data_count

        # Get mean values for the bands
        b02 = float(bands_out.get("B0", {}).get("stats", {}).get("mean", 0.0))
        b04 = float(bands_out.get("B1", {}).get("stats", {}).get("mean", 0.0))
        b08 = float(bands_out.get("B2", {}).get("stats", {}).get("mean", 0.0))
        b11 = float(bands_out.get("B3", {}).get("stats", {}).get("mean", 0.0))
        
        # Calculate NDVI
        denom_ndvi = b08 + b04
        ndvi = (b08 - b04) / denom_ndvi if denom_ndvi > 0 else 0.0
        
        # Calculate BSI
        denom_bsi = (b11 + b04) + (b08 + b02)
        bsi = ((b11 + b04) - (b08 + b02)) / denom_bsi if denom_bsi > 0 else 0.0

        records.append({
            "lote_id": lote_id,
            "fecha": date_str,
            "sensor": "Sentinel-2",
            "B2": round(b02, 4),
            "B4": round(b04, 4),
            "B8": round(b08, 4),
            "B11": round(b11, 4),
            "NDVI": round(ndvi, 4),
            "BSI": round(bsi, 4),
            "valid_pixels": valid_pixels
        })
    return records

def process_s1_results(data, lote_id, orbit_direction):
    """Parse Sentinel-1 Statistical API output into structured records."""
    records = []
    if not data:
        return records

    for item in data:
        interval = item.get("interval", {})
        start_time = interval.get("from")
        if not start_time:
            continue
        date_str = start_time.split("T")[0]

        bands_out = item.get("outputs", {}).get("default", {}).get("bands", {})
        
        # B0: VV, B1: VH
        b0_stats = bands_out.get("B0", {}).get("stats", {})
        mean_vv = b0_stats.get("mean")
        if mean_vv is None or mean_vv == "NaN" or math.isnan(float(mean_vv)):
            continue

        sample_count = float(b0_stats.get("sampleCount", 0.0))
        no_data_count = float(b0_stats.get("noDataCount", 0.0))
        valid_pixels = sample_count - no_data_count

        vv_lin = float(bands_out.get("B0", {}).get("stats", {}).get("mean", 0.0))
        vh_lin = float(bands_out.get("B1", {}).get("stats", {}).get("mean", 0.0))
        
        # Calculate RVI
        denom_rvi = vv_lin + vh_lin
        rvi = (4.0 * vh_lin) / denom_rvi if denom_rvi > 0 else 0.0
        
        # Convert linear power backscatter to dB (log scale)
        vv_db = 10 * math.log10(vv_lin) if vv_lin > 0 else -99.0
        vh_db = 10 * math.log10(vh_lin) if vh_lin > 0 else -99.0
        
        # Calculate VH_VV difference in dB (equivalent to log-ratio of VH/VV)
        vh_vv = vh_db - vv_db if vv_lin > 0 and vh_lin > 0 else 0.0

        records.append({
            "lote_id": lote_id,
            "fecha": date_str,
            "sensor": "Sentinel-1",
            "orbita": orbit_direction,
            "VV": round(vv_db, 4),
            "VH": round(vh_db, 4),
            "VH_VV": round(vh_vv, 4),
            "RVI": round(rvi, 4),
            "valid_pixels": valid_pixels
        })
    return records

def main():
    parser = argparse.ArgumentParser(description="Extractor multibanda S1+S2 HyBRIS-like")
    parser.add_argument("--lote", default=None,
                        help="Filtra a un solo lote (match parcial por id/campo). Vacío = todos los lotes.")
    parser.add_argument("--shp", default="data/lotes_mani.shp",
                        help="Ruta al archivo shapefile (por defecto data/lotes_mani.shp)")
    args = parser.parse_args()

    print("=" * 72)
    print("🛰️  EXTRACTOR MULTIBANDA S1 + S2 HYBRIS-LIKE")
    print("=" * 72)

    # 1. Credentials
    username, password = get_credentials()
    if not username or not password:
        print("[ERROR] Credentials not found in .env.")
        return
    print(f"🔑 Authenticating as: {username[:4]}***...")
    token = get_cdse_token(username, password)
    if not token:
        print("[ERROR] Could not authenticate.")
        return
    print("✅ Authentication successful.")

    # 2. Load shapefile
    if not os.path.exists(args.shp):
        print(f"[ERROR] Shapefile not found: {args.shp}")
        return
    gdf = gpd.read_file(args.shp)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")
    print(f"✅ Loaded {len(gdf)} lots.")

    all_s2_records = []
    all_s1_records = []

    for idx, row in gdf.iterrows():
        # Auto-detect shapefile fields to match database names
        if "ID" in row and "Nomb_campo" not in row:
            lote_id = f"Mani_2425_L{row['ID']}"
        elif "id" in row and "Nomb_campo" not in row:
            lote_id = f"Soja_2425_L{row['id']}"
        else:
            camp_name = str(row.get("Nomb_campo", "Unknown")).strip().replace(" ", "_")
            lote_num = str(row.get("Lote", "None")).strip()
            lote_id = f"Lote_{idx:02d}_{camp_name}"
            if lote_num and lote_num not in ("None", "nan"):
                lote_id += f"_L{lote_num}"

        # Optional single-lot filter to keep test extractions cheap (fewer PU).
        if args.lote and args.lote.upper() not in lote_id.upper():
            continue

        print(f"\n[{idx+1}/{len(gdf)}] Processing: {lote_id}...")
        geom = get_clean_geometry(row["geometry"])
        geom_mapping = mapping(geom)

        # A. Query Sentinel-2 (BSI, NDVI, bands)
        print("  📸 Querying Sentinel-2 (B2, B4, B8, B11)...")
        s2_payload = build_s2_payload(geom_mapping, START_DATE, END_DATE)
        s2_raw = query_stats(token, s2_payload)
        s2_records = process_s2_results(s2_raw, lote_id)
        print(f"     -> Found {len(s2_records)} valid Sentinel-2 observations.")
        all_s2_records.extend(s2_records)

        # B. Query Sentinel-1 (VV, VH, VH_VV, RVI)
        for orbit in ("DESCENDING", "ASCENDING"):
            print(f"  📡 Querying Sentinel-1 SAR ({orbit})...")
            s1_payload = build_s1_payload(geom_mapping, START_DATE, END_DATE, orbit)
            s1_raw = query_stats(token, s1_payload)
            s1_records = process_s1_results(s1_raw, lote_id, orbit)
            print(f"     -> Found {len(s1_records)} valid SAR observations.")
            all_s1_records.extend(s1_records)

    # 3. Export to CSVs
    if all_s2_records:
        df_s2_new = pd.DataFrame(all_s2_records)
        if os.path.exists(OUTPUT_S2_CSV):
            df_s2_old = pd.read_csv(OUTPUT_S2_CSV)
            lotes_nuevos = df_s2_new["lote_id"].unique()
            df_s2_old = df_s2_old[~df_s2_old["lote_id"].isin(lotes_nuevos)]
            df_s2 = pd.concat([df_s2_old, df_s2_new])
        else:
            df_s2 = df_s2_new
        df_s2 = df_s2.sort_values(["lote_id", "fecha"]).reset_index(drop=True)
        df_s2.to_csv(OUTPUT_S2_CSV, index=False)
        print(f"\n✅ Sentinel-2 data saved/merged to: {OUTPUT_S2_CSV} (Total records: {len(df_s2)})")
    else:
        print("\n[WARN] No Sentinel-2 data extracted.")

    if all_s1_records:
        df_s1_new = pd.DataFrame(all_s1_records)
        if os.path.exists(OUTPUT_S1_CSV):
            df_s1_old = pd.read_csv(OUTPUT_S1_CSV)
            lotes_nuevos = df_s1_new["lote_id"].unique()
            df_s1_old = df_s1_old[~df_s1_old["lote_id"].isin(lotes_nuevos)]
            df_s1 = pd.concat([df_s1_old, df_s1_new])
        else:
            df_s1 = df_s1_new
        df_s1 = df_s1.sort_values(["lote_id", "orbita", "fecha"]).reset_index(drop=True)
        df_s1.to_csv(OUTPUT_S1_CSV, index=False)
        print(f"✅ Sentinel-1 data saved/merged to: {OUTPUT_S1_CSV} (Total records: {len(df_s1)})")
    else:
        print("[WARN] No Sentinel-1 data extracted.")

    print("\n🎉 EXTRACTION COMPLETE!")

if __name__ == "__main__":
    main()
