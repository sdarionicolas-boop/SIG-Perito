"""Servicio de peritaje de eventualidades — reemplazo web-friendly del pipeline.

Envuelve `extractor.peritaje_eventos.peritar_evento` para ejecutarse contra un
lote persistido (usando su geom_geojson de la DB) y persistir la salida en el
sistema de jobs del proyecto (mismo patrón que `services/extraction.py`).

Diseño:
  * Todo el cómputo pesado (bajar rásters de CDSE, mediana en JS, delta y
    clasificación local) vive en `extractor/peritaje_eventos.py`. Este módulo
    sólo actúa como bridge: resuelve credenciales/geom del lote, invoca el
    peritaje y actualiza el estado del job.
  * Las salidas se escriben bajo REPORTES_DIR/peritaje/<lote_id>/<caso_slug>/
    para poder linkearlas después desde el frontend.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from app.config import BASE_DIR, REPORTES_DIR
from app.database import db_cursor, get_conn
from app.jobs import update_job

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from extractor.peritaje_eventos import (  # noqa: E402
    UMBRALES_POR_EVENTO,
    peritar_evento,
)


def _slug(txt: str) -> str:
    """Slug filesystem-safe, sin acentos raros ni espacios."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", txt.strip())
    return s.strip("_") or "caso"


