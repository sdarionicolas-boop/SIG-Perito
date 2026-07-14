"""Motor de reglas de penalización de rinde y cálculo de margen bruto zonal.

El estimador es deliberadamente basado en REGLAS agronómicas transparentes
(no un modelo entrenado): cada factor aporta un puntaje y se explicita su porqué.
La firma `evaluar_rinde(lote_id) -> dict` está pensada para reemplazarse luego por
un Random Forest sin cambiar el contrato del endpoint.
"""
import datetime as dt
import json
import sys
import time

import numpy as np
import requests

from app.config import BASE_DIR
from app.database import get_conn

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
from extractor.analizar_cosecha import despike_ndvi, detectar_cosecha_sar  # noqa: E402

# Umbrales empíricos (literatura agronómica + calibración local de la serie 2026).
NDVI_PICO_BAJO = 0.60       # pico de vigor pobre -> biomasa insuficiente
NDVI_PICO_MUY_BAJO = 0.45
PENDIENTE_ABRUPTA = 0.020   # caída de NDVI/día desde el pico (senescencia forzada)
RVI_CAIDA_OK = 0.40         # caída de RVI pico->piso esperada en cosecha normal
# NDWI (Gao) cerca del pico: canopeo bien hidratado ~>0.10; por debajo sugiere
# bajo contenido de agua foliar (estrés hídrico). Umbrales empíricos calibrables.
NDWI_ESTRES = 0.10
NDWI_ESTRES_SEVERO = 0.0
VENTANA_PICO_DIAS = 21      # ventana ± alrededor del pico para promediar NDWI


def _series(lote_id: int):
    conn = get_conn()
    try:
        ndvi = conn.execute(
            "SELECT fecha, valor FROM series_temporales "
            "WHERE lote_id=? AND indice='NDVI' ORDER BY fecha", (lote_id,)).fetchall()
        rvi = conn.execute(
            "SELECT fecha, valor, orbita FROM series_temporales "
            "WHERE lote_id=? AND indice='RVI' ORDER BY fecha", (lote_id,)).fetchall()
        ndwi = conn.execute(
            "SELECT fecha, valor FROM series_temporales "
            "WHERE lote_id=? AND indice='NDWI' ORDER BY fecha", (lote_id,)).fetchall()
    finally:
        conn.close()
    return ndvi, rvi, ndwi


def _features(ndvi_rows, rvi_rows, ndwi_rows) -> dict:
    """Extrae las variables que alimentan las reglas."""
    import datetime as dt
    fechas = [r["fecha"] for r in ndvi_rows]
    vals = despike_ndvi([float(r["valor"]) for r in ndvi_rows])
    idx_pico = int(np.argmax(vals))
    ndvi_pico = float(vals[idx_pico])
    ndvi_min = float(np.min(vals))
    fecha_pico = fechas[idx_pico]

    # NDWI cerca del pico de vigor: promedio dentro de ±VENTANA_PICO_DIAS días.
    ndwi_pico = None
    if ndwi_rows:
        d_pico = dt.date.fromisoformat(fecha_pico)
        cercanos = [float(r["valor"]) for r in ndwi_rows
                    if abs((dt.date.fromisoformat(r["fecha"]) - d_pico).days) <= VENTANA_PICO_DIAS]
        if not cercanos:  # sin obs cerca del pico -> usar el mínimo de la serie
            cercanos = [min(float(r["valor"]) for r in ndwi_rows)]
        ndwi_pico = round(sum(cercanos) / len(cercanos), 4)

    # Pendiente media de caída (NDVI/día) desde el pico hasta el final de la serie.
    pendiente = None
    if idx_pico < len(vals) - 1:
        d0 = dt.date.fromisoformat(fechas[idx_pico])
        d1 = dt.date.fromisoformat(fechas[-1])
        dias = (d1 - d0).days
        if dias > 0:
            pendiente = round((vals[idx_pico] - vals[-1]) / dias, 5)

    # Caída de RVI (estructura): pico->piso vía detector SAR del extractor.
    rvi_caida = None
    if rvi_rows:
        import pandas as pd
        df = pd.DataFrame([{"lote_id": 0, "fecha": r["fecha"],
                            "rvi_medio": float(r["valor"]),
                            "orbita": r["orbita"]} for r in rvi_rows])
        df["fecha"] = pd.to_datetime(df["fecha"])
        sar = detectar_cosecha_sar(df)
        if sar:
            rvi_caida = round(sar["rvi_peak"] - sar["rvi_floor"], 3)

    return {
        "ndvi_pico": round(ndvi_pico, 4), "fecha_pico": fecha_pico,
        "ndvi_min": round(ndvi_min, 4),
        "pendiente_caida": pendiente, "rvi_caida": rvi_caida,
        "ndwi_pico": ndwi_pico,
        "ndwi_disponible": ndwi_pico is not None,
    }


