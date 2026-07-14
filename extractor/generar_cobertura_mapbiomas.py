"""Precalcula la composición de cobertura MapBiomas por lote y la guarda en CSV.

Lee un GeoTIFF anual de MapBiomas Argentina (país entero, ~600 MB) UNA vez y
recorta por ventana la composición de cada lote de la BD. El CSV resultante es
chico y versionable; la app y HF leen de ahí, sin el ráster y sin GEE.

Uso:
  python -m extractor.generar_cobertura_mapbiomas --tif argentina_coverage_2024.tif --anio 2024

Correr una vez por año descargado. El CSV acumula años (reemplaza el mismo año
si se re-ejecuta). Descarga de rásters (CC-BY-SA):
  https://storage.googleapis.com/mapbiomas-public/initiatives/argentina/collection-2/coverage/argentina_coverage_{ANIO}.tif
"""
import argparse
import csv
import json
import os

import rasterio

from app.database import get_conn
from app.services.mapbiomas import CATEGORIAS, composicion_desde_raster

CAMPOS = ["lote_id", "nombre", "anio", *CATEGORIAS]


def _leer_existente(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    ap = argparse.ArgumentParser(description="Composición MapBiomas por lote -> CSV")
    ap.add_argument("--tif", required=True, help="Ruta al GeoTIFF anual de MapBiomas Argentina")
    ap.add_argument("--anio", type=int, required=True, help="Año del ráster")
    ap.add_argument("--out", default="data/cobertura_mapbiomas.csv")
    args = ap.parse_args()

    conn = get_conn()
    lotes = conn.execute("SELECT id, nombre, geom_geojson FROM lotes ORDER BY id").fetchall()
    conn.close()

    filas = []
    with rasterio.open(args.tif) as src:
        if src.crs and src.crs.to_epsg() != 4326:
            raise SystemExit(f"El ráster está en {src.crs}, se espera EPSG:4326")
        for lo in lotes:
            comp = composicion_desde_raster(json.loads(lo["geom_geojson"]), src)
            filas.append({"lote_id": lo["id"], "nombre": lo["nombre"],
                          "anio": args.anio, **comp})

    # Merge: conservar otros años, reemplazar el año actual.
    previas = [r for r in _leer_existente(args.out) if int(r["anio"]) != args.anio]
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        for r in previas:
            w.writerow({k: r[k] for k in CAMPOS})
        for r in filas:
            w.writerow(r)

    print(f"OK: {len(filas)} lotes · año {args.anio} -> {args.out} "
          f"({len(previas)} filas de otros años conservadas)")


if __name__ == "__main__":
    main()
