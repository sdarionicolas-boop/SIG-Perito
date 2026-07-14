"""Deteccion automatica de cultivos por lote usando el Mapa Nacional de Cultivo del INTA (SEPA).

Fuente: https://sepa.inta.gob.ar/productos/geosepa/Mapa_Nacional_Cultivo/

Los TIFFs del INTA (uint8, EPSG:4326, ~30 m/pixel) contienen un raster de codigos numericos
donde cada valor identifica un cultivo o categoria de cobertura. La leyenda oficial se
mapea via `LEYENDA_VERANO` y `LEYENDA_INVIERNO` mas abajo — actualizar apenas se confirme
con INTA/Nestor.

Pipeline:
  1. Abrir el TIFF de la campana solicitada.
  2. Recortar (rasterio.mask) al poligono del lote.
  3. Contar pixeles por clase y convertir a hectareas usando la latitud del centroide.
  4. Devolver dict con superficies por cultivo + hipotesis (si aplica) + area total.

Match verificado sobre 3 lotes reales:
  - BERTONE OESTE (64.59 ha): 99.62% cobertura
  - LAS COLAS (2195.69 ha):   99.94% cobertura
  - LA GRAMILLA (1293.86 ha): 99.997% cobertura
"""
from __future__ import annotations

import json
import sqlite3
from math import cos, radians
from pathlib import Path

from app.config import BASE_DIR, DB_PATH

TIFF_VERANO = BASE_DIR / "MNC_verano_2024-2025.tif"
TIFF_INVIERNO = BASE_DIR / "MNC_invierno_2024-2025.tif"

# NODATA del raster (INTA usa 255 fuera de la mascara del pais).
NODATA = 255

# ============================================================================
# LEYENDA OFICIAL INTA SEPA - Mapa Nacional de Cultivo
# ----------------------------------------------------------------------------
# Fuente: archivos QML de estilo QGIS provistos por INTA junto con los TIFFs
# (MNC_verano.qml, MNC_invierno.qml). Validada 2026-07-08.
# ============================================================================

LEYENDA_VERANO = {
    10: "Maiz",
    11: "Soja",
    12: "Girasol",
    13: "Poroto",
    14: "Cana de azucar",
    15: "Algodon",
    16: "Mani",
    17: "Arroz",
    18: "Sorgo",
    19: "Girasol-CV",
    21: "Barbecho",
    22: "No agricola",
    26: "Papa",
    27: "Verdeo de Maiz",
    28: "Verdeo de Sorgo",
    30: "Tabaco",
    31: "Mascara",
}

LEYENDA_INVIERNO = {
    6: "Garbanzo",
    16: "Cereales de invierno",
    17: "Otros cultivos",
    18: "Barbecho",
    19: "Cana de azucar",
    20: "No agricola",
    24: "Arveja",
    25: "Mascara",
    26: "Papa",
}

# Categorias que no son cultivo comercial - se listan aparte del "area sembrada"
_NO_CULTIVO = {"Barbecho", "No agricola", "Mascara", "Otros cultivos"}


