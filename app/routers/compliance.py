# app/routers/compliance.py
import sqlite3
from fastapi import APIRouter, HTTPException, Response
from app.config import DB_PATH
from app.services.compliance import evaluar_compliance, generar_pdf_certificado

router = APIRouter(prefix="/api/compliance", tags=["Compliance"])

@router.get("/{lote_id}")
async def get_compliance_status(lote_id: int):
    res = evaluar_compliance(lote_id)
    if "error" in res:
        raise HTTPException(status_code=404, detail=res["error"])
    return res

@router.get("/{lote_id}/pdf")
async def get_compliance_pdf(lote_id: int):
    # Obtener el nombre del lote para el encabezado de descarga
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT nombre FROM lotes WHERE id = ?", (lote_id,)).fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Lote no encontrado")
        
    nombre_lote = row["nombre"]
    
    try:
        pdf_bytes = generar_pdf_certificado(lote_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
        
    # Retornar como archivo binario descargable
    filename = f"{nombre_lote}_certificado_compliance.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )
