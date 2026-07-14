"""Gestión de tareas en segundo plano (persistidas en la tabla `jobs`).

Los jobs se guardan en SQLite (no en memoria) para que el frontend pueda
consultar el progreso vía GET /api/jobs/{id} aunque el worker se reinicie.
"""
import uuid
from typing import Optional

from app.database import db_cursor, get_conn


def create_job(tipo: str, lote_id: Optional[int] = None,
               mensaje: str = "En cola...") -> str:
    """Crea un job en estado PENDING y devuelve su id (uuid hex)."""
    job_id = uuid.uuid4().hex
    with db_cursor() as conn:
        conn.execute(
            "INSERT INTO jobs (id, lote_id, tipo, estado, progreso, mensaje) "
            "VALUES (?, ?, ?, 'PENDING', 0, ?)",
            (job_id, lote_id, tipo, mensaje),
        )
    return job_id


def update_job(job_id: str, *, estado: Optional[str] = None,
               progreso: Optional[int] = None, mensaje: Optional[str] = None,
               error_msg: Optional[str] = None) -> None:
    """Actualiza los campos provistos de un job y refresca updated_at."""
    sets, params = [], []
    if estado is not None:
        sets.append("estado = ?"); params.append(estado)
    if progreso is not None:
        sets.append("progreso = ?"); params.append(int(progreso))
    if mensaje is not None:
        sets.append("mensaje = ?"); params.append(mensaje)
    if error_msg is not None:
        sets.append("error_msg = ?"); params.append(error_msg)
    if not sets:
        return
    sets.append("updated_at = datetime('now')")
    params.append(job_id)
    with db_cursor() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)


def get_job(job_id: str) -> Optional[dict]:
    """Devuelve el job como dict, o None si no existe."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
