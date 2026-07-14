# app/routers/uso_suelo.py
import sqlite3
from fastapi import APIRouter, HTTPException
from app.config import DB_PATH
from app.services.uso_suelo import fuente_lote, obtener_historial_cobertura

router = APIRouter(prefix="/api/uso-suelo", tags=["Uso de Suelo"])

@router.get("/{lote_id}")
async def get_uso_suelo_historial(lote_id: int):
    # Obtener el nombre del lote desde la BD
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT nombre FROM lotes WHERE id = ?", (lote_id,)).fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Lote no encontrado")
        
    nombre = row["nombre"]
    historial = obtener_historial_cobertura(nombre)

    return {
        "lote_id": lote_id,
        "nombre": nombre,
        "fuente": fuente_lote(nombre),
        "historial": historial,
    }
