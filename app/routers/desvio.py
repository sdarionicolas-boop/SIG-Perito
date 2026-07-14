"""Endpoint de alertas de desvío de NDVI vs. baseline regional (normativa tipo 5267)."""
from fastapi import APIRouter, HTTPException

from app.services.desvio import evaluar_desvio

router = APIRouter(prefix="/api/lotes", tags=["desvio"])


@router.get("/{lote_id}/desvio-ndvi")
def desvio_ndvi(lote_id: int, umbral_pct: float = 15.0):
    try:
        return evaluar_desvio(lote_id, umbral_pct=umbral_pct)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
