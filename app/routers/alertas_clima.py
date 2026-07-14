# app/routers/alertas_clima.py
from fastapi import APIRouter, HTTPException

from app.database import get_conn
from app.services.alertas_clima import evaluar_alertas_tormenta
from app.services.goes_glm import rayos_cercanos
from app.services.goes_ot import overshooting_cercano
from app.services.smn_avisos import avisos_para_lote
from app.services.wrf_smn import serie_precipitacion

router = APIRouter(prefix="/api/lotes", tags=["alertas-clima"])


@router.get("/{lote_id}/alertas-clima")
def get_alertas_clima(lote_id: int):
    try:
        return evaluar_alertas_tormenta(lote_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/{lote_id}/precipitacion-wrf")
def get_precipitacion_wrf(lote_id: int, horas: int = 12):
    """Serie de precipitación de alta resolución (WRF-DET 4 km del SMN).

    Descarga bajo demanda (cada archivo pesa ~17 MB): el frontend la dispara sólo
    cuando el usuario lo pide, no en cada carga del panel. `horas` se acota a 1..24.
    """
    horas = min(max(horas, 1), 24)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT centroide_lat, centroide_lon FROM lotes WHERE id = ?", (lote_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Lote no encontrado")
    lat, lon = row["centroide_lat"], row["centroide_lon"]
    if lat is None or lon is None:
        return {"disponible": False, "fuente": "wrf-smn", "error": "lote sin coordenadas"}
    return serie_precipitacion(lat, lon, leads=range(1, horas + 1))


@router.get("/{lote_id}/rayos-goes")
def get_rayos_goes(lote_id: int, radio_km: float = 40, ventana_min: int = 10):
    """Actividad eléctrica en tiempo real (GOES-19 GLM) alrededor del lote.

    Descarga bajo demanda (~45 MB por ventana de 10 min): el frontend la dispara
    sólo cuando el usuario lo pide. `radio_km` 5..200, `ventana_min` 5..30.
    """
    radio_km = min(max(radio_km, 5), 200)
    ventana_min = min(max(ventana_min, 5), 30)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT centroide_lat, centroide_lon FROM lotes WHERE id = ?", (lote_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Lote no encontrado")
    lat, lon = row["centroide_lat"], row["centroide_lon"]
    if lat is None or lon is None:
        return {"disponible": False, "fuente": "goes19-glm", "error": "lote sin coordenadas"}
    return rayos_cercanos(lat, lon, radio_km=radio_km, ventana_min=ventana_min)


@router.get("/{lote_id}/overshooting-goes")
def get_overshooting_goes(lote_id: int, radio_km: float = 30):
    """Topes nubosos penetrantes (GOES-19 ABI Cloud Top Temperature) sobre el lote.

    Detecta cúpulas que penetran la tropopausa —firma de tormenta severa con
    potencial de granizo grande—. Descarga bajo demanda (disco completo, decenas
    de MB): el frontend lo dispara sólo cuando el usuario lo pide. `radio_km` 5..100.
    """
    radio_km = min(max(radio_km, 5), 100)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT centroide_lat, centroide_lon FROM lotes WHERE id = ?", (lote_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Lote no encontrado")
    lat, lon = row["centroide_lat"], row["centroide_lon"]
    if lat is None or lon is None:
        return {"disponible": False, "fuente": "goes19-abi-acht", "error": "lote sin coordenadas"}
    return overshooting_cercano(lat, lon, radio_km=radio_km)


@router.get("/{lote_id}/avisos-smn")
def get_avisos_smn(lote_id: int):
    """Avisos OFICIALES del SMN (vía Alert Hub CAP de la WMO) que cubren el lote.

    Es la capa de mayor peso legal: cada aviso viene firmado por el SMN. La
    primera consulta baja ~100 CAP XMLs (luego cachea 5 min por el set nacional).
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT centroide_lat, centroide_lon FROM lotes WHERE id = ?", (lote_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Lote no encontrado")
    lat, lon = row["centroide_lat"], row["centroide_lon"]
    if lat is None or lon is None:
        return {"disponible": False, "fuente": "smn-wmo-cap", "error": "lote sin coordenadas"}
    return avisos_para_lote(lat, lon)
