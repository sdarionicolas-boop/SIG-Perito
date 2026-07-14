import os
import sys
import requests
import json
import calendar
import math
import time
import pandas as pd
import geopandas as gpd
from shapely.geometry import mapping
from datetime import datetime

# Configure standard output to use UTF-8 to prevent encoding errors on Windows cp1252
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

def get_credentials():
    """Retrieve CDSE credentials from env or .env files."""
    env_paths = [
        ".env",
        r"C:\Users\sdari\Desktop\COSECHA\.env",
        r"C:\Users\sdari\Desktop\AgroIA_RAG HACKATON COPERNICUS\config\.env"
    ]
    
    creds = {}
    for path in env_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        if "=" in line and not line.startswith("#"):
                            k, v = line.strip().split("=", 1)
                            creds[k.strip()] = v.strip().strip('"').strip("'")
                if "EODAG__COP_DATASPACE__AUTH__CREDENTIALS__USERNAME" in creds:
                    break
            except Exception as e:
                print(f"[WARN] Error reading {path}: {e}")
                
    username = creds.get("EODAG__COP_DATASPACE__AUTH__CREDENTIALS__USERNAME") or os.environ.get("EODAG__COP_DATASPACE__AUTH__CREDENTIALS__USERNAME")
    password = creds.get("EODAG__COP_DATASPACE__AUTH__CREDENTIALS__PASSWORD") or os.environ.get("EODAG__COP_DATASPACE__AUTH__CREDENTIALS__PASSWORD")
    
    return username, password

def get_cdse_token(username, password):
    """Authenticate and get access token for CDSE."""
    auth_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    auth_data = {
        "client_id": "cdse-public",
        "grant_type": "password",
        "username": username,
        "password": password,
    }
    
    for attempt in range(1, 4):
        try:
            resp = requests.post(auth_url, data=auth_data, timeout=30)
            if resp.status_code == 200:
                return resp.json().get('access_token')
            else:
                print(f"[WARN] Auth attempt {attempt}/3 failed (HTTP {resp.status_code}): {resp.text[:150]}")
        except Exception as e:
            print(f"[WARN] Auth attempt {attempt}/3 error: {e}")
        time.sleep(2)
    return None

def get_clean_geometry(geom):
    """Repair invalid geometries and handle geometry collections."""
    if not geom.is_valid:
        geom = geom.buffer(0)
        
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        if polys:
            from shapely.ops import unary_union
            geom = unary_union(polys)
            
    # Try one more time if needed
    if not geom.is_valid:
        geom = geom.buffer(0.00001).buffer(-0.00001)
        
    return geom

def build_s2_payload(geom_mapping, start_date, end_date):
    """Build Sentinel Hub Statistical API payload for Sentinel-2 (S2L2A)."""
    evalscript = """
    //VERSION=3
    function setup() {
      return {
        input: ["B04", "B08", "SCL", "dataMask"],
        output: [
          {id: "default", bands: 1, sampleType: "FLOAT32"},
          {id: "dataMask", bands: 1}
        ]
      };
    }
    function evaluatePixel(samples) {
      let ndvi = (samples.B08 - samples.B04) / (samples.B08 + samples.B04);
      // SCL values: 3=cloud shadow, 8=cloud medium prob, 9=cloud high prob, 10=cirrus
      let isCloud = samples.SCL === 3 || samples.SCL === 8 || samples.SCL === 9 || samples.SCL === 10;
      let mask = samples.dataMask && !isCloud ? 1 : 0;
      return { default: [ndvi], dataMask: [mask] };
    }
    """
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
            "evalscript": evalscript,
            "resx": 0.0001, "resy": 0.0001
        },
        "calculations": {
            "default": {
                "statistics": {"default": {"stDev": True, "mean": True, "min": True, "max": True}}
            }
        }
    }

def build_landsat_payload(geom_mapping, start_date, end_date):
    """Build Sentinel Hub Statistical API payload for Landsat 8-9 L1."""
    evalscript = """
    //VERSION=3
    function setup() {
      return {
        input: [{
          bands: ["B04", "B05", "BQA", "dataMask"],
          units: ["REFLECTANCE", "REFLECTANCE", "DN", "DN"]
        }],
        output: [
          {id: "default", bands: 1, sampleType: "FLOAT32"},
          {id: "dataMask", bands: 1}
        ]
      };
    }
    function evaluatePixel(samples) {
      let qa = samples.BQA;
      // QA_PIXEL bits Collection 2: 
      // 1=dilated_cloud, 2=cirrus, 3=cloud, 4=shadow, 5=snow
      let isCloud = (qa & (1 << 3)) !== 0;
      let isShadow = (qa & (1 << 4)) !== 0;
      let isDilated = (qa & (1 << 1)) !== 0;
      let isCirrus = (qa & (1 << 2)) !== 0;
      let isSnow = (qa & (1 << 5)) !== 0;
      
      let bad = isCloud || isShadow || isDilated || isCirrus || isSnow;
      
      let ndvi = (samples.B05 - samples.B04) / (samples.B05 + samples.B04);
      let mask = samples.dataMask && !bad ? 1 : 0;
      
      return { default: [ndvi], dataMask: [mask] };
    }
    """
    return {
        "input": {
            "bounds": {
                "geometry": geom_mapping,
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}
            },
            "data": [{
                "type": "landsat-ot-l1",
                "dataFilter": {"timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"}}
            }]
        },
        "aggregation": {
            "timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"},
            "aggregationInterval": {"of": "P1D"},
            "evalscript": evalscript,
            "resx": 0.00027, "resy": 0.00027
        },
        "calculations": {
            "default": {
                "statistics": {"default": {"stDev": True, "mean": True, "min": True, "max": True}}
            }
        }
    }

