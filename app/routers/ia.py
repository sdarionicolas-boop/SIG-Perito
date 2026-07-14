"""Endpoints de IA local: zonificación KMeans, margen bruto y reglas de rinde."""
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.database import get_conn
from app.jobs import create_job
from app.schemas import MargenInput
from app.services.rinde import estimar_rinde_rue, evaluar_rinde, margen_bruto_zonal
from app.services.zonificacion import run_zonificacion_job

router = APIRouter(prefix="/api/lotes", tags=["ia"])


def _existe_lote(lote_id: int) -> bool:
    conn = get_conn()
    try:
        return conn.execute("SELECT 1 FROM lotes WHERE id=?", (lote_id,)).fetchone() is not None
    finally:
        conn.close()


@router.post("/{lote_id}/zonificacion")
def lanzar_zonificacion(lote_id: int, background: BackgroundTasks, k: int = 3):
    """Dispara la zonificación KMeans en segundo plano (descarga NDVI + clustering)."""
    if not _existe_lote(lote_id):
        raise HTTPException(404, "Lote no encontrado.")
    if k < 2 or k > 6:
        raise HTTPException(422, "k debe estar entre 2 y 6.")
    job_id = create_job("zonificacion", lote_id=lote_id,
                        mensaje="Zonificación en cola...")
    background.add_task(run_zonificacion_job, job_id, lote_id, k)
    return {"job_id": job_id, "mensaje": "Zonificación iniciada. "
            "Seguí el progreso en GET /api/jobs/{job_id} y luego "
            "GET /api/lotes/{id}/zonificacion."}


@router.get("/{lote_id}/zonificacion")
def obtener_zonificacion(lote_id: int):
    """Devuelve la última zonificación calculada para el lote."""
    conn = get_conn()
    try:
        z = conn.execute(
            "SELECT * FROM zonificaciones WHERE lote_id=? "
            "ORDER BY created_at DESC LIMIT 1", (lote_id,)).fetchone()
    finally:
        conn.close()
    if not z:
        raise HTTPException(404, "Sin zonificación. Ejecutá POST /zonificacion primero.")
    return {
        "lote_id": lote_id, "fecha_pico": z["fecha_pico"], "k": z["k"],
        "ndvi_medio": z["ndvi_medio"], "zonas": json.loads(z["zonas_json"]),
        "png_url": z["png_path"], "bounds": json.loads(z["bounds_json"]),
        "created_at": z["created_at"],
    }


@router.post("/{lote_id}/margen-bruto")
def calcular_margen(lote_id: int, datos: MargenInput):
    """Calcula el margen bruto por zona usando la última zonificación."""
    if not _existe_lote(lote_id):
        raise HTTPException(404, "Lote no encontrado.")
    try:
        return margen_bruto_zonal(lote_id, datos.rinde_objetivo,
                                  datos.precio, datos.costo_base)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.get("/{lote_id}/rinde")
def rinde_lote(lote_id: int):
    """Evalúa la penalización de rinde por reglas agronómicas."""
    if not _existe_lote(lote_id):
        raise HTTPException(404, "Lote no encontrado.")
    try:
        return evaluar_rinde(lote_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.get("/{lote_id}/rinde-potencial")
def rinde_potencial_lote(lote_id: int, cultivo: str | None = None):
    """Estima el rinde potencial (kg/ha) por el modelo RUE-fAPAR-PAR (Monteith).

    Complementa a /rinde: en vez de penalizar, integra la biomasa producida
    según la luz interceptada durante el ciclo. `cultivo` opcional (default: el
    del lote, o maní): mani | soja | maiz | trigo | girasol.
    """
    if not _existe_lote(lote_id):
        raise HTTPException(404, "Lote no encontrado.")
    try:
        return estimar_rinde_rue(lote_id, cultivo)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))