def _reglas(f: dict) -> tuple[list[dict], int]:
    """Aplica las reglas y devuelve (factores, score). Score alto = más penalización."""
    factores, score = [], 0

    if f["ndvi_pico"] < NDVI_PICO_MUY_BAJO:
        score += 3
        factores.append({"factor": "pico_vigor", "severidad": "alta",
                         "detalle": f"NDVI pico muy bajo ({f['ndvi_pico']}) < {NDVI_PICO_MUY_BAJO}: "
                                    "biomasa claramente insuficiente."})
    elif f["ndvi_pico"] < NDVI_PICO_BAJO:
        score += 2
        factores.append({"factor": "pico_vigor", "severidad": "media",
                         "detalle": f"NDVI pico bajo ({f['ndvi_pico']}) < {NDVI_PICO_BAJO}: "
                                    "desarrollo de canopia por debajo del óptimo."})
    else:
        factores.append({"factor": "pico_vigor", "severidad": "ok",
                         "detalle": f"NDVI pico saludable ({f['ndvi_pico']})."})

    if f["pendiente_caida"] is not None and f["pendiente_caida"] > PENDIENTE_ABRUPTA:
        score += 2
        factores.append({"factor": "senescencia", "severidad": "media",
                         "detalle": f"Caída de NDVI abrupta ({f['pendiente_caida']}/día) > "
                                    f"{PENDIENTE_ABRUPTA}: posible estrés/senescencia forzada."})
    else:
        factores.append({"factor": "senescencia", "severidad": "ok",
                         "detalle": "Ritmo de senescencia dentro de lo esperado."})

    if f["rvi_caida"] is not None and f["rvi_caida"] < RVI_CAIDA_OK:
        score += 1
        factores.append({"factor": "cosecha_sar", "severidad": "baja",
                         "detalle": f"Caída de RVI débil ({f['rvi_caida']}): señal de despeje "
                                    "poco clara (cosecha aún no confirmada o parcial)."})

    if not f["ndwi_disponible"]:
        factores.append({"factor": "estres_hidrico_ndwi", "severidad": "n/d",
                         "detalle": "NDWI no disponible en la serie; factor no evaluado."})
    elif f["ndwi_pico"] < NDWI_ESTRES_SEVERO:
        score += 2
        factores.append({"factor": "estres_hidrico_ndwi", "severidad": "alta",
                         "detalle": f"NDWI en pico muy bajo ({f['ndwi_pico']}) < {NDWI_ESTRES_SEVERO}: "
                                    "bajo contenido de agua foliar → estrés hídrico marcado."})
    elif f["ndwi_pico"] < NDWI_ESTRES:
        score += 1
        factores.append({"factor": "estres_hidrico_ndwi", "severidad": "media",
                         "detalle": f"NDWI en pico bajo ({f['ndwi_pico']}) < {NDWI_ESTRES}: "
                                    "indicio de estrés hídrico en el período crítico."})
    else:
        factores.append({"factor": "estres_hidrico_ndwi", "severidad": "ok",
                         "detalle": f"NDWI en pico adecuado ({f['ndwi_pico']}): canopeo bien hidratado."})
    return factores, score


def evaluar_rinde(lote_id: int) -> dict:
    """Evalúa la penalización de rinde por reglas. Lanza ValueError si faltan datos."""
    ndvi_rows, rvi_rows, ndwi_rows = _series(lote_id)
    if not ndvi_rows:
        raise ValueError("Sin serie NDVI para evaluar el rinde.")

    f = _features(ndvi_rows, rvi_rows, ndwi_rows)
    factores, score = _reglas(f)

    if score >= 4:
        nivel = "alta"
    elif score >= 2:
        nivel = "media"
    else:
        nivel = "baja"

    return {
        "lote_id": lote_id,
        "modelo": "reglas-agronomicas-v1",
        "penalizacion": score >= 2,
        "nivel_penalizacion": nivel,
        "score": score,
        "features": f,
        "factores": factores,
    }


