"""Endpoints de lotes: alta (upload GeoJSON/shapefile) y series temporales."""
import json
import sys
import tempfile
from pathlib import Path

import geopandas as gpd
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from shapely.geometry import shape

from app.config import BASE_DIR
from app.database import db_cursor, get_conn
from app.geoutils import calculate_area_hectares
from app.jobs import create_job
from app.schemas import (GeoJSONUpload, LoteOut, SerieTemporalOut, UploadResponse)
from app.services.extraction import run_extraction_job
from app.services.seed import COHORTE_SOMBRA_PREFIJO

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
from extractor.extractor_temporal import get_clean_geometry  # noqa: E402

router = APIRouter(prefix="/api/lotes", tags=["lotes"])


def _register_lote(geom, nombre, cultivo=None, campo=None, lote_num=None) -> dict:
    """Limpia la geometría, calcula centroide/área y la registra en la BD."""
    geom = get_clean_geometry(geom)
    if geom.is_empty:
        raise HTTPException(400, "La geometría está vacía o es inválida.")
    centroide = geom.centroid
    lon, lat = float(centroide.x), float(centroide.y)
    try:
        area_ha = round(calculate_area_hectares(geom, lon, lat), 2)
    except Exception:
        area_ha = None
    with db_cursor() as conn:
        cur = conn.execute(
            "INSERT INTO lotes "
            "(nombre, cultivo, campo, lote_num, geom_geojson, "
            " centroide_lat, centroide_lon, crs_original, area_ha) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'EPSG:4326', ?)",
            (nombre, cultivo, campo, lote_num,
             json.dumps(geom.__geo_interface__), lat, lon, area_ha),
        )
        lote_id = cur.lastrowid
    return {"id": lote_id, "nombre": nombre, "area_ha": area_ha}


# Los lotes "sombra" (cohorte NDVI sintético, ver seed.py) nunca son navegables:
# no tienen polígono real y solo existen para que Demo 2/3 tengan con qué
# compararse en el módulo de desvío. Se excluyen de la lista y del mapa.
_SIN_SOMBRA = f"nombre NOT LIKE '{COHORTE_SOMBRA_PREFIJO}%'"


@router.get("", response_model=list[LoteOut])
def listar_lotes():
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT * FROM lotes WHERE {_SIN_SOMBRA} ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/mapa")
def lotes_geojson():
    """Devuelve todos los lotes como FeatureCollection para renderizar en el mapa."""
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT * FROM lotes WHERE {_SIN_SOMBRA} ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    features = []
    for r in rows:
        features.append({
            "type": "Feature",
            "geometry": json.loads(r["geom_geojson"]),
            "properties": {
                "id": r["id"], "nombre": r["nombre"], "cultivo": r["cultivo"],
                "campo": r["campo"], "lote_num": r["lote_num"],
                "area_ha": r["area_ha"],
                "centroide_lat": r["centroide_lat"],
                "centroide_lon": r["centroide_lon"],
            },
        })
    return {"type": "FeatureCollection", "features": features}


@router.get("/{lote_id}", response_model=LoteOut)
def obtener_lote(lote_id: int):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM lotes WHERE id = ?", (lote_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Lote no encontrado.")
    return dict(row)


@router.post("/geojson", response_model=UploadResponse)
def alta_geojson(payload: GeoJSONUpload):
    """Alta de un lote desde GeoJSON crudo (p. ej. dibujado en el mapa)."""
    gj = payload.geojson
    if gj.get("type") == "FeatureCollection":
        feats = gj.get("features", [])
        if not feats:
            raise HTTPException(400, "FeatureCollection sin features.")
        gj = feats[0].get("geometry")
    elif gj.get("type") == "Feature":
        gj = gj.get("geometry")
    if not gj:
        raise HTTPException(400, "GeoJSON sin geometría.")
    nombre = payload.nombre or "Lote dibujado"
    res = _register_lote(shape(gj), nombre, cultivo=payload.cultivo)
    return UploadResponse(**res)


@router.post("/upload", response_model=list[UploadResponse])
async def upload_lotes(file: UploadFile = File(...)):
    """Alta desde archivo: GeoJSON (.json/.geojson) o shapefile comprimido (.zip)."""
    suffix = Path(file.filename or "").suffix.lower()
    raw = await file.read()

    if suffix in (".json", ".geojson"):
        try:
            gj = json.loads(raw.decode("utf-8"))
        except Exception:
            raise HTTPException(400, "GeoJSON inválido.")
        feats = (gj.get("features") if gj.get("type") == "FeatureCollection"
                 else [gj])
        out = []
        for f in feats:
            geom = f.get("geometry") if f.get("type") == "Feature" else f
            props = f.get("properties", {}) if isinstance(f, dict) else {}
            nombre = str(props.get("Nomb_campo") or props.get("nombre")
                         or f"Lote {len(out)+1}")
            res = _register_lote(shape(geom), nombre,
                                 cultivo=props.get("cultivo"),
                                 campo=props.get("Nomb_campo"),
                                 lote_num=str(props.get("Lote") or "") or None)
            out.append(UploadResponse(**res))
        return out

    if suffix == ".zip":
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        try:
            gdf = gpd.read_file(f"zip://{tmp_path}")
        except Exception as exc:
            raise HTTPException(400, f"No se pudo leer el shapefile: {exc}")
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        elif gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs("EPSG:4326")
        out = []
        for idx, row in gdf.iterrows():
            nombre = str(row.get("Nomb_campo", f"Lote {idx}")).strip()
            res = _register_lote(
                row["geometry"], nombre,
                cultivo=str(row.get("cultivo", "")) or None,
                campo=str(row.get("Nomb_campo", "")) or None,
                lote_num=str(row.get("Lote", "")) or None)
            out.append(UploadResponse(**res))
        return out

    raise HTTPException(400, "Formato no soportado. Use .geojson, .json o .zip.")


@router.get("/{lote_id}/serie-temporal", response_model=SerieTemporalOut)
def serie_temporal(lote_id: int, background: BackgroundTasks,
                   indice: str = "NDVI"):
    """Devuelve la serie cacheada. Si no hay datos, dispara extracción en fondo."""
    conn = get_conn()
    try:
        if not conn.execute("SELECT 1 FROM lotes WHERE id = ?",
                            (lote_id,)).fetchone():
            raise HTTPException(404, "Lote no encontrado.")
        rows = conn.execute(
            "SELECT fecha, indice, valor, valor_min, valor_max, valor_std, "
            "       sensor, orbita, pct_valido "
            "FROM series_temporales WHERE lote_id = ? AND indice = ? "
            "ORDER BY fecha", (lote_id, indice.upper()),
        ).fetchall()
    finally:
        conn.close()

    if rows:
        return SerieTemporalOut(
            lote_id=lote_id, indice=indice.upper(),
            puntos=[dict(r) for r in rows], cacheado=True)

    # Sin datos cacheados: lanzar extracción en segundo plano.
    job_id = create_job("extraccion", lote_id=lote_id,
                        mensaje="Extracción de series satelitales en cola...")
    background.add_task(run_extraction_job, job_id, lote_id)
    return SerieTemporalOut(
        lote_id=lote_id, indice=indice.upper(), puntos=[], cacheado=False,
        job_id=job_id,
        mensaje="No había datos cacheados. Se inició la extracción; "
                "consultá el progreso en GET /api/jobs/{job_id}.")
