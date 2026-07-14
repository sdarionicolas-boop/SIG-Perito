"""Contabilidad local de consumo de las APIs de Sentinel Hub (Processing Units).

CDSE (capa gratuita) no expone un endpoint simple de saldo de PUs, así que llevamos
un registro local: cada llamada externa estima su costo en PU y se acumula. Los
valores son APROXIMADOS y configurables; sirven para vigilar el presupuesto y
detectar consumo anómalo, no como facturación exacta.
"""
import os

from app.database import db_cursor, get_conn

# Estimación aproximada de PU por llamada (calibrable por entorno).
PU_POR_LLAMADA = {
    "statistical": float(os.environ.get("PU_STATISTICAL", "2.0")),
    "process": float(os.environ.get("PU_PROCESS", "3.0")),
}
PRESUPUESTO_PU = float(os.environ.get("PRESUPUESTO_PU", "30000"))  # tope mensual típico free


def registrar_uso(tipo: str, lote_id: int | None = None, pu: float | None = None) -> None:
    """Registra una llamada externa. Silencioso ante errores (no debe romper la extracción)."""
    try:
        costo = pu if pu is not None else PU_POR_LLAMADA.get(tipo, 1.0)
        with db_cursor() as conn:
            conn.execute("INSERT INTO uso_api (tipo, lote_id, pu_estim) VALUES (?, ?, ?)",
                         (tipo, lote_id, costo))
    except Exception:
        pass


def resumen_uso() -> dict:
    """Totales de consumo estimado y % de presupuesto utilizado."""
    conn = get_conn()
    try:
        filas = conn.execute(
            "SELECT tipo, COUNT(*) AS llamadas, COALESCE(SUM(pu_estim),0) AS pu "
            "FROM uso_api GROUP BY tipo").fetchall()
        total = conn.execute(
            "SELECT COUNT(*) AS llamadas, COALESCE(SUM(pu_estim),0) AS pu FROM uso_api").fetchone()
    finally:
        conn.close()
    return {
        "por_tipo": {f["tipo"]: {"llamadas": f["llamadas"], "pu_estim": round(f["pu"], 1)} for f in filas},
        "total_llamadas": total["llamadas"],
        "pu_estimadas": round(total["pu"], 1),
        "presupuesto_pu": PRESUPUESTO_PU,
        "pct_presupuesto": round(total["pu"] / PRESUPUESTO_PU * 100, 2) if PRESUPUESTO_PU else None,
        "nota": "Valores aproximados (PU_STATISTICAL/PU_PROCESS configurables).",
    }