def _cargar_geom_lote(lote_id: int) -> dict | None:
    """Recupera geom + nombre + cultivo del lote desde SQLite."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT geom_geojson, nombre, cultivo FROM lotes WHERE id = ?", (lote_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {"geom": json.loads(row["geom_geojson"]), "nombre": row["nombre"],
            "cultivo": row["cultivo"]}


def _urlificar_outputs(outputs: dict[str, str]) -> dict[str, str]:
    """Convierte rutas de archivo de salida en URLs servidas bajo /reportes.

    REPORTES_DIR se monta como estático en /reportes (ver main.py), así que la
    URL es /reportes/<ruta relativa a REPORTES_DIR> con separadores POSIX.
    """
    base = Path(REPORTES_DIR).resolve()
    urls: dict[str, str] = {}
    for clave, ruta in outputs.items():
        try:
            rel = Path(ruta).resolve().relative_to(base)
            urls[clave] = "/reportes/" + rel.as_posix()
        except (ValueError, OSError):
            urls[clave] = ruta  # fuera de REPORTES_DIR: dejar tal cual
    return urls


def ultimo_peritaje(lote_id: int) -> dict | None:
    """Devuelve el resultado del peritaje más reciente del lote, o None.

    Busca el `resultado.json` más nuevo bajo REPORTES_DIR/peritaje/<lote_id>/.
    """
    base = Path(REPORTES_DIR) / "peritaje" / str(lote_id)
    if not base.exists():
        return None
    resultados = sorted(base.glob("*/resultado.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not resultados:
        return None
    try:
        return json.loads(resultados[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def guardar_siniestro(
    lote_id: int, fecha_evento: str, tipo_evento: str, *,
    aseguradora: str | None = None, numero_poliza: str | None = None,
    productor: str | None = None, comentarios_perito: str | None = None,
    resultado_path: str | None = None,
) -> int:
    """Crea o actualiza (upsert) el registro de siniestro para (lote, fecha, tipo).

    Re-peritar el mismo caso actualiza la fila existente en vez de duplicarla
    (mismo criterio que la carpeta de salidas en disco, ver `_slug` arriba).
    """
    with db_cursor() as conn:
        cur = conn.execute(
            "INSERT INTO siniestros "
            "(lote_id, fecha_evento, tipo_evento, aseguradora, numero_poliza, "
            " productor, comentarios_perito, resultado_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(lote_id, fecha_evento, tipo_evento) DO UPDATE SET "
            "  aseguradora=excluded.aseguradora, numero_poliza=excluded.numero_poliza, "
            "  productor=excluded.productor, comentarios_perito=excluded.comentarios_perito, "
            "  resultado_path=excluded.resultado_path, updated_at=datetime('now')",
            (lote_id, fecha_evento, tipo_evento, aseguradora, numero_poliza,
             productor, comentarios_perito, resultado_path),
        )
        if cur.lastrowid:
            return cur.lastrowid
    # Upsert por ON CONFLICT no siempre repuebla lastrowid en la rama UPDATE;
    # si no vino, resolvemos el id con un SELECT (misma clave única).
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM siniestros WHERE lote_id=? AND fecha_evento=? AND tipo_evento=?",
            (lote_id, fecha_evento, tipo_evento)).fetchone()
        return row["id"] if row else 0
    finally:
        conn.close()


def listar_siniestros(
    *, aseguradora: str | None = None, numero_poliza: str | None = None,
    lote_id: int | None = None,
) -> list[dict]:
    """Lista/busca siniestros registrados, con el nombre y cultivo del lote."""
    conn = get_conn()
    try:
        q = ("SELECT s.*, l.nombre AS lote_nombre, l.cultivo AS lote_cultivo "
             "FROM siniestros s JOIN lotes l ON l.id = s.lote_id WHERE 1=1")
        params: list = []
        if aseguradora:
            q += " AND s.aseguradora LIKE ?"
            params.append(f"%{aseguradora}%")
        if numero_poliza:
            q += " AND s.numero_poliza LIKE ?"
            params.append(f"%{numero_poliza}%")
        if lote_id is not None:
            q += " AND s.lote_id = ?"
            params.append(lote_id)
        q += " ORDER BY s.updated_at DESC"
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def run_peritaje_job(
    job_id: str,
    lote_id: int,
    fecha_evento: str,
    tipo_evento: str = "granizo",
    ventana_dias: int = 14,
    baseline_anos: int = 3,
    aseguradora: str | None = None,
    numero_poliza: str | None = None,
    productor: str | None = None,
    comentarios_perito: str | None = None,
) -> None:
    """Tarea de fondo: corre el peritaje completo para un lote y persiste salidas.

    Actualiza el `job_id` con el progreso a medida que avanza. Cualquier excepción
    queda capturada y el job se marca como FAILED (patrón espejo del `extraction`).
    """
    try:
        if tipo_evento not in UMBRALES_POR_EVENTO:
            update_job(job_id, estado="FAILED",
                       error_msg=f"tipo_evento inválido: {tipo_evento}")
            return

        update_job(job_id, estado="PROCESSING", progreso=5,
                   mensaje="Cargando geometría del lote...")
        lote = _cargar_geom_lote(lote_id)
        if lote is None:
            update_job(job_id, estado="FAILED",
                       error_msg=f"Lote {lote_id} inexistente.")
            return

        caso_slug = _slug(f"{lote['nombre']}_{fecha_evento}_{tipo_evento}")
        out_dir = Path(REPORTES_DIR) / "peritaje" / str(lote_id) / caso_slug
        out_dir.mkdir(parents=True, exist_ok=True)

        update_job(job_id, progreso=15,
                   mensaje=f"Autenticando CDSE y bajando NDVI PRE/POST ({fecha_evento})...")

        metadata = {"aseguradora": aseguradora, "numero_poliza": numero_poliza,
                   "productor": productor, "comentarios_perito": comentarios_perito}

        resultado = peritar_evento(
            geom=lote["geom"],
            fecha_evento=fecha_evento,
            tipo_evento=tipo_evento,
            nombre_caso=lote["nombre"],
            cultivo=lote.get("cultivo"),
            metadata=metadata,
            ventana_dias=ventana_dias,
            baseline_anos=baseline_anos,
            output_dir=out_dir,
        )

        # Exponer las salidas como URLs (/reportes/...) y persistir el resultado
        # completo para que el GET /peritaje lo devuelva sin recomputar.
        resultado["lote_id"] = lote_id
        resultado["outputs"] = _urlificar_outputs(resultado.get("outputs", {}))
        (out_dir / "resultado.json").write_text(
            json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")

        # Registro de póliza/siniestro para listado y búsqueda (GET /api/siniestros).
        guardar_siniestro(
            lote_id, fecha_evento, tipo_evento,
            aseguradora=aseguradora, numero_poliza=numero_poliza,
            productor=productor, comentarios_perito=comentarios_perito,
            resultado_path=str(out_dir),
        )

        update_job(
            job_id, estado="COMPLETED", progreso=100,
            mensaje=(f"Peritaje listo: {resultado['areas_ha']['afectada']:.1f} ha "
                     f"afectadas ({resultado['pct']['afectada']:.1f}% del lote). "
                     f"Confianza: {resultado['confianza']}."),
        )
    except Exception as exc:  # noqa: BLE001
        update_job(job_id, estado="FAILED", error_msg=str(exc)[:500])