def build_s1_payload(geom_mapping, start_date, end_date, orbit_direction):
    """Build Sentinel Hub Statistical API payload for Sentinel-1 GRD (SAR).

    Computes the Radar Vegetation Index (RVI = 4*VH / (VV + VH)) over linear
    power backscatter. RVI is the SAR analogue of NDVI: high for dense green
    canopy, dropping when the crop is arrancado/trillado (harvest) because the
    surface roughness and structure change. Cloud-independent.

    orbit_direction must be "ASCENDING" or "DESCENDING" and is kept separate:
    different incidence angles are not directly comparable, so each orbit is its
    own time series.
    """
    evalscript = """
    //VERSION=3
    function setup() {
      return {
        input: ["VV", "VH", "dataMask"],
        output: [
          {id: "default", bands: 1, sampleType: "FLOAT32"},
          {id: "dataMask", bands: 1}
        ]
      };
    }
    function evaluatePixel(samples) {
      // RVI over linear power units. Guard against null/zero denominator.
      let denom = samples.VV + samples.VH;
      let rvi = denom > 0 ? (4.0 * samples.VH) / denom : 0.0;
      // Clamp to the physical RVI range [0, ~1.3+]; cap defensively at 0..2
      rvi = Math.max(0.0, Math.min(2.0, rvi));
      return { default: [rvi], dataMask: [samples.dataMask] };
    }
    """
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
            "evalscript": evalscript,
            "resx": 0.0001, "resy": 0.0001
        },
        "calculations": {
            "default": {
                "statistics": {"default": {"stDev": True, "mean": True, "min": True, "max": True}}
            }
        }
    }


def process_s1_results(data, orbit_direction):
    """Parse Sentinel-1 Statistical API output into RVI record dictionaries."""
    records = {}
    if not data:
        return records

    for item in data:
        interval = item.get("interval", {})
        start_time = interval.get("from")
        if not start_time:
            continue

        date_str = start_time.split("T")[0]

        outputs = item.get("outputs", {})
        default_out = outputs.get("default", {})
        stats = default_out.get("bands", {}).get("B0", {}).get("stats", {})

        mean = stats.get("mean")
        if mean is None or mean == "NaN" or math.isnan(float(mean)):
            continue

        sample_count = float(stats.get("sampleCount", 0.0))
        no_data_count = float(stats.get("noDataCount", 0.0))
        valid_pixels = sample_count - no_data_count

        records[date_str] = {
            "sensor": "Sentinel-1",
            "orbita": orbit_direction,
            "rvi_medio": round(float(mean), 4),
            "rvi_std": round(float(stats.get("stDev", 0.0)), 4),
            "rvi_min": round(float(stats.get("min", 0.0)), 4),
            "rvi_max": round(float(stats.get("max", 0.0)), 4),
            "valid_pixels": valid_pixels
        }
    return records


