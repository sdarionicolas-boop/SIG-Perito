# app/services/alertas_clima.py
"""Alertas de tormentas severas con enfoque probabilístico (ensemble GEFS).

Para lotes reales se consulta el *ensemble* GEFS de la NOAA vía Open-Meteo
(31 miembros). En lugar de un umbral binario sobre una corrida única, se reporta
la PROBABILIDAD del evento = fracción de miembros del ensemble que superan el
umbral en algún momento del horizonte. Esto materializa el marco consultivo:
"20 de 31 escenarios modelan granizo (65%)" en vez de una certeza que no existe.

Contrato de 3 estados (consumido por el frontend):
  * evaluado con alertas   -> hay_alertas=True,  alertas_lista=[...]
  * evaluado sin alertas   -> hay_alertas=False, alertas_lista=[]
  * NO evaluado (fallo)    -> hay_alertas=False + clave 'error' (banner ámbar)
"""
import re
import statistics

import requests

from app.config import DB_PATH  # noqa: F401  (lo patchean los tests vía ac.DB_PATH)
from app.database import get_conn

# --- Umbrales físicos ---------------------------------------------------------
UMBRAL_CAPE = 2000.0          # J/kg -> Inestabilidad convectiva severa (granizo)
UMBRAL_RAFAGAS = 50.0         # km/h -> Daño físico / interrupción de pulverizaciones
UMBRAL_LLUVIA = 15.0          # mm/h -> Lluvia extrema / anegamiento localizado

# --- Ensemble GEFS (Open-Meteo) ----------------------------------------------
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
GEFS_MODEL = "gfs_seamless"   # GEFS: 1 control + 30 perturbados = 31 miembros
PROB_MIN_AVISO = 0.15         # probabilidad mínima para siquiera mostrar un aviso


