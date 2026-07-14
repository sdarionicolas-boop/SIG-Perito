"""
Extractor multibanda + indices para un solo lote (prueba: BELETTI CAVALLO).

Baja, por fecha, la media sobre el poligono del lote de:
  - Todas las bandas de Sentinel-2 L2A: B01-B12 (B10/cirrus no existe en L2A).
  - Indices: NDVI, GNDVI, SAVI, MSAVI.

Sentinel-2 unicamente (tiene todas las bandas nativas). Mismo enfoque que el
pipeline existente: solo valores en CSV, enmascarando nubes con la SCL.

Reutiliza las funciones de autenticacion/geometria de extractor_temporal.py.
"""
import os
import sys
import math
import pandas as pd
import geopandas as gpd
from shapely.geometry import mapping

from extractor_temporal import (
    get_credentials,
    get_cdse_token,
    get_clean_geometry,
    query_stats,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# --- Configuracion ---
SHP_PATH = "lotes_mani.shp"
LOTE_OBJETIVO = "BELETTI_CAVALLO"   # subcadena del lote_id a procesar
START_DATE = "2025-10-01"
END_DATE = "2026-05-31"
MIN_VALID_PCT = 40.0
OUTPUT_CSV = "bandas_indices_beletti.csv"

# Orden de las bandas de salida del evalscript (debe coincidir con el return).
BANDAS_REFLECTANCIA = ["B01", "B02", "B03", "B04", "B05", "B06",
                       "B07", "B08", "B8A", "B09", "B11", "B12"]
INDICES = ["NDVI", "GNDVI", "SAVI", "MSAVI"]
ORDEN_SALIDA = BANDAS_REFLECTANCIA + INDICES   # 16 bandas -> B0..B15


def build_payload(geom_mapping, start_date, end_date):
    """Statistical API payload: todas las bandas S2L2A + 4 indices, mean/std/min/max."""
    evalscript = """
    //VERSION=3
    function setup() {
      return {
        input: ["B01","B02","B03","B04","B05","B06","B07","B08","B8A","B09","B11","B12","SCL","dataMask"],
        output: [
          {id: "default", bands: 16, sampleType: "FLOAT32"},
          {id: "dataMask", bands: 1}
        ]
      };
    }
    function evaluatePixel(s) {
      let nir = s.B08, red = s.B04, green = s.B03;
      let L = 0.5;
      let ndvi  = (nir + red) > 0 ? (nir - red) / (nir + red) : 0;
      let gndvi = (nir + green) > 0 ? (nir - green) / (nir + green) : 0;
      let savi  = (nir + red + L) > 0 ? ((nir - red) / (nir + red + L)) * (1.0 + L) : 0;
      let inner = Math.pow(2.0 * nir + 1.0, 2.0) - 8.0 * (nir - red);
      let msavi = inner >= 0 ? (2.0 * nir + 1.0 - Math.sqrt(inner)) / 2.0 : 0;

      // SCL: 3=sombra nube, 8=nube media, 9=nube alta, 10=cirrus
      let isCloud = s.SCL === 3 || s.SCL === 8 || s.SCL === 9 || s.SCL === 10;
      let mask = s.dataMask && !isCloud ? 1 : 0;

      return {
        default: [s.B01, s.B02, s.B03, s.B04, s.B05, s.B06, s.B07, s.B08,
                  s.B8A, s.B09, s.B11, s.B12, ndvi, gndvi, savi, msavi],
        dataMask: [mask]
      };
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


def process_results(data):
    """Parsea la salida multibanda: una fila por fecha con la media de cada banda/indice."""
    records = {}
    if not data:
        return records

    for item in data:
        interval = item.get("interval", {})
        start_time = interval.get("from")
        if not start_time:
            continue
        date_str = start_time.split("T")[0]

        bands_out = item.get("outputs", {}).get("default", {}).get("bands", {})

        # B0 sirve de referencia para el conteo de pixeles validos (mascara comun)
        ref_stats = bands_out.get("B0", {}).get("stats", {})
        mean_ref = ref_stats.get("mean")
        if mean_ref is None or mean_ref == "NaN" or math.isnan(float(mean_ref)):
            continue
        sample_count = float(ref_stats.get("sampleCount", 0.0))
        no_data_count = float(ref_stats.get("noDataCount", 0.0))
        valid_pixels = sample_count - no_data_count

        row = {"fecha": date_str, "sensor": "Sentinel-2", "valid_pixels": valid_pixels}
        for i, nombre in enumerate(ORDEN_SALIDA):
            stats = bands_out.get(f"B{i}", {}).get("stats", {})
            mean_val = stats.get("mean")
            try:
                row[nombre] = round(float(mean_val), 4) if mean_val not in (None, "NaN") else None
            except (TypeError, ValueError):
                row[nombre] = None
        records[date_str] = row
    return records


def main():
    print("=" * 72)
    print("EXTRACTOR MULTIBANDA + INDICES (prueba) -", LOTE_OBJETIVO)
    print("=" * 72)

    username, password = get_credentials()
    if not username or not password:
        print("[ERROR] No se encontraron credenciales CDSE.")
        return
    print(f"Autenticando como: {username[:4]}***...")
    token = get_cdse_token(username, password)
    if not token:
        print("[ERROR] No se pudo obtener el token CDSE.")
        return
    print("Autenticacion OK.")

    if not os.path.exists(SHP_PATH):
        print(f"[ERROR] No se encontro el shapefile: {SHP_PATH}")
        return
    gdf = gpd.read_file(SHP_PATH)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    # Localizar el lote objetivo construyendo el lote_id igual que el extractor base
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
        print(f"[ERROR] No se encontro un lote que contenga '{LOTE_OBJETIVO}'.")
        return

    lote_id, row = fila_objetivo
    print(f"Lote objetivo: {lote_id}")
    print(f"Rango temporal: {START_DATE} a {END_DATE}")

    clean_geom = get_clean_geometry(row["geometry"])
    geom_mapping = mapping(clean_geom)

    print("Consultando Sentinel-2 L2A (todas las bandas + indices)...")
    payload = build_payload(geom_mapping, START_DATE, END_DATE)
    raw = query_stats(token, payload)
    data = process_results(raw)
    print(f"  -> {len(data)} observaciones crudas.")

    if not data:
        print("[ERROR] Sin datos. CSV no generado.")
        return

    # pct_valido relativo al maximo de pixeles validos (denominador cielo-despejado)
    max_valid = max((r["valid_pixels"] for r in data.values()), default=0.0)
    filas = []
    for date_str in sorted(data.keys()):
        rec = data[date_str]
        pct = round(rec["valid_pixels"] / max_valid * 100.0, 1) if max_valid > 0 else 0.0
        if pct < MIN_VALID_PCT:
            continue
        fila = {"lote_id": lote_id, "fecha": date_str, "sensor": rec["sensor"], "pct_valido": pct}
        for nombre in ORDEN_SALIDA:
            fila[nombre] = rec.get(nombre)
        filas.append(fila)

    if not filas:
        print(f"[ADVERTENCIA] Ninguna fecha supero el umbral de {MIN_VALID_PCT}% de pixeles validos.")
        return

    columnas = ["lote_id", "fecha", "sensor", "pct_valido"] + ORDEN_SALIDA
    df_out = pd.DataFrame(filas)[columnas].sort_values("fecha").reset_index(drop=True)
    df_out.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
    print(f"\nOK. {len(df_out)} fechas validas guardadas en: {OUTPUT_CSV}")
    print(f"Columnas ({len(columnas)}): {', '.join(columnas)}")


if __name__ == "__main__":
    main()