# =============================================================================
# Estimador de rinde potencial por RUE (modelo de Monteith)
# =============================================================================
# A diferencia de `evaluar_rinde` (motor de reglas que PENALIZA), este estimador
# devuelve un rinde POTENCIAL absoluto en kg/ha integrando la producción de
# biomasa día a día durante el ciclo:
#
#   Biomasa = Σ_días ( RUE · fAPAR · PAR )         [Monteith, 1977]
#   Rinde   = Biomasa · HI (índice de cosecha)
#
# donde:
#   PAR   = radiación fotosintéticamente activa ≈ 0.48 · radiación global (MJ/m²/día)
#   fAPAR = fracción de PAR absorbida por el canopeo, estimada del NDVI
#   RUE   = eficiencia en el uso de la radiación (g de biomasa seca / MJ de PAR)
#   HI    = índice de cosecha (fracción de la biomasa que va a grano/vaina)
#
# La serie NDVI (de la DB) da el fAPAR diario; la radiación se obtiene del
# reanálisis ERA5 (Open-Meteo Archive), la misma fuente que usa el peritaje.

# Reanálisis histórico para la radiación incidente durante el ciclo.
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
TZ_ARG = "America/Argentina/Cordoba"
_UA_ARCHIVE = {"User-Agent": "SIG-Agricola-Bonaerense/0.1 (+rinde-rue)"}

# Fracción PAR de la radiación global de onda corta (rango típico 0.45–0.50).
FRAC_PAR = 0.48

# fAPAR = a·NDVI + b, acotado a [0, FAPAR_MAX] (Daughtry et al. 1992 y afines).
FAPAR_A = 1.164
FAPAR_B = -0.143
FAPAR_MAX = 0.95

# Coeficientes por cultivo: RUE (g/MJ de PAR interceptada) e HI (índice de
# cosecha). Maní calibrado con INTA Manfredi / Bell & Wright; el resto son
# valores de literatura, ajustables. El nombre se normaliza sin acentos.
COEF_CULTIVO = {
    "mani":    {"nombre": "Maní",    "rue": 1.60, "hi": 0.38,
                "fuente": "INTA Manfredi; Bell & Wright (RUE 1.5–1.7; HI 0.35–0.40)"},
    "soja":    {"nombre": "Soja",    "rue": 1.00, "hi": 0.42,
                "fuente": "literatura (Sinclair; Andrade et al.)"},
    "maiz":    {"nombre": "Maíz",    "rue": 1.70, "hi": 0.50,
                "fuente": "literatura C4 (Andrade et al.)"},
    "trigo":   {"nombre": "Trigo",   "rue": 1.40, "hi": 0.40,
                "fuente": "literatura (Abbate et al.)"},
    "girasol": {"nombre": "Girasol", "rue": 1.20, "hi": 0.35,
                "fuente": "literatura (Trápani; Hall et al.)"},
}
COEF_DEFAULT = "mani"


def _normalizar_cultivo(cultivo: str | None) -> str:
    """Normaliza el nombre de cultivo a la clave de COEF_CULTIVO (sin acentos)."""
    if not cultivo:
        return COEF_DEFAULT
    c = cultivo.strip().lower()
    tabla = str.maketrans("áéíóúü", "aeiouu")
    c = c.translate(tabla)
    return c if c in COEF_CULTIVO else COEF_DEFAULT


def _fapar_desde_ndvi(ndvi: float) -> float:
    """Convierte NDVI en fAPAR lineal, acotado a [0, FAPAR_MAX]."""
    return max(0.0, min(FAPAR_MAX, FAPAR_A * ndvi + FAPAR_B))


