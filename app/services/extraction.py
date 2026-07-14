"""Extracción de series temporales por lote, reutilizando extractor_temporal.

Envuelve las funciones puras del extractor original (auth, payloads, parsing)
para operar sobre UN lote bajo demanda y persistir el resultado en SQLite,
actualizando el progreso del job asociado. No modifica el script original.
"""
import os
import sys

from shapely.geometry import mapping, shape

from app.config import BASE_DIR, FECHA_INICIO, FECHA_FIN, MIN_VALID_PCT
from app.database import db_cursor, get_conn
from app.jobs import update_job

# Permitir importar el paquete `extractor` desde la raíz del proyecto.
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from extractor.extractor_temporal import (  # noqa: E402
    get_credentials,
    get_cdse_token,
    get_clean_geometry,
    build_s2_payload,
    build_landsat_payload,
    build_s1_payload,
    query_stats,
    process_results,
    process_s1_results,
)


def _resolve_credentials():
    """Credenciales desde el entorno (cargado por config) o vía extractor."""
    user = os.environ.get("EODAG__COP_DATASPACE__AUTH__CREDENTIALS__USERNAME")
    pwd = os.environ.get("EODAG__COP_DATASPACE__AUTH__CREDENTIALS__PASSWORD")
    if user and pwd:
        return user, pwd
    return get_credentials()


def _load_lote_geom(lote_id: int):
    """Devuelve (geom_shapely_limpia, geom_mapping) del lote, o (None, None)."""
    import json

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT geom_geojson FROM lotes WHERE id = ?", (lote_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None, None
    geom = get_clean_geometry(shape(json.loads(row["geom_geojson"])))
    return geom, mapping(geom)


def _insert_optical(lote_id: int, records: list[dict]) -> int:
    """Inserta/actualiza registros NDVI ópticos. Devuelve cantidad escrita."""
    if not records:
        return 0
    with db_cursor() as conn:
        for r in records:
            conn.execute(
                "INSERT INTO series_temporales "
                "(lote_id, fecha, indice, valor, valor_min, valor_max, valor_std, "
                " sensor, orbita, pct_valido) "
                "VALUES (?, ?, 'NDVI', ?, ?, ?, ?, ?, '', ?) "
                "ON CONFLICT(lote_id, fecha, indice, orbita) DO UPDATE SET "
                "  valor=excluded.valor, valor_min=excluded.valor_min, "
                "  valor_max=excluded.valor_max, valor_std=excluded.valor_std, "
                "  sensor=excluded.sensor, pct_valido=excluded.pct_valido",
                (lote_id, r["fecha"], r["ndvi_medio"], r["ndvi_min"],
                 r["ndvi_max"], r["ndvi_std"], r["sensor"], r["pct_valido"]),
            )
    return len(records)


def _insert_sar(lote_id: int, records: list[dict]) -> int:
    """Inserta/actualiza registros RVI (SAR). Devuelve cantidad escrita."""
    if not records:
        return 0
    with db_cursor() as conn:
        for r in records:
            conn.execute(
                "INSERT INTO series_temporales "
                "(lote_id, fecha, indice, valor, valor_min, valor_max, valor_std, "
                " sensor, orbita, pct_valido) "
                "VALUES (?, ?, 'RVI', ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(lote_id, fecha, indice, orbita) DO UPDATE SET "
                "  valor=excluded.valor, valor_min=excluded.valor_min, "
                "  valor_max=excluded.valor_max, valor_std=excluded.valor_std, "
                "  sensor=excluded.sensor, pct_valido=excluded.pct_valido",
                (lote_id, r["fecha"], r["rvi_medio"], r["rvi_min"], r["rvi_max"],
                 r["rvi_std"], r["sensor"], r["orbita"], r["pct_valido"]),
            )
    return len(records)


