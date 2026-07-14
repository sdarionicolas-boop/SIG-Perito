"""Validación de consistencia temporal interna de las series (ruta sin baseline GEE).

Sin un baseline histórico de GEE, validamos la *coherencia interna* de cada serie:
calidad de muestreo (cadencia/gaps), rango físico, ruido residual (spikes de nube),
forma fenológica esperada (unimodal: sube al pico y cae) y coherencia óptico-radar.
Devuelve métricas + un score 0–100 y advertencias accionables.
"""
import datetime as dt
import sys

import numpy as np

from app.config import BASE_DIR
from app.database import get_conn

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
from extractor.analizar_cosecha import despike_ndvi, detectar_cosecha_sar  # noqa: E402

SPIKE_UMBRAL = 0.15        # caída/rebote de NDVI considerada spike (igual que el extractor)
CADENCIA_OBJETIVO = 10     # días: cadencia media deseable para óptico armonizado
GAP_ALERTA = 30            # días: hueco máximo tolerable sin observaciones


def _dias(a: str, b: str) -> int:
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def _media_movil(vals: list[float], w: int = 5) -> list[float]:
    """Media móvil centrada para atenuar el ruido día a día antes de medir forma."""
    n = len(vals)
    out = []
    h = w // 2
    for i in range(n):
        ventana = vals[max(0, i - h):min(n, i + h + 1)]
        out.append(sum(ventana) / len(ventana))
    return out


def _cambios_direccion(vals: list[float], min_delta: float = 0.04) -> int:
    """Cambios de dirección 'significativos' (pivotes zigzag) de la curva.

    Suaviza con media móvil y solo cuenta una reversión cuando el precio se aleja
    del último extremo en al menos min_delta de NDVI, ignorando el ruido. Una
    campaña limpia (sube al pico y baja) da ~1.
    """
    s = _media_movil(vals)
    trend, ext, cambios = 0, s[0], 0
    for v in s[1:]:
        if trend == 1:
            if v > ext:
                ext = v
            elif ext - v >= min_delta:
                cambios += 1; trend = -1; ext = v
        elif trend == -1:
            if v < ext:
                ext = v
            elif v - ext >= min_delta:
                cambios += 1; trend = 1; ext = v
        else:
            if v - ext >= min_delta:
                trend = 1; ext = v
            elif ext - v >= min_delta:
                trend = -1; ext = v
    return cambios