def _radiacion_diaria(lat: float, lon: float, start: str, end: str,
                      *, intentos: int = 3, timeout: int = 20) -> dict[str, float]:
    """Radiación global diaria (MJ/m²/día) de ERA5 entre start y end (inclusive).

    Devuelve {fecha: MJ}. Lanza RuntimeError si la API no responde tras los
    reintentos (la maneja el endpoint como 502).
    """
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": start, "end_date": end,
        "daily": "shortwave_radiation_sum",   # MJ/m²/día
        "timezone": TZ_ARG,
    }
    ultimo = None
    for i in range(intentos):
        try:
            r = requests.get(OPEN_METEO_ARCHIVE, params=params,
                             headers=_UA_ARCHIVE, timeout=timeout)
            if r.status_code == 200:
                d = r.json().get("daily", {})
                fechas = d.get("time", []) or []
                rad = d.get("shortwave_radiation_sum", []) or []
                return {f: float(v) for f, v in zip(fechas, rad) if v is not None}
            ultimo = f"HTTP {r.status_code}: {r.text[:150]}"
        except requests.exceptions.RequestException as exc:
            ultimo = str(exc)
        time.sleep(1.5)
    raise RuntimeError(f"No se pudo obtener radiación de Open-Meteo Archive: {ultimo}")


def _fapar_diario(ndvi_rows) -> tuple[list[dt.date], np.ndarray]:
    """Serie fAPAR interpolada a resolución diaria desde las observaciones NDVI.

    Aplica el mismo despike que el motor de reglas, ordena por fecha e interpola
    linealmente el NDVI entre observaciones. Devuelve (fechas_diarias, fapar).
    """
    fechas = [dt.date.fromisoformat(r["fecha"]) for r in ndvi_rows]
    vals = despike_ndvi([float(r["valor"]) for r in ndvi_rows])
    # Ordenar por fecha por las dudas (la query ya ordena, pero el despike no).
    orden = np.argsort([f.toordinal() for f in fechas])
    fechas = [fechas[i] for i in orden]
    vals = [vals[i] for i in orden]

    d0, d1 = fechas[0], fechas[-1]
    dias = [d0 + dt.timedelta(days=k) for k in range((d1 - d0).days + 1)]
    x_obs = np.array([f.toordinal() for f in fechas], dtype=float)
    y_obs = np.array(vals, dtype=float)
    x_dia = np.array([d.toordinal() for d in dias], dtype=float)
    ndvi_diario = np.interp(x_dia, x_obs, y_obs)
    fapar = np.array([_fapar_desde_ndvi(v) for v in ndvi_diario])
    return dias, fapar


