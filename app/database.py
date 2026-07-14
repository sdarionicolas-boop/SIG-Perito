"""Capa de acceso a SQLite (stdlib, sin ORM) y definición del esquema."""
import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS lotes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre         TEXT NOT NULL,
    cultivo        TEXT,
    campo          TEXT,
    lote_num       TEXT,
    geom_geojson   TEXT NOT NULL,
    centroide_lat  REAL,
    centroide_lon  REAL,
    crs_original   TEXT DEFAULT 'EPSG:4326',
    area_ha        REAL,
    created_at     TEXT DEFAULT (datetime('now'))
);

-- Una fila por (lote, fecha, índice, órbita). Para índices ópticos la órbita
-- se guarda como '' (cadena vacía) para que el UNIQUE deduplique correctamente
-- (SQLite considera NULLs como distintos entre sí).
CREATE TABLE IF NOT EXISTS series_temporales (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id     INTEGER NOT NULL REFERENCES lotes(id) ON DELETE CASCADE,
    fecha       TEXT NOT NULL,
    indice      TEXT NOT NULL,            -- NDVI | RVI | NDWI ...
    valor       REAL,
    valor_min   REAL,
    valor_max   REAL,
    valor_std   REAL,
    sensor      TEXT,                     -- Sentinel-2 | Landsat | Sentinel-1 ...
    orbita      TEXT DEFAULT '',          -- ASCENDING | DESCENDING | '' (óptico)
    pct_valido  REAL,
    UNIQUE(lote_id, fecha, indice, orbita)
);
CREATE INDEX IF NOT EXISTS idx_series_lookup
    ON series_temporales(lote_id, indice, fecha);

CREATE TABLE IF NOT EXISTS zonificaciones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id     INTEGER NOT NULL REFERENCES lotes(id) ON DELETE CASCADE,
    fecha_pico  TEXT,
    k           INTEGER NOT NULL DEFAULT 3,
    ndvi_medio  REAL,
    zonas_json  TEXT NOT NULL,           -- [{zona,etiqueta,ndvi_medio,pixeles,area_ha,frac}]
    png_path    TEXT,                    -- ruta relativa servida bajo /media
    bounds_json TEXT,                    -- [[south,west],[north,east]] para Leaflet
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_zonif_lote ON zonificaciones(lote_id, created_at);

-- Caché diaria del clima histórico (Open-Meteo) por lote, para no repetir
-- llamadas cuando el usuario cambia selectores del módulo de sanidad.
CREATE TABLE IF NOT EXISTS clima_cache (
    lote_id  INTEGER NOT NULL REFERENCES lotes(id) ON DELETE CASCADE,
    fecha    TEXT NOT NULL,             -- día de la consulta (YYYY-MM-DD)
    datos    TEXT NOT NULL,             -- JSON con {tC, hVol, pMm, dias}
    PRIMARY KEY (lote_id, fecha)
);

-- Registro de consumo de las APIs de Sentinel Hub (Processing Units estimadas).
CREATE TABLE IF NOT EXISTS uso_api (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT DEFAULT (datetime('now')),
    tipo      TEXT NOT NULL,             -- statistical | process
    lote_id   INTEGER,
    pu_estim  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_uso_tipo ON uso_api(tipo, ts);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,         -- uuid hex
    lote_id     INTEGER,
    tipo        TEXT NOT NULL,            -- extraccion | zonificacion | forecast
    estado      TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING|PROCESSING|COMPLETED|FAILED
    progreso    INTEGER NOT NULL DEFAULT 0,       -- 0..100
    mensaje     TEXT,
    error_msg   TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- Metadatos de póliza/siniestro asociados a un caso de peritaje. Una fila por
-- (lote, fecha_evento, tipo_evento): re-peritar el mismo caso actualiza la
-- fila existente en vez de duplicarla (mismo criterio que usa la carpeta de
-- salidas en disco, ver services/peritaje.py::_slug).
CREATE TABLE IF NOT EXISTS siniestros (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id             INTEGER NOT NULL REFERENCES lotes(id) ON DELETE CASCADE,
    fecha_evento        TEXT NOT NULL,
    tipo_evento         TEXT NOT NULL,
    aseguradora         TEXT,
    numero_poliza       TEXT,
    productor           TEXT,
    comentarios_perito  TEXT,
    resultado_path      TEXT,             -- carpeta de salidas (reporte.html, etc.)
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now')),
    UNIQUE(lote_id, fecha_evento, tipo_evento)
);
CREATE INDEX IF NOT EXISTS idx_siniestros_poliza ON siniestros(numero_poliza);
CREATE INDEX IF NOT EXISTS idx_siniestros_aseguradora ON siniestros(aseguradora);
"""


def get_conn() -> sqlite3.Connection:
    """Devuelve una conexión SQLite con row_factory y FKs activadas."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_cursor():
    """Context manager transaccional: commit al salir, rollback ante error."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Crea las tablas si no existen."""
    with db_cursor() as conn:
        conn.executescript(SCHEMA)
