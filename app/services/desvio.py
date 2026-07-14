"""Alertas de desvío de NDVI vs. baseline regional (normativa tipo 5267 · Banco Central Brasil).

Ignacio (JusToken/COFCO) describió el requisito: "asociás regionalmente el índice verde
(NDVI/EVI) y si hay un desvío de 15–30% respecto al rendimiento esperado, es una alerta de
posible incumplimiento contractual".

Sin baseline histórico multianual (no usamos GEE), construimos el rendimiento ESPERADO de
cada fecha como la MEDIANA del cohorte de lotes del mismo cultivo observados en una ventana
temporal cercana. Comparar contra pares del mismo cultivo y misma fecha controla la fenología
(todos en un estadio similar), de modo que un lote que cae por debajo del cohorte es una
anomalía real (piedra, seca, mal manejo) y no simplemente una etapa distinta del ciclo.

Limitación conocida: dentro de un mismo cultivo conviven fechas de siembra distintas, lo que
mete algo de ruido en el "esperado". Es suficiente para una primera versión defendible; el
salto de calidad es un baseline por fecha-de-siembra o multianual real.
"""
import datetime as dt
import statistics
import sys

from app.config import BASE_DIR
from app.database import get_conn

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
from extractor.analizar_cosecha import despike_ndvi  # noqa: E402

VENTANA_DIAS = 8       # ± días para asociar observaciones del cohorte a cada fecha del lote
UMBRAL_PCT = 15.0      # % de caída respecto al esperado regional que dispara una alerta
MIN_COHORTE = 3        # mínimo de observaciones vecinas para calcular un esperado confiable
MIN_ESPERADO = 0.35    # NDVI mínimo del esperado para considerar "ventana productiva":
                       # a NDVI bajo (rastrojo/suelo desnudo) el % de desvío se dispara por
                       # el denominador chico y produce falsas alarmas. Solo alertamos con el
                       # cultivo vegetando activamente, que es lo que monitorea la 5267.


def _dias(a: str, b: str) -> int:
    return abs((dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days)


def evaluar_desvio(lote_id: int, umbral_pct: float = UMBRAL_PCT) -> dict:
    """Evalúa el desvío del NDVI del lote frente a la mediana regional de su cultivo."""
    conn = get_conn()
    try:
        lote = conn.execute(
            "SELECT id, nombre, cultivo FROM lotes WHERE id=?", (lote_id,)).fetchone()
        if lote is None:
            raise ValueError("Lote no encontrado")
        cultivo = lote["cultivo"]
        objetivo = conn.execute(
            "SELECT fecha, valor FROM series_temporales "
            "WHERE lote_id=? AND indice='NDVI' ORDER BY fecha", (lote_id,)).fetchall()
        cohorte = conn.execute(
            "SELECT s.fecha AS fecha, s.valor AS valor FROM series_temporales s "
            "JOIN lotes l ON l.id = s.lote_id "
            "WHERE s.indice='NDVI' AND l.cultivo=? AND s.lote_id<>?",
            (cultivo, lote_id)).fetchall()
        n_cohorte_lotes = conn.execute(
            "SELECT COUNT(DISTINCT s.lote_id) AS n FROM series_temporales s "
            "JOIN lotes l ON l.id = s.lote_id "
            "WHERE s.indice='NDVI' AND l.cultivo=? AND s.lote_id<>?",
            (cultivo, lote_id)).fetchone()["n"]
    finally:
        conn.close()

    base = {
        "lote_id": lote_id, "nombre": lote["nombre"], "cultivo": cultivo,
        "umbral_pct": umbral_pct, "disponible": False, "estado": "SIN_DATOS",
        "n_cohorte_lotes": n_cohorte_lotes, "n_alertas": 0, "desvio_peor": None,
        "actual": None, "serie": [],
    }
    if not objetivo or not cohorte:
        return base

    cohorte_pts = [(r["fecha"], float(r["valor"])) for r in cohorte]

    # Despike de la serie del lote: caídas transitorias que rebotan (nubes/sombras)
    # se reemplazan por el vecino más bajo. Las caídas reales (cosecha, piedra) no
    # rebotan y se conservan. Sin esto, un solo NDVI contaminado dispara una alerta
    # falsa de -80%+. El cohorte usa mediana (robusta), así que no necesita despike.
    fechas = [r["fecha"] for r in objetivo]
    crudos = [float(r["valor"]) for r in objetivo]
    suaves = despike_ndvi(crudos)

    serie = []
    for f, crudo, v in zip(fechas, crudos, suaves):
        vecinos = [val for (cf, val) in cohorte_pts if _dias(cf, f) <= VENTANA_DIAS]
        esperado = desvio = None
        alerta = False
        productiva = False
        if len(vecinos) >= MIN_COHORTE:
            esperado = statistics.median(vecinos)
            productiva = esperado >= MIN_ESPERADO
            if abs(esperado) > 1e-6:
                desvio = round((v - esperado) / abs(esperado) * 100, 1)
                alerta = productiva and desvio <= -umbral_pct
        serie.append({
            "fecha": f, "ndvi": round(v, 3), "ndvi_crudo": round(crudo, 3),
            "despicado": abs(crudo - v) > 1e-6,
            "esperado": round(esperado, 3) if esperado is not None else None,
            "desvio_pct": desvio, "alerta": alerta, "n_cohorte": len(vecinos),
            "productiva": productiva,
        })

    con_desvio = [p for p in serie if p["desvio_pct"] is not None]
    if not con_desvio:
        return base

    n_alertas = sum(1 for p in serie if p["alerta"])
    peor = min(p["desvio_pct"] for p in con_desvio)
    actual = serie[-1]
    estado = "ALERTA" if actual["alerta"] else "OK"

    base.update({
        "disponible": True,
        "estado": estado,
        "n_alertas": n_alertas,
        "desvio_peor": peor,
        "actual": actual,
        "serie": serie,
    })
    return base