def query_stats(token, payload):
    """Send request to Sentinel Hub Statistical API."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    url = "https://sh.dataspace.copernicus.eu/api/v1/statistics"
    
    for attempt in range(1, 4):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=45)
            if resp.status_code == 200:
                return resp.json().get("data", [])
            else:
                print(f"[WARN] Stats request attempt {attempt}/3 failed (HTTP {resp.status_code}): {resp.text[:150]}")
        except Exception as e:
            print(f"[WARN] Stats request attempt {attempt}/3 error: {e}")
        time.sleep(3)
    return None

def process_results(data, sensor_name):
    """Parse output from Statistical API into raw record dictionaries."""
    records = {}
    if not data:
        return records
        
    for item in data:
        interval = item.get("interval", {})
        start_time = interval.get("from")
        if not start_time:
            continue
            
        # Format date as YYYY-MM-DD
        date_str = start_time.split("T")[0]
        
        outputs = item.get("outputs", {})
        default_out = outputs.get("default", {})
        stats = default_out.get("bands", {}).get("B0", {}).get("stats", {})
        
        mean = stats.get("mean")
        if mean is None or mean == "NaN" or math.isnan(float(mean)):
            continue
            
        sample_count = float(stats.get("sampleCount", 0.0))
        no_data_count = float(stats.get("noDataCount", 0.0))
        valid_pixels = sample_count - no_data_count
        
        mean_val = float(mean)
        std_val = float(stats.get("stDev", 0.0))
        min_val = float(stats.get("min", 0.0))
        max_val = float(stats.get("max", 0.0))
        
        # Apply Landsat-to-S2 calibration if Landsat
        if sensor_name == "Landsat":
            # Skakun et al. 2018 calibration: NDVI_S2 = 0.0246 + 0.9712 * NDVI_L8
            mean_val = 0.0246 + 0.9712 * mean_val
            min_val = 0.0246 + 0.9712 * min_val
            max_val = 0.0246 + 0.9712 * max_val
            std_val = 0.9712 * std_val
            
        records[date_str] = {
            "sensor": sensor_name,
            "ndvi_medio": round(mean_val, 4),
            "ndvi_std": round(std_val, 4),
            "ndvi_min": round(min_val, 4),
            "ndvi_max": round(max_val, 4),
            "valid_pixels": valid_pixels
        }
    return records

def run_extraction(min_valid_pct=70.0):
    print("=" * 72)
    print("🛰️  S2 + LANDSAT TEMPORAL SERIES HARMONIZED EXTRACTOR")
    print("=" * 72)
    
    # 1. Credentials
    username, password = get_credentials()
    if not username or not password:
        print("[ERROR] Credentials not found in any .env file or env variables.")
        return
        
    print(f"🔑 Authenticating as: {username[:4]}***...")
    token = get_cdse_token(username, password)
    if not token:
        print("[ERROR] Could not get CDSE Token. Please check credentials.")
        return
    print("✅ Authentication successful.")
    
    # 2. Load shapefile
    shp_path = "lotes_mani.shp"
    if not os.path.exists(shp_path):
        print(f"[ERROR] Shapefile not found: {shp_path}")
        return
        
    print(f"📂 Loading shapefile: {shp_path}...")
    gdf = gpd.read_file(shp_path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")
        
    print(f"✅ Loaded {len(gdf)} lots.")
    
    # Define period of interest
    start_date = "2025-10-01"
    end_date = "2026-05-31"
    print(f"📅 Temporal Range: {start_date} to {end_date}")
    print(f"⚙️  Minimum valid pixel threshold: {min_valid_pct}%")
    
    all_merged_records = []
    all_sar_records = []

    for idx, row in gdf.iterrows():
        # Create unique lot ID
        camp_name = str(row.get("Nomb_campo", "Unknown")).strip().replace(" ", "_")
        lote_num = str(row.get("Lote", "None")).strip()
        lote_id = f"Lote_{idx:02d}_{camp_name}"
        if lote_num and lote_num != "None" and lote_num != "nan":
            lote_id += f"_L{lote_num}"
            
        print(f"\n[{idx+1}/{len(gdf)}] Processing: {lote_id}...")
        geom = row["geometry"]
        clean_geom = get_clean_geometry(geom)
        geom_mapping = mapping(clean_geom)
        
        # A. Query Sentinel-2 L2A
        print(f"  🛰️  Querying Sentinel-2 L2A...")
        s2_payload = build_s2_payload(geom_mapping, start_date, end_date)
        s2_raw = query_stats(token, s2_payload)
        s2_data = process_results(s2_raw, "Sentinel-2")
        print(f"     -> Found {len(s2_data)} raw observations.")
        
        # B. Query Landsat 8-9 L1
        print(f"  🛰️  Querying Landsat 8-9 L1...")
        l8_payload = build_landsat_payload(geom_mapping, start_date, end_date)
        l8_raw = query_stats(token, l8_payload)
        l8_data = process_results(l8_raw, "Landsat")
        print(f"     -> Found {len(l8_data)} raw observations.")
        
        # Calculate maximum valid pixels found for each sensor to establish relative cloud-free denominator
        max_s2_valid = max([r["valid_pixels"] for r in s2_data.values()], default=0.0)
        max_l8_valid = max([r["valid_pixels"] for r in l8_data.values()], default=0.0)
        
        # Assign relative pct_valido
        for date_str, rec in s2_data.items():
            rec["pct_valido"] = round(rec["valid_pixels"] / max_s2_valid * 100.0, 1) if max_s2_valid > 0 else 0.0
            
        for date_str, rec in l8_data.items():
            rec["pct_valido"] = round(rec["valid_pixels"] / max_l8_valid * 100.0, 1) if max_l8_valid > 0 else 0.0
            
        # C. Filter and Merge
        all_dates = sorted(list(set(list(s2_data.keys()) + list(l8_data.keys()))))
        
        lote_records_count = 0
        for date_str in all_dates:
            s2_rec = s2_data.get(date_str)
            l8_rec = l8_data.get(date_str)
            
            chosen_rec = None
            # Prioritize Sentinel-2
            if s2_rec and s2_rec["pct_valido"] >= min_valid_pct:
                chosen_rec = s2_rec
            # Fall back to Landsat 8-9
            elif l8_rec and l8_rec["pct_valido"] >= min_valid_pct:
                chosen_rec = l8_rec
                
            if chosen_rec:
                all_merged_records.append({
                    "lote_id": lote_id,
                    "fecha": date_str,
                    "sensor": chosen_rec["sensor"],
                    "ndvi_medio": chosen_rec["ndvi_medio"],
                    "ndvi_std": chosen_rec["ndvi_std"],
                    "ndvi_min": chosen_rec["ndvi_min"],
                    "ndvi_max": chosen_rec["ndvi_max"],
                    "pct_valido": chosen_rec["pct_valido"]
                })
                lote_records_count += 1
                
        print(f"  ✅ Done. Extracted {lote_records_count} valid harmonized dates (S2 + Landsat fallback).")

        # D. Query Sentinel-1 GRD (SAR) — cloud-independent harvest confirmation.
        # Ascending and descending orbits are kept as separate series.
        sar_lote_count = 0
        for orbit in ("ASCENDING", "DESCENDING"):
            print(f"  📡 Querying Sentinel-1 GRD ({orbit})...")
            s1_payload = build_s1_payload(geom_mapping, start_date, end_date, orbit)
            s1_raw = query_stats(token, s1_payload)
            s1_data = process_s1_results(s1_raw, orbit)
            print(f"     -> Found {len(s1_data)} raw SAR observations.")

            max_s1_valid = max([r["valid_pixels"] for r in s1_data.values()], default=0.0)
            for date_str in sorted(s1_data.keys()):
                rec = s1_data[date_str]
                pct = round(rec["valid_pixels"] / max_s1_valid * 100.0, 1) if max_s1_valid > 0 else 0.0
                if pct < min_valid_pct:
                    continue
                all_sar_records.append({
                    "lote_id": lote_id,
                    "fecha": date_str,
                    "sensor": rec["sensor"],
                    "orbita": rec["orbita"],
                    "rvi_medio": rec["rvi_medio"],
                    "rvi_std": rec["rvi_std"],
                    "rvi_min": rec["rvi_min"],
                    "rvi_max": rec["rvi_max"],
                    "pct_valido": pct
                })
                sar_lote_count += 1
        print(f"  ✅ Done. Extracted {sar_lote_count} valid SAR dates (S1 asc+desc).")
        
    if not all_merged_records:
        print("\n❌ No valid data extracted for any lot. CSV was not generated.")
        return
        
    # Create DataFrame and save CSV
    df_out = pd.DataFrame(all_merged_records)
    df_out = df_out.sort_values(by=["lote_id", "fecha"]).reset_index(drop=True)
    
    csv_filename = "serie_temporal_lotes.csv"
    df_out.to_csv(csv_filename, index=False)
    print(f"\n🎉 SUCCESS! Harmonized time series saved to: {csv_filename}")
    print(f"     Total records: {len(df_out)}")
    print(f"     Sentinel-2 records: {len(df_out[df_out['sensor'] == 'Sentinel-2'])}")
    print(f"     Landsat records: {len(df_out[df_out['sensor'] == 'Landsat'])}")

    # Write the SAR (Sentinel-1) companion series to its own CSV. Kept separate
    # because RVI has a different scale/cadence than NDVI and each orbit is its
    # own series — it is fused with the optical signal in analizar_cosecha.py.
    if all_sar_records:
        df_sar = pd.DataFrame(all_sar_records).sort_values(
            by=["lote_id", "orbita", "fecha"]
        ).reset_index(drop=True)
        sar_filename = "serie_temporal_sar_lotes.csv"
        df_sar.to_csv(sar_filename, index=False)
        print(f"\n📡 SAR time series saved to: {sar_filename}")
        print(f"     Total SAR records: {len(df_sar)}")
        print(f"     Ascending: {len(df_sar[df_sar['orbita'] == 'ASCENDING'])}")
        print(f"     Descending: {len(df_sar[df_sar['orbita'] == 'DESCENDING'])}")
    else:
        print("\n[WARN] No valid Sentinel-1 SAR data extracted.")
    
if __name__ == "__main__":
    min_pct = 70.0
    if len(sys.argv) > 1:
        try:
            min_pct = float(sys.argv[1])
        except ValueError:
            pass
    run_extraction(min_pct)
