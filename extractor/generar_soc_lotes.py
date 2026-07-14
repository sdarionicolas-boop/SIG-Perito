"""Precalcula el stock de carbono orgánico del suelo (SOC, 0-30 cm) por lote a CSV.

Corre la estadística zonal sobre los rásters del Mapa de COS del INTA (en data/soc/,
~600 MB) UNA vez y vuelca el resultado por lote a data/soc_lotes.csv (chico, versionable).
La app y HF leen ese CSV al instante, sin los rásters ni la API externa de SoilGrids.

Uso:
  python -m extractor.generar_soc_lotes
"""
import argparse
import csv
import json

from shapely.geometry import shape

from app.database import get_conn
from app.services.soilgrids import analyze_soc_for_geom_or_coords

CAMPOS = ["lote_id", "nombre", "depth", "soc_mean", "soc_low", "soc_high",
          "bulk_density_used", "source"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Precalcula SOC por lote -> CSV")
    ap.add_argument("--out", default="data/soc_lotes.csv")
    args = ap.parse_args()

    conn = get_conn()
    lotes = conn.execute(
        "SELECT id, nombre, centroide_lat, centroide_lon, geom_geojson FROM lotes ORDER BY id"
    ).fetchall()
    conn.close()

    filas, saltados = [], 0
    for lo in lotes:
        lat, lon = lo["centroide_lat"], lo["centroide_lon"]
        if lat is None or lon is None:
            saltados += 1
            continue
        geom = shape(json.loads(lo["geom_geojson"])) if lo["geom_geojson"] else None
        try:
            res = analyze_soc_for_geom_or_coords(lon, lat, geom)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip lote {lo['id']}: {exc}")
            saltados += 1
            continue
        # Sumar las capas a un único stock 0-30 cm (la ruta INTA ya devuelve 0-30).
        filas.append({
            "lote_id": lo["id"],
            "nombre": lo["nombre"],
            "depth": "0-30cm",
            "soc_mean": round(sum(s.mean for s in res.stocks), 2),
            "soc_low": round(sum(s.uncertainty_low for s in res.stocks), 2),
            "soc_high": round(sum(s.uncertainty_high for s in res.stocks), 2),
            "bulk_density_used": res.bulk_density_used,
            "source": res.source,
        })

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(filas)

    print(f"OK: {len(filas)} lotes -> {args.out} ({saltados} saltados sin coords/datos)")


if __name__ == "__main__":
    main()
