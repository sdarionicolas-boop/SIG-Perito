"""Listado y búsqueda de siniestros (casos de peritaje con metadatos de póliza).

Complementa a `/api/lotes/{id}/peritaje` (que sólo devuelve el último caso de UN
lote): este endpoint permite buscar entre TODOS los casos registrados, por
aseguradora o número de póliza — el flujo real de una compañía gestionando
varios siniestros del mismo temporal.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.services.peritaje import listar_siniestros

router = APIRouter(prefix="/api/siniestros", tags=["siniestros"])


@router.get("")
def get_siniestros(
    aseguradora: str | None = None,
    numero_poliza: str | None = None,
    lote_id: int | None = None,
):
    """Lista siniestros registrados, opcionalmente filtrados."""
    return listar_siniestros(
        aseguradora=aseguradora, numero_poliza=numero_poliza, lote_id=lote_id)