def _harmonize_optical(geom_mapping, token) -> list[dict]:
    """Consulta S2 + Landsat y fusiona priorizando S2 (igual que el extractor)."""
    from app.services.uso import registrar_uso
    registrar_uso("statistical")
    s2 = process_results(query_stats(token, build_s2_payload(
        geom_mapping, FECHA_INICIO, FECHA_FIN)), "Sentinel-2")
    registrar_uso("statistical")
    l8 = process_results(query_stats(token, build_landsat_payload(
        geom_mapping, FECHA_INICIO, FECHA_FIN)), "Landsat")

    max_s2 = max((r["valid_pixels"] for r in s2.values()), default=0.0)
    max_l8 = max((r["valid_pixels"] for r in l8.values()), default=0.0)
    for r in s2.values():
        r["pct_valido"] = round(r["valid_pixels"] / max_s2 * 100, 1) if max_s2 else 0.0
    for r in l8.values():
        r["pct_valido"] = round(r["valid_pixels"] / max_l8 * 100, 1) if max_l8 else 0.0

    out = []
    for date_str in sorted(set(s2) | set(l8)):
        s2_rec, l8_rec = s2.get(date_str), l8.get(date_str)
        chosen = None
        if s2_rec and s2_rec["pct_valido"] >= MIN_VALID_PCT:
            chosen = s2_rec
        elif l8_rec and l8_rec["pct_valido"] >= MIN_VALID_PCT:
            chosen = l8_rec
        if chosen:
            out.append({"fecha": date_str, **chosen})
    return out


def _query_sar(geom_mapping, token) -> list[dict]:
    """Consulta S1 ascendente + descendente y filtra por % válido."""
    from app.services.uso import registrar_uso
    out = []
    for orbit in ("ASCENDING", "DESCENDING"):
        registrar_uso("statistical")
        data = process_s1_results(
            query_stats(token, build_s1_payload(
                geom_mapping, FECHA_INICIO, FECHA_FIN, orbit)), orbit)
        max_v = max((r["valid_pixels"] for r in data.values()), default=0.0)
        for date_str in sorted(data):
            r = data[date_str]
            pct = round(r["valid_pixels"] / max_v * 100, 1) if max_v else 0.0
            if pct >= MIN_VALID_PCT:
                out.append({"fecha": date_str, "pct_valido": pct, **r})
    return out


