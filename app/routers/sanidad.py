"""Endpoint de riesgo de enfermedades (sanidad)."""
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.services.sanidad import evaluar_riesgo_lote, procesar_clima_data

router = APIRouter(prefix="/api/lotes", tags=["sanidad"])


@router.get("/{lote_id}/sanidad")
def sanidad_lote(lote_id: int, cultivo: Optional[str] = None,
                 zona: Optional[str] = None, etapa: Optional[str] = None):
    """Evalúa el riesgo epidemiológico del lote (clima consultado en el servidor)."""
    try:
        return evaluar_riesgo_lote(lote_id, cultivo=cultivo, zona=zona, etapa=etapa)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{lote_id}/sanidad")
def sanidad_lote_post(lote_id: int, raw_data: dict, cultivo: Optional[str] = None,
                      zona: Optional[str] = None, etapa: Optional[str] = None):
    """Igual que GET pero el clima viene del navegador (bypass del 429 de Open-Meteo).

    `raw_data` es el JSON crudo de Open-Meteo forecast (past_days=15) consultado
    desde el cliente; el backend solo lo agrega y puntúa.
    """
    try:
        clima = procesar_clima_data(raw_data, lote_id)
        return evaluar_riesgo_lote(lote_id, cultivo=cultivo, zona=zona,
                                   etapa=etapa, clima=clima)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
