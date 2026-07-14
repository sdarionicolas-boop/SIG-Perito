import os
import sys
import glob
import re
import argparse
import numpy as np
import pandas as pd
import geopandas as gpd

# Rutas: raíz del proyecto (app.geoutils) y carpeta del script (cosecha_core).
sys.path.append(os.path.abspath("."))
sys.path.append(os.path.abspath("app"))
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from app.geoutils import calculate_area_hectares
import cosecha_core

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def resolver_lote(gdf, clave):
    """Reconstruye el lote_id canónico y devuelve (lote_id, row) por match parcial."""
    for idx, row in gdf.iterrows():
        if "ID" in row and "Nomb_campo" not in row:
            lote_id = f"Mani_2425_L{row['ID']}"
        elif "id" in row and "Nomb_campo" not in row:
            lote_id = f"Soja_2425_L{row['id']}"
        else:
            camp = str(row.get("Nomb_campo", "Unknown")).strip().replace(" ", "_")
            lote_num = str(row.get("Lote", "None")).strip()
            lote_id = f"Lote_{idx:02d}_{camp}"
            if lote_num and lote_num not in ("None", "nan"):
                lote_id += f"_L{lote_num}"
        if clave.upper() in lote_id.upper() or lote_id.upper() in clave.upper():
            return lote_id, row
    return None, None


def main():
    parser = argparse.ArgumentParser(
        description="Avance de cosecha por píxel (baseline por píxel + monotonía)"
    )
    parser.add_argument("--lote_id", required=True, help="ID completo o búsqueda parcial del lote")
    parser.add_argument("--shp", default="data/lotes_mani.shp", help="Ruta al shapefile de lotes")
    parser.add_argument("--tifs_dir", default="data/tifs_procesados", help="Carpeta raíz de TIFs (subcarpeta por lote)")
    parser.add_argument("--umbral", type=float, default=0.55,
                        help="Fracción del pico de vigor: cosechado si RVI < umbral*pico (0-1, defecto 0.55)")
    args = parser.parse_args()

    print("=" * 78)
    print("🚜 AVANCE DE COSECHA POR PÍXEL — BASELINE POR PÍXEL + MONOTONÍA")
    print("=" * 78)

    # 1. Shapefile y geometría del lote
    if not os.path.exists(args.shp):
        print(f"[ERROR] Shapefile not found: {args.shp}")
        return
    gdf = gpd.read_file(args.shp)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    lote_id, row = resolver_lote(gdf, args.lote_id)
    if row is None:
        print(f"[ERROR] Lote '{args.lote_id}' no encontrado en {args.shp}.")
        return
    geom = row["geometry"]
    centroid = geom.centroid
    area_total_ha = calculate_area_hectares(geom, centroid.x, centroid.y)
    print(f"🎯 Lote: {lote_id}")
    print(f"📐 Superficie total: {area_total_ha:.2f} ha")
    print(f"📉 Regla: cosechado si RVI < {args.umbral:.2f} × (pico de vigor del píxel)\n")

    # 2. TIFs de RVI del lote
    lote_dir = os.path.join(args.tifs_dir, lote_id)
    tif_files = glob.glob(os.path.join(lote_dir, f"{lote_id}_*_RVI.tif"))
    if not tif_files:
        print(f"[WARN] Sin GeoTIFFs RVI en {lote_dir}. Corré descargar_tifs_automatico.py primero.")
        return

    records = []
    for fp in tif_files:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(fp))
        if m:
            records.append((m.group(1), fp))
    records = sorted(records, key=lambda x: x[0])

    # 3. Apilar, enmascarar por polígono y detectar cosecha acumulada
    fechas, stack, ref_transform, _ = cosecha_core.cargar_stack(records)
    poly_mask = cosecha_core.mascara_poligono(geom, ref_transform, stack.shape[1:])
    det = cosecha_core.detectar_cosecha(stack, poly_mask, frac=args.umbral)

    valid_pixel = det["valid_pixel"]
    harvested_cum = det["harvested_cum"]
    denom = int(valid_pixel.sum())
    t_peak = det["t_peak"]
    print(f"🌱 Pico de vigor del lote: {fechas[t_peak]} (inicio de la ventana de cosecha)")
    print(f"🧮 Píxeles de referencia (dentro del lote, con vigor): {denom}\n")

    print(f"{'Fecha':<12} | {'Cosechado':<10} | {'Progreso (%)':<13} | {'Sup. cosechada (ha)':<20}")
    print("-" * 78)

    results_table = []
    for t, fecha in enumerate(fechas):
        cos = int(harvested_cum[t].sum())
        pct = (cos / denom * 100.0) if denom else 0.0
        area_cos = area_total_ha * (cos / denom) if denom else 0.0
        marca = "  (pre-pico)" if t < t_peak else ""
        print(f"{fecha:<12} | {cos:<10} | {pct:>11.1f}% | {area_cos:>13.2f} ha / {area_total_ha:.2f}{marca}")
        results_table.append({
            "lote_id": lote_id,
            "fecha": fecha,
            "pixeles_validos": denom,
            "pixeles_cosechados": cos,
            "pct_cosechado": round(pct, 2),
            "hectareas_cosechadas": round(area_cos, 2),
            "hectareas_totales": round(area_total_ha, 2),
        })

    # 4. Guardar reporte (consumido por procesar_lote.py para el GIF)
    os.makedirs("data/reportes", exist_ok=True)
    output_csv = f"data/reportes/{lote_id}_progreso_cosecha.csv"
    pd.DataFrame(results_table).to_csv(output_csv, index=False)
    print("\n" + "=" * 78)
    print(f"🎉 Reporte guardado en: {output_csv}")
    print("=" * 78)


if __name__ == "__main__":
    main()
