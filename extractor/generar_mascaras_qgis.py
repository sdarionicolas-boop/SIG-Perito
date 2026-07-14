import os
import sys
import glob
import re
import argparse
import numpy as np
import geopandas as gpd
import rasterio

sys.path.append(os.path.abspath(os.path.dirname(__file__)))
import cosecha_core

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Máscaras entregables: se escriben al Escritorio del usuario (carpeta por lote).
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")


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
        description="Máscaras binarias de cosecha acumulada (GeoTIFF) para QGIS y el GIF"
    )
    parser.add_argument("--lote_id", required=True, help="ID completo o búsqueda parcial del lote")
    parser.add_argument("--shp", default="data/lotes_mani.shp", help="Ruta al shapefile de lotes")
    parser.add_argument("--tifs_dir", default="data/tifs_procesados", help="Carpeta raíz de TIFs (subcarpeta por lote)")
    parser.add_argument("--umbral", type=float, default=0.55,
                        help="Fracción del pico de vigor: cosechado si RVI < umbral*pico (0-1, defecto 0.55)")
    parser.add_argument("--meses", default="",
                        help="Filtro opcional de meses YYYY-MM separados por coma. Vacío = todas las fechas.")
    args = parser.parse_args()

    print("=" * 78)
    print("🎨 MÁSCARAS DE COSECHA ACUMULADA (BINARIAS) PARA QGIS")
    print("=" * 78)

    meses_filtro = [m.strip() for m in args.meses.split(",") if m.strip()]

    # 1. Shapefile y geometría
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

    output_dir = os.path.join(DESKTOP, f"Secuencia_Cosecha_{lote_id}")
    os.makedirs(output_dir, exist_ok=True)

    # 2. TIFs de RVI del lote
    lote_dir = os.path.join(args.tifs_dir, lote_id)
    tif_files = glob.glob(os.path.join(lote_dir, f"{lote_id}_*_RVI.tif"))
    if not tif_files:
        print(f"[WARN] Sin GeoTIFFs RVI en {lote_dir}.")
        return

    records = []
    for fp in tif_files:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(fp))
        if m:
            records.append((m.group(1), fp))
    records = sorted(records, key=lambda x: x[0])

    # 3. Detección acumulada (misma lógica que el CSV) sobre el stack alineado
    fechas, stack, ref_transform, ref_meta = cosecha_core.cargar_stack(records)
    poly_mask = cosecha_core.mascara_poligono(geom, ref_transform, stack.shape[1:])
    det = cosecha_core.detectar_cosecha(stack, poly_mask, frac=args.umbral)
    valid_pixel = det["valid_pixel"]
    harvested_cum = det["harvested_cum"]

    out_meta = ref_meta.copy()
    out_meta.update({"driver": "GTiff", "count": 1, "dtype": "float32", "nodata": np.nan})

    generated = 0
    for t, fecha in enumerate(fechas):
        if meses_filtro and fecha[:7] not in meses_filtro:
            continue

        # NaN fuera del lote; 1.0 cosechado acumulado; 0.0 en pie
        mask_band = np.full(stack.shape[1:], np.nan, dtype="float32")
        mask_band[valid_pixel] = 0.0
        mask_band[harvested_cum[t]] = 1.0

        mask_filename = f"{lote_id}_{fecha}_mascara_cosecha.tif"
        mask_filepath = os.path.join(output_dir, mask_filename)
        with rasterio.open(mask_filepath, "w", **out_meta) as dst:
            dst.write(mask_band, 1)

        generated += 1
        print(f"✅ Máscara {fecha}: {mask_filename}")

    print(f"\n🎉 Listo. {generated} máscaras en: {output_dir}")


if __name__ == "__main__":
    main()
