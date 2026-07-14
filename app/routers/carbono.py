"""Endpoints de Carbono Orgánico del Suelo (COS) y Finanzas Verdes."""

import json
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from shapely.geometry import shape

from app.database import get_conn
from app.services import soilgrids
from app.services.uso_suelo import obtener_cobertura

router = APIRouter(prefix="/api/carbono", tags=["carbono"])


class SOCDepthOut(BaseModel):
    depth: str
    mean: float
    uncertainty_low: float
    uncertainty_high: float
    unit: str


class CarbonoResultOut(BaseModel):
    lote_id: int
    soc_by_depth: list[SOCDepthOut]
    total_stock_0_30cm_t_c_ha: float
    total_stock_lote_t_c: float
    co2e_por_ha_t: float
    co2e_total_t: float
    alerta_emision_arado_t: float
    elegibilidad: str  # ALTA, MEDIA, BAJA
    pastura_pct: float
    es_pastizal: bool
    above_national_mean: bool
    source: str
    bulk_density_used: float
    centroid: list[float]  # [lon, lat]


@router.get("/{lote_id}", response_model=CarbonoResultOut)
def obtener_carbono_lote(lote_id: int):
    """Calcula el stock de carbono en el suelo (0-30 cm) y métricas de finanzas verdes."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM lotes WHERE id = ?", (lote_id,)).fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No se encontró el lote con ID {lote_id}.",
            )
        lote = dict(row)
    finally:
        conn.close()

    lat = lote.get("centroide_lat")
    lon = lote.get("centroide_lon")
    area_ha = lote.get("area_ha") or 1.0

    if lat is None or lon is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El lote seleccionado no tiene coordenadas de centroide válidas.",
        )

    # Reconstruir geometría Shapely desde el GeoJSON de la base de datos
    geom_shapely = None
    if lote.get("geom_geojson"):
        try:
            geom_shapely = shape(json.loads(lote["geom_geojson"]))
        except Exception as exc:
            # Si falla la carga de geometría, continuará usando coordenadas para SoilGrids
            pass

    # 1) SOC precalculado (CSV, dato INTA real e instantáneo; funciona en HF sin rásters).
    # 2) Fallback: estadística zonal sobre los rásters INTA locales, o SoilGrids global.
    res = soilgrids.soc_de_lote(lote.get("nombre", ""))
    if res is None:
        try:
            res = soilgrids.analyze_soc_for_geom_or_coords(lon, lat, geom_shapely)
        except soilgrids.SoilGridsError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Error consultando el stock de carbono: {exc}",
            )

    soc_by_depth = [
        SOCDepthOut(
            depth=s.depth,
            mean=s.mean,
            uncertainty_low=s.uncertainty_low,
            uncertainty_high=s.uncertainty_high,
            unit=s.unit,
        )
        for s in res.stocks
    ]

    total_stock_0_30cm_t_c_ha = round(sum(s.mean for s in res.stocks), 2)
    total_stock_lote_t_c = round(total_stock_0_30cm_t_c_ha * area_ha, 2)

    # Conversión a CO2 equivalente (peso molecular CO2/C = 44/12 ≈ 3.67)
    co2e_por_ha_t = round(total_stock_0_30cm_t_c_ha * 3.67, 2)
    co2e_total_t = round(total_stock_lote_t_c * 3.67, 2)

    # Pérdida por labranza (IPCC - 20% del stock de carbono liberado como CO2)
    alerta_emision_arado_t = round(co2e_total_t * 0.20, 2)

    # Uso de suelo actual (para contextualizar la alerta: pastizal vs. ya agrícola).
    try:
        cob_actual = obtener_cobertura(lote.get("nombre", ""), 2024)["cobertura"]
        pastura_pct = round(cob_actual.get("Pastura", 0.0), 1)
    except Exception:  # noqa: BLE001
        pastura_pct = 0.0
    es_pastizal = pastura_pct >= 50.0

    # Comparación con la media nacional de Argentina de INTA (51.35 t C/ha)
    above_national_mean = total_stock_0_30cm_t_c_ha > 51.35

    # Clasificación de elegibilidad
    if total_stock_0_30cm_t_c_ha > 60.0:
        elegibilidad = "ALTA"
    elif total_stock_0_30cm_t_c_ha >= 45.0:
        elegibilidad = "MEDIA"
    else:
        elegibilidad = "BAJA"

    return CarbonoResultOut(
        lote_id=lote_id,
        soc_by_depth=soc_by_depth,
        total_stock_0_30cm_t_c_ha=total_stock_0_30cm_t_c_ha,
        total_stock_lote_t_c=total_stock_lote_t_c,
        co2e_por_ha_t=co2e_por_ha_t,
        co2e_total_t=co2e_total_t,
        alerta_emision_arado_t=alerta_emision_arado_t,
        elegibilidad=elegibilidad,
        pastura_pct=pastura_pct,
        es_pastizal=es_pastizal,
        above_national_mean=above_national_mean,
        source=res.source,
        bulk_density_used=res.bulk_density_used,
        centroid=[lon, lat],
    )
