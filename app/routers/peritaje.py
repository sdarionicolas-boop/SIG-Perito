"""Endpoint de peritaje satelital de eventualidades.

Recibe un lote_id, la fecha del evento y el tipo, y lanza el pipeline como
background job (mismo modelo que `/api/lotes/{id}/zonificacion` y la extracción
de series temporales).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from app.database import get_conn
from app.jobs import create_job
from app.services.peritaje import run_peritaje_job, ultimo_peritaje

TipoEvento = Literal["granizo", "helada", "viento", "sequia", "inundacion"]

router = APIRouter(prefix="/api/lotes", tags=["peritaje"])


class PeritajeIn(BaseModel):
    fecha_evento: str = Field(
        ..., pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Fecha del evento, YYYY-MM-DD. Para tormentas nocturnas "
                    "(granizo/viento), declarar el DÍA DESPUÉS de la noche de "
                    "tormenta (si fue la noche del 18 al 19, usar '19') — el "
                    "escaneo GOES (overshooting/rayos) busca la noche anterior "
                    "a esta fecha y no tolera el día equivocado, a diferencia "
                    "de NDVI/ERA5 que sí lo toleran.")
    tipo_evento: TipoEvento = "granizo"
    ventana_dias: int = Field(14, ge=3, le=60,
                              description="Días antes y después del evento")
    baseline_anos: int = Field(3, ge=0, le=5,
                               description="Años previos para el baseline fenológico")
    # Metadatos de póliza/siniestro — opcionales, no afectan el cómputo satelital.
    # Se guardan en la tabla `siniestros` y se inyectan en el reporte HTML/KML.
    aseguradora: str | None = Field(None, max_length=200)
    numero_poliza: str | None = Field(None, max_length=100)
    productor: str | None = Field(None, max_length=200)
    comentarios_perito: str | None = Field(None, max_length=2000)


@router.post("/{lote_id}/peritaje", status_code=202)
def lanzar_peritaje(
    lote_id: int, body: PeritajeIn, bg: BackgroundTasks,
):
    """Lanza el peritaje asincrónicamente. Devuelve el `job_id` para sondear."""
    conn = get_conn()
    try:
        exists = conn.execute(
            "SELECT 1 FROM lotes WHERE id = ?", (lote_id,)
        ).fetchone()
    finally:
        conn.close()
    if not exists:
        raise HTTPException(404, f"Lote {lote_id} no encontrado")

    job_id = create_job(
        "peritaje", lote_id=lote_id,
        mensaje=f"En cola: {body.tipo_evento} @ {body.fecha_evento}")
    bg.add_task(
        run_peritaje_job,
        job_id, lote_id,
        body.fecha_evento, body.tipo_evento,
        body.ventana_dias, body.baseline_anos,
        body.aseguradora, body.numero_poliza,
        body.productor, body.comentarios_perito,
    )
    return {"job_id": job_id, "estado": "PENDING"}


@router.get("/{lote_id}/peritaje")
def obtener_peritaje(lote_id: int):
    """Devuelve el resultado del peritaje más reciente del lote (o 404)."""
    res = ultimo_peritaje(lote_id)
    if res is None:
        raise HTTPException(404, "Sin peritaje previo. Lanzá POST /peritaje primero.")
    return res