# --- NDWI (Gao): contenido de agua del canopeo, sensible al estrés hídrico ---
# NDWI = (B08 − B11) / (B08 + B11). Solo Sentinel-2 (B11 SWIR), máscara SCL.
_NDWI_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: ["B08", "B11", "SCL", "dataMask"],
    output: [
      {id: "default", bands: 1, sampleType: "FLOAT32"},
      {id: "dataMask", bands: 1}
    ]
  };
}
function evaluatePixel(s) {
  let ndwi = (s.B08 - s.B11) / (s.B08 + s.B11);
  let isCloud = s.SCL === 3 || s.SCL === 8 || s.SCL === 9 || s.SCL === 10;
  let mask = s.dataMask && !isCloud ? 1 : 0;
  return { default: [ndwi], dataMask: [mask] };
}
"""


def _build_ndwi_payload(geom_mapping, start_date, end_date):
    """Payload Statistical API para NDWI (Gao) sobre Sentinel-2 L2A."""
    return {
        "input": {
            "bounds": {"geometry": geom_mapping,
                       "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}},
            "data": [{"type": "S2L2A",
                      "dataFilter": {"timeRange": {"from": f"{start_date}T00:00:00Z",
                                                   "to": f"{end_date}T23:59:59Z"}}}],
        },
        "aggregation": {
            "timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"},
            "aggregationInterval": {"of": "P1D"},
            "evalscript": _NDWI_EVALSCRIPT,
            "resx": 0.0001, "resy": 0.0001,
        },
        "calculations": {"default": {"statistics": {"default": {
            "stDev": True, "mean": True, "min": True, "max": True}}}},
    }


def _query_ndwi(geom_mapping, token) -> list[dict]:
    """Consulta NDWI (S2) y filtra por % de píxeles válidos."""
    from app.services.uso import registrar_uso
    registrar_uso("statistical")
    # process_results es genérico (mean/min/max/std); reutilizamos sin calibración.
    data = process_results(query_stats(
        token, _build_ndwi_payload(geom_mapping, FECHA_INICIO, FECHA_FIN)), "Sentinel-2")
    max_v = max((r["valid_pixels"] for r in data.values()), default=0.0)
    out = []
    for date_str in sorted(data):
        r = data[date_str]
        pct = round(r["valid_pixels"] / max_v * 100, 1) if max_v else 0.0
        if pct >= MIN_VALID_PCT:
            out.append({"fecha": date_str, "valor": r["ndvi_medio"], "min": r["ndvi_min"],
                        "max": r["ndvi_max"], "std": r["ndvi_std"], "pct_valido": pct})
    return out


def _insert_ndwi(lote_id: int, records: list[dict]) -> int:
    """Inserta/actualiza registros NDWI (Sentinel-2). Devuelve cantidad escrita."""
    if not records:
        return 0
    with db_cursor() as conn:
        for r in records:
            conn.execute(
                "INSERT INTO series_temporales "
                "(lote_id, fecha, indice, valor, valor_min, valor_max, valor_std, "
                " sensor, orbita, pct_valido) "
                "VALUES (?, ?, 'NDWI', ?, ?, ?, ?, 'Sentinel-2', '', ?) "
                "ON CONFLICT(lote_id, fecha, indice, orbita) DO UPDATE SET "
                "  valor=excluded.valor, valor_min=excluded.valor_min, "
                "  valor_max=excluded.valor_max, valor_std=excluded.valor_std, "
                "  pct_valido=excluded.pct_valido",
                (lote_id, r["fecha"], r["valor"], r["min"], r["max"], r["std"], r["pct_valido"]),
            )
    return len(records)


def backfill_ndwi(lote_id: int, token: str) -> int:
    """Extrae e inserta solo la serie NDWI de un lote (para backfill). Devuelve n."""
    geom, geom_mapping = _load_lote_geom(lote_id)
    if geom is None:
        return 0
    return _insert_ndwi(lote_id, _query_ndwi(geom_mapping, token))


def run_extraction_job(job_id: str, lote_id: int) -> None:
    """Tarea de fondo: extrae NDVI (S2+Landsat) y RVI (S1) para un lote.

    Pensada para ejecutarse vía FastAPI BackgroundTasks. Toda excepción se
    captura y marca el job como FAILED para no romper el proceso del servidor.
    """
    try:
        update_job(job_id, estado="PROCESSING", progreso=5,
                   mensaje="Autenticando con Copernicus CDSE...")
        user, pwd = _resolve_credentials()
        if not user or not pwd:
            update_job(job_id, estado="FAILED",
                       error_msg="Credenciales CDSE no encontradas.")
            return
        token = get_cdse_token(user, pwd)
        if not token:
            update_job(job_id, estado="FAILED",
                       error_msg="No se pudo obtener token CDSE.")
            return

        geom, geom_mapping = _load_lote_geom(lote_id)
        if geom is None:
            update_job(job_id, estado="FAILED",
                       error_msg=f"Lote {lote_id} inexistente.")
            return

        update_job(job_id, progreso=30,
                   mensaje="Consultando series ópticas (Sentinel-2 / Landsat)...")
        n_opt = _insert_optical(lote_id, _harmonize_optical(geom_mapping, token))

        update_job(job_id, progreso=60,
                   mensaje="Consultando radar (Sentinel-1 RVI)...")
        n_sar = _insert_sar(lote_id, _query_sar(geom_mapping, token))

        update_job(job_id, progreso=85,
                   mensaje="Consultando agua del canopeo (NDWI)...")
        n_ndwi = _insert_ndwi(lote_id, _query_ndwi(geom_mapping, token))

        update_job(job_id, estado="COMPLETED", progreso=100,
                   mensaje=f"Listo: {n_opt} NDVI + {n_sar} RVI + {n_ndwi} NDWI extraídos.")
    except Exception as exc:  # noqa: BLE001 — registrar y marcar el job
        update_job(job_id, estado="FAILED", error_msg=str(exc)[:500])