def validar_lote(lote_id: int) -> dict:
    """Calcula las métricas de consistencia interna para un lote."""
    conn = get_conn()
    try:
        nombre_row = conn.execute("SELECT nombre FROM lotes WHERE id=?", (lote_id,)).fetchone()
        ndvi = conn.execute(
            "SELECT fecha, valor, sensor FROM series_temporales "
            "WHERE lote_id=? AND indice='NDVI' ORDER BY fecha", (lote_id,)).fetchall()
        rvi = conn.execute(
            "SELECT fecha, valor, orbita FROM series_temporales "
            "WHERE lote_id=? AND indice='RVI' ORDER BY fecha", (lote_id,)).fetchall()
    finally:
        conn.close()
    if not nombre_row:
        raise ValueError("Lote no encontrado.")
    if not ndvi:
        raise ValueError("Sin serie NDVI para validar.")

    fechas = [r["fecha"] for r in ndvi]
    crudos = [float(r["valor"]) for r in ndvi]
    suaves = despike_ndvi(crudos)

    # 1. Muestreo
    n = len(fechas)
    gaps = [_dias(fechas[i], fechas[i + 1]) for i in range(n - 1)]
    cadencia = round(float(np.mean(gaps)), 1) if gaps else None
    gap_max = max(gaps) if gaps else 0

    # 2. Rango físico
    fuera_rango = sum(1 for v in crudos if v < -1.0 or v > 1.0)

    # 3. Ruido residual (spikes): puntos que el despike modificó
    spikes = [fechas[i] for i in range(n) if abs(crudos[i] - suaves[i]) > 1e-6]
    spike_pct = round(len(spikes) / n * 100, 1) if n else 0.0

    # 4. Forma fenológica
    idx_pico = int(np.argmax(suaves))
    cambios = _cambios_direccion(suaves)
    unimodal = cambios <= 3  # campaña limpia: sube al pico y baja (1–3 pivotes)

    # 5. Sensores
    sensores = {}
    for r in ndvi:
        sensores[r["sensor"]] = sensores.get(r["sensor"], 0) + 1

    # 6. Coherencia radar (cosecha física)
    cosecha_sar = None
    if rvi:
        import pandas as pd
        df = pd.DataFrame([{"lote_id": 0, "fecha": r["fecha"],
                            "rvi_medio": float(r["valor"]), "orbita": r["orbita"]} for r in rvi])
        df["fecha"] = pd.to_datetime(df["fecha"])
        res = detectar_cosecha_sar(df)
        if res and res["fecha_cosecha"] is not None:
            cosecha_sar = res["fecha_cosecha"].strftime("%Y-%m-%d")

    # --- Score + advertencias ---
    score, avisos = 100, []
    if fuera_rango:
        score -= 40; avisos.append(f"{fuera_rango} valores de NDVI fuera de rango físico [-1,1].")
    if cadencia and cadencia > CADENCIA_OBJETIVO:
        score -= 10; avisos.append(f"Cadencia media alta ({cadencia} d > {CADENCIA_OBJETIVO} d): muestreo ralo.")
    if gap_max > GAP_ALERTA:
        score -= 15; avisos.append(f"Hueco temporal de {gap_max} días sin observaciones.")
    if spike_pct > 15:
        score -= 15; avisos.append(f"Ruido alto: {spike_pct}% de observaciones son spikes (nubes residuales).")
    elif spike_pct > 5:
        score -= 5; avisos.append(f"Ruido moderado: {spike_pct}% de spikes despicados.")
    if not unimodal:
        score -= 10; avisos.append(f"Curva no unimodal ({cambios} cambios de dirección): revisar fenología/ruido.")
    if not rvi:
        avisos.append("Sin serie SAR (RVI): no se pudo corroborar la cosecha física.")
    score = max(0, score)
    nivel = "alta" if score >= 80 else "media" if score >= 60 else "baja"

    return {
        "lote_id": lote_id, "nombre": nombre_row["nombre"],
        "muestreo": {"observaciones": n, "desde": fechas[0], "hasta": fechas[-1],
                     "cadencia_media_dias": cadencia, "gap_max_dias": gap_max},
        "rango": {"ndvi_fuera_de_rango": fuera_rango,
                  "ndvi_min": round(min(crudos), 4), "ndvi_max": round(max(crudos), 4)},
        "ruido": {"spikes": len(spikes), "spike_pct": spike_pct, "fechas_spike": spikes[:10]},
        "fenologia": {"ndvi_pico": round(suaves[idx_pico], 4), "fecha_pico": fechas[idx_pico],
                      "cambios_direccion": cambios, "unimodal": unimodal},
        "sensores": sensores,
        "coherencia_radar": {"obs_rvi": len(rvi), "fecha_cosecha_fisica": cosecha_sar},
        "consistencia": {"score": score, "nivel": nivel, "avisos": avisos},
    }


def validar_todos() -> dict:
    """Resumen de consistencia para todos los lotes con serie NDVI."""
    from app.services.seed import COHORTE_SOMBRA_PREFIJO
    conn = get_conn()
    try:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM lotes WHERE nombre NOT LIKE ? ORDER BY id",
            (f"{COHORTE_SOMBRA_PREFIJO}%",)).fetchall()]
    finally:
        conn.close()
    items, scores = [], []
    for lid in ids:
        try:
            v = validar_lote(lid)
        except ValueError:
            continue
        scores.append(v["consistencia"]["score"])
        items.append({"lote_id": lid, "nombre": v["nombre"],
                      "score": v["consistencia"]["score"], "nivel": v["consistencia"]["nivel"],
                      "observaciones": v["muestreo"]["observaciones"],
                      "cadencia_media_dias": v["muestreo"]["cadencia_media_dias"],
                      "spike_pct": v["ruido"]["spike_pct"]})
    return {"n_lotes": len(items),
            "score_promedio": round(float(np.mean(scores)), 1) if scores else None,
            "lotes": items}
