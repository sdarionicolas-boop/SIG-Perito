"""Servicio de avance de cosecha escalonada.

Lee los reportes generados por el pipeline offline de radar (extractor/
procesar_lote.py -> data/reportes/{nombre}_progreso_cosecha.csv) en tiempo de
request, enlazándolos por el `nombre` del lote. No requiere sembrar la BD: si el
reporte no existe (p. ej. en Hugging Face), el módulo responde "no disponible"
con gracia.
"""
import csv

from app.config import REPORTES_DIR
from app.database import get_conn


def _lote_row(lote_id: int):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT id, nombre, cultivo, area_ha FROM lotes WHERE id = ?",
            (lote_id,),
        ).fetchone()
    finally:
        conn.close()


def obtener_progreso_cosecha(lote_id: int) -> dict:
    """Devuelve la serie de avance de cosecha acumulado del lote.

    Estructura:
      {lote_id, nombre, cultivo, area_ha, disponible,
       inicio, actual: {fecha, pct, ha}, serie: [{fecha, pct, ha}]}
    """
    row = _lote_row(lote_id)
    if row is None:
        raise ValueError(f"Lote {lote_id} no encontrado")

    base = {
        "lote_id": lote_id,
        "nombre": row["nombre"],
        "cultivo": row["cultivo"],
        "area_ha": row["area_ha"],
        "disponible": False,
        "inicio": None,
        "actual": None,
        "serie": [],
    }

    path = REPORTES_DIR / f"{row['nombre']}_progreso_cosecha.csv"
    if not path.exists():
        return base

    serie = []
    area_total = None
    with open(path, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                serie.append({
                    "fecha": r["fecha"],
                    "pct": round(float(r["pct_cosechado"]), 1),
                    "ha": round(float(r["hectareas_cosechadas"]), 2),
                })
                area_total = float(r["hectareas_totales"])
            except (KeyError, ValueError):
                continue

    if not serie:
        return base

    serie.sort(key=lambda p: p["fecha"])
    con_avance = [p for p in serie if p["pct"] > 0]

    base.update({
        "disponible": True,
        "area_ha": round(area_total, 2) if area_total is not None else row["area_ha"],
        "inicio": con_avance[0]["fecha"] if con_avance else None,
        "actual": serie[-1],
        "serie": serie,
    })
    return base
