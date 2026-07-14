"""Composición de cobertura de suelo por lote desde ráster MapBiomas Argentina.

Lee un GeoTIFF anual de MapBiomas Argentina (Colección 2, 30 m, EPSG:4326) por
VENTANA sobre el polígono del lote — nunca carga el país entero (~600 MB, 14.7 Gpx).
Reclasifica los códigos de leyenda de MapBiomas a 5 categorías agronómicas y
devuelve la composición porcentual. Sin Google Earth Engine.

Descarga de los rásters (CC-BY-SA, cambiar el año en la URL):
  https://storage.googleapis.com/mapbiomas-public/initiatives/argentina/
  collection-2/coverage/argentina_coverage_{ANIO}.tif

Reclasificación validada empíricamente contra el ráster 2024: los lotes agrícolas
de Córdoba dan clase 19 (cultivo temporal) ~100%.
"""
import numpy as np
import rasterio
from rasterio.mask import mask

# Reclasificación de códigos MapBiomas -> 5 categorías. Lo no listado cae en "Otros"
# (22/23/24/25/30 sin vegetación, 9 silvicultura, 27 no observado).
LEYENDA = {
    "Bosque Nativo": {3, 4, 5, 6},                    # formación boscosa/sabana/manglar/inundable
    "Agricultura":   {18, 19, 20, 36, 39, 40, 41, 46, 47, 48, 62},  # cultivos temp/perennes
    "Pastura":       {12, 13, 15, 21},                # pastizal/pastura/mosaico de usos
    "Agua":          {11, 26, 31, 33},                # humedal/agua/acuicultura/río-lago
}
_COD2CAT = {cod: cat for cat, cods in LEYENDA.items() for cod in cods}
CATEGORIAS = ["Bosque Nativo", "Agricultura", "Pastura", "Agua", "Otros"]


def composicion_desde_raster(geom_geojson: dict, src) -> dict:
    """Composición de 5 categorías (%) para un polígono sobre un ráster ya abierto.

    El ráster debe estar en EPSG:4326 (igual que los polígonos de la BD). Devuelve
    un dict {categoría: porcentaje}; todo en cero si el polígono no intersecta datos.
    """
    out, _ = mask(src, [geom_geojson], crop=True, nodata=0)
    arr = out[0]
    validos = arr[arr != 0]
    comp = {cat: 0.0 for cat in CATEGORIAS}
    if validos.size == 0:
        return comp
    vals, counts = np.unique(validos, return_counts=True)
    tot = int(counts.sum())
    for v, c in zip(vals.tolist(), counts.tolist()):
        comp[_COD2CAT.get(v, "Otros")] += 100.0 * c / tot
    return {k: round(v, 1) for k, v in comp.items()}


def composicion_lote(geom_geojson: dict, ruta_tif: str) -> dict:
    """Abre el ráster y calcula la composición para un polígono (uso puntual)."""
    with rasterio.open(ruta_tif) as src:
        if src.crs and src.crs.to_epsg() != 4326:
            raise ValueError(f"Se esperaba EPSG:4326, el ráster está en {src.crs}")
        return composicion_desde_raster(geom_geojson, src)
