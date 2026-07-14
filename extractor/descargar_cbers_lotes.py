import os
import sys
import math
import requests
import rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds
import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import mapping

# Import credentials and geometry helpers from extractor_temporal
sys.path.append(os.path.abspath('.'))
from extractor_temporal import get_clean_geometry

OUTPUT_DIR = "tiff_cbers_abril_mayo"
SHP_PATH = "lotes_mani.shp"

BAND_MAP = {
    "AMAZONIA1-WFI": {"red": "B3", "nir": "B4"},
    "CBERS4A-WFI": {"red": "B15", "nir": "B16"},
    "CBERS4-AWFI": {"red": "B15", "nir": "B16"},
    "CBERS4-MUX": {"red": "B7", "nir": "B8"}
}

def s3_to_http(s3_url):
    if s3_url.startswith("s3://brazil-eosats/"):
        return s3_url.replace("s3://brazil-eosats/", "https://brazil-eosats.s3.amazonaws.com/")
    elif s3_url.startswith("s3://"):
        # Generic fallback
        parts = s3_url.replace("s3://", "").split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""
        return f"https://{bucket}.s3.amazonaws.com/{key}"
    return s3_url

def main():
    print("=" * 72)
    print("CBERS / AMAZONIA-1 DATA EXTRACTOR & CLIPPER (APRIL - MAY 2026)")
    print("=" * 72)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. Load shapefile
    if not os.path.exists(SHP_PATH):
        print(f"[ERROR] Shapefile not found: {SHP_PATH}")
        return
    gdf = gpd.read_file(SHP_PATH)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")
    print(f"Loaded {len(gdf)} lots from shapefile.")

    # 2. Get bbox for STAC search
    bounds = gdf.total_bounds
    bbox = [float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3])]
    print(f"Overall bounding box (lon/lat): {bbox}")

    # 3. Query STAC search
    stac_url = "https://stac.scitekno.com.br/v100/search"
    payload = {
        "bbox": bbox,
        "datetime": "2026-04-01T00:00:00Z/2026-05-31T23:59:59Z",
        "limit": 100
    }
    
    print("Querying CBERS/Amazonia STAC API...")
    try:
        resp = requests.post(stac_url, json=payload, timeout=30)
        if resp.status_code != 200:
            print(f"[ERROR] STAC API returned code {resp.status_code}: {resp.text}")
            return
        features = resp.json().get("features", [])
        print(f"Found {len(features)} total scenes in range.")
    except Exception as e:
        print(f"[ERROR] Could not query STAC API: {e}")
        return

    # Filter features that are in our BAND_MAP
    valid_features = []
    for feat in features:
        col = feat.get("collection")
        if col in BAND_MAP:
            valid_features.append(feat)
    
    print(f"Filtered to {len(valid_features)} multispectral scenes (WFI, AWFI, MUX).")
    
    # Sort valid features by date
    valid_features = sorted(valid_features, key=lambda x: x.get("properties", {}).get("datetime", ""))

    csv_records = []

    # 4. Loop over each scene and clip for each lot
    for idx_feat, feat in enumerate(valid_features):
        properties = feat.get("properties", {})
        col = feat.get("collection")
        scene_date = properties.get("datetime", "").split("T")[0]
        scene_id = feat.get("id")
        
        print(f"\n[{idx_feat+1}/{len(valid_features)}] Processing scene: {scene_id} ({col} on {scene_date})...")
        
        # Get band links
        assets = feat.get("assets", {})
        red_key = BAND_MAP[col]["red"]
        nir_key = BAND_MAP[col]["nir"]
        
        red_asset = assets.get(red_key)
        nir_asset = assets.get(nir_key)
        
        if not red_asset or not nir_asset:
            print(f"  [WARN] Red or NIR bands not found for this scene. Skipping.")
            continue
            
        red_url = s3_to_http(red_asset["href"])
        nir_url = s3_to_http(nir_asset["href"])
        
        # Open source datasets using rasterio
        try:
            with rasterio.open(red_url) as src_red, rasterio.open(nir_url) as src_nir:
                img_crs = src_red.crs
                
                # Clip for each lot in shapefile
                for idx_lot, row in gdf.iterrows():
                    camp_name = str(row.get("Nomb_campo", "Unknown")).strip().replace(" ", "_")
                    lote_num = str(row.get("Lote", "None")).strip()
                    lote_id = f"Lote_{idx_lot:02d}_{camp_name}"
                    if lote_num and lote_num not in ("None", "nan"):
                        lote_id += f"_L{lote_num}"
                        
                    geom = get_clean_geometry(row["geometry"])
                    lote_bounds = geom.bounds
                    
                    # Reproject bounds to image CRS
                    minx, miny, maxx, maxy = transform_bounds("EPSG:4326", img_crs, *lote_bounds)
                    
                    # Get read window
                    window = from_bounds(minx, miny, maxx, maxy, src_red.transform).round()
                    
                    # Read window data with boundless padding to handle edges
                    red_data = src_red.read(1, window=window, boundless=True, fill_value=0).astype('float32')
                    nir_data = src_nir.read(1, window=window, boundless=True, fill_value=0).astype('float32')
                    
                    # Guard against empty/zero readings
                    if red_data.size == 0 or np.all(red_data == 0):
                        continue
                        
                    # Calculate NDVI
                    denom = nir_data + red_data
                    # Prevent division by zero and calculation on 0-filled background
                    valid_mask = (denom > 0) & (red_data > 0) & (nir_data > 0)
                    ndvi = np.where(valid_mask, (nir_data - red_data) / denom, np.nan)
                    
                    # Calculate statistics
                    valid_ndvi_pixels = ndvi[~np.isnan(ndvi)]
                    if valid_ndvi_pixels.size == 0:
                        continue
                        
                    mean_val = np.mean(valid_ndvi_pixels)
                    std_val = np.std(valid_ndvi_pixels)
                    min_val = np.min(valid_ndvi_pixels)
                    max_val = np.max(valid_ndvi_pixels)
                    
                    # Save clipped NDVI GeoTIFF
                    win_transform = src_red.window_transform(window)
                    out_meta = src_red.meta.copy()
                    out_meta.update({
                        "driver": "GTiff",
                        "height": ndvi.shape[0],
                        "width": ndvi.shape[1],
                        "transform": win_transform,
                        "count": 1,
                        "dtype": "float32",
                        "crs": img_crs
                    })
                    
                    tiff_filename = f"{lote_id}_{scene_date}_{col}_NDVI.tif"
                    tiff_filepath = os.path.join(OUTPUT_DIR, tiff_filename)
                    
                    with rasterio.open(tiff_filepath, "w", **out_meta) as dst:
                        dst.write(ndvi, 1)
                        
                    csv_records.append({
                        "lote_id": lote_id,
                        "fecha": scene_date,
                        "sensor": col,
                        "ndvi_medio": round(float(mean_val), 4),
                        "ndvi_std": round(float(std_val), 4),
                        "ndvi_min": round(float(min_val), 4),
                        "ndvi_max": round(float(max_val), 4),
                        "pixeles_validos": int(valid_ndvi_pixels.size)
                    })
                    print(f"    ✅ Clipped {lote_id}: Mean NDVI = {mean_val:.4f} ({valid_ndvi_pixels.size} px)")
                    
        except Exception as e:
            print(f"  ❌ Error reading or clipping scene {scene_id}: {e}")
            continue

    # 5. Save consolidated CSV
    if csv_records:
        df = pd.DataFrame(csv_records)
        df = df.sort_values(by=["lote_id", "fecha"]).reset_index(drop=True)
        csv_path = os.path.join(OUTPUT_DIR, "serie_temporal_cbers.csv")
        df.to_csv(csv_path, index=False)
        print(f"\n🎉 SUCCESS! Extracted {len(df)} records. Saved CSV to {csv_path}")
    else:
        print("\n❌ No data could be successfully clipped.")

if __name__ == "__main__":
    main()