def evaluar_alertas_tormenta(lote_id: int) -> dict:
    """Punto de entrada: resuelve el lote, aplica demos y delega en el ensemble."""
    # 1. Coordenadas y nombre del lote (helper de la app -> sin fugas de conexión)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT nombre, centroide_lat, centroide_lon FROM lotes WHERE id = ?",
            (lote_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise ValueError("Lote no encontrado")

    nombre_upper = row["nombre"].upper()
    lat = row["centroide_lat"]
    lon = row["centroide_lon"]

    # 2. Inyección de demo controlada (ahora con probabilidad para la nueva UI)
    if "DEMO 2" in nombre_upper:
        return {
            "hay_alertas": True,
            "alertas_lista": [
                {
                    "tipo": "Riesgo de Granizo",
                    "probabilidad": 0.87, "prob_pct": 87,
                    "mensaje": "Inestabilidad convectiva severa: 27 de 31 escenarios del "
                               "ensemble GEFS superan el umbral de granizo (CAPE mediana "
                               "1750 J/kg). Potencial de tormenta severa con granizo.",
                    "fecha": "Mañana 18:00 hs",
                    "gravedad": "alta",
                },
                {
                    "tipo": "Riesgo de Vientos Fuertes",
                    "probabilidad": 0.61, "prob_pct": 61,
                    "mensaje": "Ráfagas intensas: 19 de 31 escenarios superan el umbral "
                               "(mediana 62 km/h). Precaución en aplicaciones agrícolas.",
                    "fecha": "Mañana 19:00 hs",
                    "gravedad": "media",
                },
            ],
            "fuente": "simulado",
        }

    if "DEMO 1" in nombre_upper:
        return {
            "hay_alertas": True,
            "alertas_lista": [
                {
                    "tipo": "Riesgo de Vientos Fuertes",
                    "probabilidad": 0.55, "prob_pct": 55,
                    "mensaje": "Ráfagas intensas: 17 de 31 escenarios superan el umbral "
                               "(mediana 54 km/h). Precaución en pulverizaciones para "
                               "evitar derivas.",
                    "fecha": "Hoy 21:00 hs",
                    "gravedad": "media",
                },
            ],
            "fuente": "simulado",
        }

    # 3. Lote real: ensemble en vivo
    if lat is None or lon is None:
        return {"hay_alertas": False, "alertas_lista": [], "fuente": "real"}

    return evaluar_ensemble(lat, lon)


# =============================================================================
# Motor probabilístico GEFS
# =============================================================================
def _series_por_miembro(hourly: dict, var: str) -> list[list]:
    """Agrupa las series de todos los miembros del ensemble para una variable.

    Open-Meteo nombra las claves como 'cape' (control) + 'cape_member01'..NN.
    El regex captura ambas; con un modelo único (una sola clave 'cape') el
    resultado es un ensemble de 1 miembro, lo que preserva compatibilidad.
    El orden entre miembros es irrelevante (se agrega simétricamente).
    """
    patron = re.compile(rf"^{re.escape(var)}(_member\d+)?$")
    claves = sorted(k for k in hourly if patron.match(k))
    return [hourly[k] for k in claves]


def _prob_evento(miembros: list[list], umbral: float, times: list[str]) -> dict | None:
    """Probabilidad ensemble de superar `umbral` en ALGÚN momento del horizonte.

    Se evalúa POR MIEMBRO (cada miembro es un escenario temporalmente coherente),
    no combinando horas como si fueran independientes. Devuelve probabilidad,
    intensidad mediana del pico y la hora de máximo consenso, o None si la
    probabilidad no alcanza el mínimo para avisar.
    """
    n = len(miembros)
    if n == 0:
        return None
    n_horas = max((len(m) for m in miembros), default=0)
    if n_horas == 0:
        return None

    picos: list[float] = []
    excede_por_hora = [0] * n_horas
    for serie in miembros:
        pico = 0.0
        for i in range(n_horas):
            v = serie[i] if i < len(serie) else None
            if v is None:
                continue
            v = float(v)
            if v > pico:
                pico = v
            if v >= umbral:
                excede_por_hora[i] += 1
        picos.append(pico)

    n_cruzan = sum(1 for p in picos if p >= umbral)
    prob = n_cruzan / n
    if prob < PROB_MIN_AVISO:
        return None

    idx_critico = max(range(n_horas), key=lambda i: excede_por_hora[i])
    hora_critica = times[idx_critico].replace("T", " ") if idx_critico < len(times) else None
    return {
        "probabilidad": round(prob, 2),
        "miembros_totales": n,
        "miembros_evento": n_cruzan,
        "intensidad_mediana": round(statistics.median(picos), 1),
        "hora_critica": hora_critica,
    }


def _gravedad(prob: float) -> str:
    if prob >= 0.70:
        return "alta"
    if prob >= 0.40:
        return "media"
    return "baja"          # 0.15–0.40 -> vigilancia


# (variable, umbral, tipo, plantilla de mensaje)
_FENOMENOS = [
    ("cape", UMBRAL_CAPE, "Riesgo de Granizo",
     "Inestabilidad convectiva severa: {miembros_evento} de {miembros_totales} "
     "escenarios del ensemble GEFS superan el umbral de granizo (CAPE mediana "
     "{intensidad_mediana:.0f} J/kg). Potencial de tormenta severa con granizo."),
    ("wind_gusts_10m", UMBRAL_RAFAGAS, "Riesgo de Vientos Fuertes",
     "Ráfagas intensas: {miembros_evento} de {miembros_totales} escenarios superan "
     "el umbral (mediana {intensidad_mediana:.0f} km/h). Precaución en pulverizaciones "
     "y por daño físico a cultivos."),
    ("precipitation", UMBRAL_LLUVIA, "Riesgo de Lluvia Intensa",
     "Lluvia intensa: {miembros_evento} de {miembros_totales} escenarios superan el "
     "umbral (mediana {intensidad_mediana:.1f} mm/h). Riesgo de anegamientos y lavado "
     "foliar de aplicaciones."),
]


def evaluar_ensemble(lat: float, lon: float) -> dict:
    """Consulta el ensemble GEFS y devuelve alertas probabilísticas (3 estados)."""
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "cape,wind_gusts_10m,precipitation",
        "models": GEFS_MODEL,
        "forecast_days": 3,
        "timezone": "America/Argentina/Cordoba",
    }
    try:
        # Timeout separado: 5s conexión, 25s lectura (cold-cache del ensemble)
        r = requests.get(ENSEMBLE_URL, params=params, timeout=(5, 25))
        if not r.ok:
            return {"hay_alertas": False, "alertas_lista": [], "fuente": "real",
                    "error": f"HTTP {r.status_code}"}
        hourly = r.json().get("hourly", {})
        times = hourly.get("time", [])
    except Exception as e:
        # NO evaluado -> el frontend muestra 'Riesgo no verificado', nunca "sin alertas"
        return {"hay_alertas": False, "alertas_lista": [], "fuente": "real",
                "error": str(e)}

    alertas = []
    for var, umbral, tipo, plantilla in _FENOMENOS:
        res = _prob_evento(_series_por_miembro(hourly, var), umbral, times)
        if res is None:
            continue
        alertas.append({
            "tipo": tipo,
            "probabilidad": res["probabilidad"],
            "prob_pct": round(res["probabilidad"] * 100),
            "mensaje": plantilla.format(**res),
            "fecha": res["hora_critica"],
            "gravedad": _gravedad(res["probabilidad"]),
        })

    # Orden estable: primero lo más probable
    alertas.sort(key=lambda a: a["probabilidad"], reverse=True)
    return {"hay_alertas": len(alertas) > 0, "alertas_lista": alertas, "fuente": "real"}