def estimar_rinde_rue(lote_id: int, cultivo: str | None = None) -> dict:
    """Estima el rinde potencial (kg/ha) por el modelo RUE-fAPAR-PAR.

    Integra la biomasa diaria durante el ciclo (span de la serie NDVI) usando
    la radiación de ERA5 y el fAPAR derivado del NDVI, y aplica el índice de
    cosecha del cultivo. Es un rinde POTENCIAL de materia seca según la luz
    interceptada; no descuenta pérdidas por eventualidades (para eso está el
    motor de reglas `evaluar_rinde` y el peritaje).

    Lanza ValueError si faltan datos NDVI; RuntimeError si falla la radiación.
    """
    ndvi_rows, _, _ = _series(lote_id)
    if not ndvi_rows or len(ndvi_rows) < 2:
        raise ValueError("Se necesitan al menos 2 observaciones NDVI para el modelo RUE.")

    # Cultivo: el argumento manda; si no, lo tomamos del lote; default maní.
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT cultivo, centroide_lat, centroide_lon FROM lotes WHERE id=?",
            (lote_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise ValueError(f"Lote {lote_id} inexistente.")
    lat, lon = row["centroide_lat"], row["centroide_lon"]
    if lat is None or lon is None:
        raise ValueError("El lote no tiene centroide para consultar radiación.")

    clave = _normalizar_cultivo(cultivo or row["cultivo"])
    coef = COEF_CULTIVO[clave]

    # fAPAR diario y radiación diaria en la MISMA ventana.
    dias, fapar = _fapar_diario(ndvi_rows)
    start, end = dias[0].isoformat(), dias[-1].isoformat()
    rad = _radiacion_diaria(lat, lon, start, end)

    # Integración de biomasa: Σ RUE · fAPAR · (0.48 · Rg).
    # g/m²  → kg/ha multiplicando por 10 (1 g/m² = 10 kg/ha).
    biomasa_g_m2 = 0.0
    par_acum = 0.0
    dias_con_rad = 0
    for d, fap in zip(dias, fapar):
        rg = rad.get(d.isoformat())
        if rg is None:
            continue
        par = FRAC_PAR * rg
        par_int = par * float(fap)
        biomasa_g_m2 += coef["rue"] * par_int
        par_acum += par
        dias_con_rad += 1

    biomasa_kg_ha = biomasa_g_m2 * 10.0
    rinde_kg_ha = biomasa_kg_ha * coef["hi"]

    fapar_medio = float(np.mean(fapar)) if len(fapar) else 0.0
    cobertura_rad = round(dias_con_rad / len(dias) * 100, 1) if dias else 0.0

    return {
        "lote_id": lote_id,
        "modelo": "RUE-fAPAR-PAR (Monteith)",
        "cultivo": coef["nombre"],
        "cultivo_clave": clave,
        "rinde_potencial_kg_ha": round(rinde_kg_ha, 1),
        "biomasa_aerea_kg_ha": round(biomasa_kg_ha, 1),
        "ventana": {"desde": start, "hasta": end, "dias": len(dias)},
        "coeficientes": {"rue_g_mj": coef["rue"], "hi": coef["hi"],
                         "frac_par": FRAC_PAR, "fuente": coef["fuente"]},
        "diagnostico": {
            "fapar_medio": round(fapar_medio, 3),
            "par_acumulada_mj_m2": round(par_acum, 1),
            "dias_con_radiacion": dias_con_rad,
            "cobertura_radiacion_pct": cobertura_rad,
        },
        "nota": ("Rinde potencial de materia seca según luz interceptada. "
                 "No descuenta pérdidas por eventualidades ni estrés; "
                 "combinar con /rinde (reglas) y el peritaje."),
    }


def margen_bruto_zonal(lote_id: int, rinde_objetivo: float, precio: float,
                       costo_base: float) -> dict:
    """Reparte el rinde y calcula el margen bruto por zona KMeans.

    rinde_objetivo: rinde medio esperado del lote (kg/ha).
    precio: precio de venta ($/kg).  costo_base: costo directo ($/ha).
    El rinde se escala por zona según el desvío de su NDVI respecto de la media.
    """
    conn = get_conn()
    try:
        z = conn.execute(
            "SELECT fecha_pico, ndvi_medio, zonas_json FROM zonificaciones "
            "WHERE lote_id=? ORDER BY created_at DESC LIMIT 1", (lote_id,)).fetchone()
    finally:
        conn.close()
    if not z:
        raise ValueError("No hay zonificación previa. Ejecutá /zonificacion primero.")

    zonas = json.loads(z["zonas_json"])
    ndvi_lote = z["ndvi_medio"] or 1.0
    detalle, tot_ing, tot_cost, tot_area = [], 0.0, 0.0, 0.0
    for zn in zonas:
        if zn.get("ndvi_medio") is None or zn.get("area_ha") is None:
            continue
        factor = zn["ndvi_medio"] / ndvi_lote if ndvi_lote else 1.0
        rinde_zona = rinde_objetivo * factor
        ingreso = rinde_zona * precio * zn["area_ha"]
        costo = costo_base * zn["area_ha"]
        margen = ingreso - costo
        tot_ing += ingreso; tot_cost += costo; tot_area += zn["area_ha"]
        detalle.append({
            "etiqueta": zn["etiqueta"], "area_ha": zn["area_ha"],
            "ndvi_medio": zn["ndvi_medio"], "factor_rinde": round(factor, 3),
            "rinde_kg_ha": round(rinde_zona, 1),
            "ingreso": round(ingreso, 2), "costo": round(costo, 2),
            "margen_bruto": round(margen, 2),
            "margen_ha": round(margen / zn["area_ha"], 2) if zn["area_ha"] else 0.0,
        })

    return {
        "lote_id": lote_id, "fecha_pico": z["fecha_pico"],
        "supuestos": {"rinde_objetivo_kg_ha": rinde_objetivo, "precio_kg": precio,
                      "costo_base_ha": costo_base, "ndvi_medio_lote": ndvi_lote},
        "zonas": detalle,
        "totales": {"area_ha": round(tot_area, 2), "ingreso": round(tot_ing, 2),
                    "costo": round(tot_cost, 2), "margen_bruto": round(tot_ing - tot_cost, 2),
                    "margen_ha": round((tot_ing - tot_cost) / tot_area, 2) if tot_area else 0.0},
    }
