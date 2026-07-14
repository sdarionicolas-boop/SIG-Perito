"""Endpoints de validación de consistencia y métricas operativas (Fase 5)."""
from fastapi import APIRouter, HTTPException

from app.metrics import resumen_latencia
from app.services.uso import resumen_uso
from app.services.validacion import validar_lote, validar_todos

router = APIRouter(tags=["validacion"])


@router.get("/api/lotes/{lote_id}/validacion")
def validacion_lote(lote_id: int):
    try:
        return validar_lote(lote_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/api/validacion")
def validacion_global():
    return validar_todos()


@router.get("/api/admin/metricas")
def metricas():
    """Latencia por endpoint (in-memory) + consumo estimado de Processing Units."""
    return {"latencia": resumen_latencia(), "uso_api": resumen_uso()}
