"""Endpoint de avance de cosecha escalonada (lee reportes del pipeline S1 RVI)."""
from fastapi import APIRouter, HTTPException

from app.services.cosecha import obtener_progreso_cosecha

router = APIRouter(prefix="/api/lotes", tags=["cosecha"])


@router.get("/{lote_id}/cosecha")
def cosecha_lote(lote_id: int):
    """Serie de avance de cosecha acumulado del lote (0-100% por fecha de paso S1)."""
    try:
        return obtener_progreso_cosecha(lote_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