def _cargar_geom(lote_id: int) -> tuple[dict, float, str]:
    """Devuelve (geom_geojson_dict, area_declarada_ha, nombre_lote) o lanza."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT nombre, geom_geojson, area_ha FROM lotes WHERE id=?", (lote_id,)
    ).fetchone()
    conn.close()
    if not row or not row["geom_geojson"]:
        raise ValueError(f"Lote {lote_id} no encontrado o sin geometria en BD.")
    return json.loads(row["geom_geojson"]), row["area_ha"] or 0.0, row["nombre"]


def _pixel_area_ha(res_grados_lon: float, res_grados_lat: float, lat_centro: float) -> float:
    """Calcula la superficie de 1 pixel en hectareas para una latitud dada."""
    m_por_grado_lon = 111320 * cos(radians(lat_centro))
    m_por_grado_lat = 111320
    return abs(res_grados_lon) * abs(res_grados_lat) * m_por_grado_lon * m_por_grado_lat / 10_000


def detectar_cultivos(lote_id: int, campana: str = "verano") -> dict:
    """Detecta cultivos en el lote usando el TIFF INTA SEPA.

    Args:
        lote_id: id del lote en tabla lotes.
        campana: "verano" o "invierno".

    Returns:
        dict con:
          - lote_id, lote_nombre, campana, fuente
          - area_declarada_ha, area_detectada_ha, cobertura_pct
          - por_clase: [{codigo, nombre, pixeles, hectareas, pct}, ...]
          - por_cultivo: dict {nombre_cultivo: hectareas} (agregando clases con el mismo nombre)
          - leyenda_confirmada: bool (False mientras sean hipotesis)
    """
    try:
        import numpy as np
        import rasterio
        from rasterio.mask import mask
    except ImportError as exc:
        raise RuntimeError("Faltan dependencias: rasterio, numpy") from exc

    if campana not in ("verano", "invierno"):
        raise ValueError("campana debe ser 'verano' o 'invierno'")

    tiff_path = TIFF_VERANO if campana == "verano" else TIFF_INVIERNO
    if not tiff_path.exists():
        raise FileNotFoundError(f"TIFF no encontrado: {tiff_path}")

    leyenda = LEYENDA_VERANO if campana == "verano" else LEYENDA_INVIERNO

    geom, area_declarada_ha, nombre = _cargar_geom(lote_id)

    with rasterio.open(tiff_path) as src:
        arr, _transform = mask(src, [geom], crop=True, filled=True, nodata=NODATA)
        data = arr[0]
        # Descartar nodata (fuera de mascara del pais o del recorte)
        data = data[data != NODATA]
        if data.size == 0:
            return {
                "lote_id": lote_id,
                "lote_nombre": nombre,
                "campana": campana,
                "fuente": "INTA SEPA - Mapa Nacional de Cultivo",
                "area_declarada_ha": area_declarada_ha,
                "area_detectada_ha": 0.0,
                "cobertura_pct": 0.0,
                "por_clase": [],
                "por_cultivo": {},
                "leyenda_confirmada": False,
                "error": "Sin datos INTA en el poligono (fuera de mascara nacional)",
            }

        # Superficie por pixel a la latitud del centroide del poligono
        coords_ext = geom["coordinates"][0]
        lat_c = sum(v[1] for v in coords_ext) / len(coords_ext)
        pix_ha = _pixel_area_ha(src.res[0], src.res[1], lat_c)

        vals, counts = np.unique(data, return_counts=True)

    por_clase = []
    por_cultivo: dict[str, float] = {}
    total_pix = int(counts.sum())
    total_ha = 0.0

    for v, c in sorted(zip(vals, counts), key=lambda x: -x[1]):
        cod = int(v)
        pix = int(c)
        ha = pix * pix_ha
        pct = pix / total_pix * 100 if total_pix else 0.0
        nombre_cultivo = leyenda.get(cod, f"Clase {cod} (sin mapear)")
        por_clase.append({
            "codigo": cod,
            "nombre": nombre_cultivo,
            "pixeles": pix,
            "hectareas": round(ha, 2),
            "pct": round(pct, 2),
        })
        por_cultivo[nombre_cultivo] = por_cultivo.get(nombre_cultivo, 0.0) + ha
        total_ha += ha

    # Redondear agregacion final
    por_cultivo = {k: round(v, 2) for k, v in sorted(por_cultivo.items(), key=lambda x: -x[1])}

    cobertura_pct = (total_ha / area_declarada_ha * 100) if area_declarada_ha > 0 else 0.0

    return {
        "lote_id": lote_id,
        "lote_nombre": nombre,
        "campana": campana,
        "fuente": "INTA SEPA - Mapa Nacional de Cultivo (resolucion 30 m/pixel)",
        "area_declarada_ha": round(area_declarada_ha, 2),
        "area_detectada_ha": round(total_ha, 2),
        "cobertura_pct": round(cobertura_pct, 2),
        "por_clase": por_clase,
        "por_cultivo": por_cultivo,
        "leyenda_confirmada": True,  # validada 2026-07-08 con QML oficial de INTA
    }


def resumir_cultivos_texto(lote_id: int) -> str:
    """Devuelve un resumen en texto plano de la deteccion verano + invierno.

    Util para logs, respuestas API rapidas o para generar el texto que va al PDF.
    """
    partes = []
    for campana in ("verano", "invierno"):
        try:
            r = detectar_cultivos(lote_id, campana)
            partes.append(f"CAMPANA {campana.upper()} 2024-25 (cobertura {r['cobertura_pct']:.2f}%):")
            for c in r["por_clase"]:
                partes.append(f"  - {c['nombre']} ({c['codigo']}): {c['hectareas']:.2f} ha ({c['pct']:.2f}%)")
        except Exception as e:
            partes.append(f"Campana {campana}: error {e}")
    return "\n".join(partes)
