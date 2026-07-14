"""Endpoint de pronóstico agrometeorológico (Open-Meteo GFS)."""
from fastapi import APIRouter, HTTPException

from app.database import get_conn
from app.schemas import ForecastOut
from app.services.clima import obtener_forecast, procesar_forecast_data

router = APIRouter(prefix="/api/lotes", tags=["clima"])


@router.get("/{lote_id}/forecast", response_model=ForecastOut)
def forecast_lote(lote_id: int):
    """Pronóstico GFS a 16 días por el centroide del lote."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT centroide_lat, centroide_lon FROM lotes WHERE id = ?",
            (lote_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Lote no encontrado.")
    if row["centroide_lat"] is None or row["centroide_lon"] is None:
        raise HTTPException(422, "El lote no tiene centroide calculado.")

    try:
        data = obtener_forecast(row["centroide_lat"], row["centroide_lon"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Error consultando Open-Meteo: {exc}")

    return ForecastOut(lote_id=lote_id, **data)


@router.post("/{lote_id}/forecast", response_model=ForecastOut)
def procesar_forecast_lote(lote_id: int, raw_data: dict):
    """Procesa datos brutos de Open-Meteo GFS provistos por el frontend."""
    conn = get_conn()
    try:
        exists = conn.execute(
            "SELECT 1 FROM lotes WHERE id = ?",
            (lote_id,),
        ).fetchone()
    finally:
        conn.close()
    if not exists:
        raise HTTPException(404, "Lote no encontrado.")

    try:
        data = procesar_forecast_data(raw_data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"Error procesando datos climáticos: {exc}")

    return ForecastOut(lote_id=lote_id, **data)
