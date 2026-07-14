"""Siembra inicial de la BD desde el shapefile y los CSV ya extraídos.

Se ejecuta una sola vez (idempotente): si ya hay lotes cargados, no hace nada.
Replica el esquema de `lote_id` del extractor original para que las series de
los CSV enlacen correctamente con cada lote.
"""
import datetime as dt
import json
import math
import random

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon, shape

import os

from app.config import (CSV_NDVI, CSV_NDWI, CSV_SAR, LOTES_GEOJSON, SHAPEFILE_PATH)
from app.database import db_cursor, get_conn
from app.geoutils import calculate_area_hectares
# Lotes de demostración pública creados por el usuario en QGIS (Buenos Aires)
DEMO_LOTES = [
    {
        "nombre": "Lote Demo 1",
        "cultivo": "soja_1",
        "campo": "Demo: QGIS Bonaerense",
        "lote_num": "1",
        "geom_coords": [
            [-60.53595381374954, -33.614699294347304],
            [-60.53017381265629, -33.60900686902819],
            [-60.52404350846647, -33.61364838505762],
            [-60.529823509559726, -33.619340810376734],
            [-60.53595381374954, -33.614699294347304]
        ]
    },
    {
        "nombre": "Lote Demo 2",
        "cultivo": "maiz",
        "campo": "Demo: QGIS Bonaerense",
        "lote_num": "2",
        "geom_coords": [
            [-60.538055632328906, -33.61323240013046],
            [-60.537792905006484, -33.61220338478431],
            [-60.53258214644514, -33.607189671714785],
            [-60.53034896420456, -33.6090725508588],
            [-60.536172753184886, -33.614611718573165],
            [-60.538055632328906, -33.61323240013046]
        ]
    },
    {
        "nombre": "Lote Demo 3",
        "cultivo": "trigo",
        "campo": "Demo: QGIS Bonaerense",
        "lote_num": "3",
        "geom_coords": [
            [-60.53816510204658, -33.61303535463865],
            [-60.54271904230187, -33.60970747522131],
            [-60.5368076775474, -33.604036943845735],
            [-60.53262593433221, -33.60716777777125],
            [-60.53788048078062, -33.61211580901017],
            [-60.53816510204658, -33.61303535463865]
        ]
    }
]


# Prefijo de los lotes "sombra": no son campos reales ni demos navegables, solo
# aportan cohorte NDVI para el módulo de desvío (normativa 5267). Demo 2 (maíz) y
# Demo 3 (trigo) tienen cultivos únicos en todo el dataset -> sin esto nunca
# encuentran con qué compararse, ni en local ni en HF (que no tiene los 123 lotes
# reales de Córdoba que le dan cohorte a Demo 1/soja en local). GET /api/lotes y
# /api/lotes/mapa los excluyen explícitamente por este prefijo.
COHORTE_SOMBRA_PREFIJO = "Cohorte NDVI "

# Ventana que coincide con el NDVI real ya extraído (Sentinel-2) de Demo 1/2, para
# que el cohorte del desvío (ventana ±8 días) encuentre coincidencias de fecha.
_TEMPORADA_DESDE = dt.date(2025, 10, 2)
_TEMPORADA_HASTA = dt.date(2026, 5, 22)

# Forma de curva por cultivo (NDVI inicio/pico/fin). Ilustrativa: prioriza que el
# cohorte tenga con qué comparar en la demo pública, no una reconstrucción agronómica
# exacta del calendario real de siembra de cada cultivo.
_CURVA_CULTIVO = {
    "soja_1": {"inicio": 0.20, "pico": 0.82, "fin": 0.18},
    "maiz": {"inicio": 0.22, "pico": 0.88, "fin": 0.20},
    "trigo": {"inicio": 0.24, "pico": 0.78, "fin": 0.16},
}

# Centros (lon, lat) de los 6 lotes sombra, cerca del cluster de demos en Buenos
# Aires. La ubicación exacta no importa: nunca se muestran en el mapa ni en la lista.
_SOMBRA_CENTROS = {
    ("soja_1", "A"): (-60.545, -33.600), ("soja_1", "B"): (-60.548, -33.598),
    ("maiz", "A"): (-60.545, -33.625), ("maiz", "B"): (-60.548, -33.627),
    ("trigo", "A"): (-60.520, -33.625), ("trigo", "B"): (-60.518, -33.628),
}


def _curva_ndvi_sintetica(cultivo: str, seed: int) -> list[tuple[str, float]]:
    """NDVI estacional sintético (campana siembra->pico->cosecha), muestreado cada
    ~6 días (cadencia S2 típica). Usado para los lotes sombra y para la serie
    propia de Lote Demo 3, que no tiene extracción satelital real como Demo 1/2."""
    cfg = _CURVA_CULTIVO[cultivo]
    rnd = random.Random(seed)
    total_dias = (_TEMPORADA_HASTA - _TEMPORADA_DESDE).days
    desfase = rnd.uniform(-0.03, 0.03)
    puntos, d = [], 0
    while d <= total_dias:
        frac = d / total_dias
        campana = math.sin(frac * math.pi) ** 1.3
        valor = cfg["inicio"] + (cfg["pico"] - cfg["inicio"]) * campana
        if frac > 0.6:
            valor -= (frac - 0.6) * (cfg["pico"] - cfg["fin"]) * 1.4
        valor = max(0.05, min(0.95, valor + desfase + rnd.uniform(-0.02, 0.02)))
        fecha = _TEMPORADA_DESDE + dt.timedelta(days=d)
        puntos.append((fecha.isoformat(), round(valor, 3)))
        d += rnd.choice([5, 6, 7, 8])
    return puntos


def _insertar_ndvi(conn, lote_id: int, puntos: list[tuple[str, float]]) -> None:
    for fecha, valor in puntos:
        conn.execute(
            "INSERT OR IGNORE INTO series_temporales "
            "(lote_id, fecha, indice, valor, valor_min, valor_max, "
            " valor_std, sensor, orbita, pct_valido) "
            "VALUES (?, ?, 'NDVI', ?, ?, ?, 0, 'Sentinel-2 (sintético)', '', 100)",
            (lote_id, fecha, valor, valor, valor),
        )


def _cuadrado(cx: float, cy: float, lado: float = 0.001) -> list[list[float]]:
    return [[cx - lado, cy - lado], [cx + lado, cy - lado],
            [cx + lado, cy + lado], [cx - lado, cy + lado],
            [cx - lado, cy - lado]]


def _seed_cohorte_sombra(conn) -> None:
    """Crea 2 lotes sombra por cultivo (soja_1/maiz/trigo) con NDVI sintético."""
    for (cultivo, letra), (cx, cy) in _SOMBRA_CENTROS.items():
        geom = Polygon(_cuadrado(cx, cy))
        lote_id = _insert_lote(
            conn, f"{COHORTE_SOMBRA_PREFIJO}{cultivo} {letra}", cultivo,
            "Cohorte de referencia (no navegable)", None, geom,
        )
        puntos = _curva_ndvi_sintetica(cultivo, seed=hash((cultivo, letra)))
        _insertar_ndvi(conn, lote_id, puntos)


def _insertar_indices_completos(conn, lote_id: int, cultivo: str, seed: int) -> None:
    """Genera curvas completas y consistentes de NDVI, NDWI y RVI para los demos."""
    puntos_ndvi = _curva_ndvi_sintetica(cultivo, seed)
    _insertar_ndvi(conn, lote_id, puntos_ndvi)
    
    rnd = random.Random(seed)
    for fecha, val_ndvi in puntos_ndvi:
        # NDWI: oscila de -0.15 (seco/suelo) a 0.45 (follaje húmedo) según vigor
        val_ndwi = round(val_ndvi * 0.6 - 0.15 + rnd.uniform(-0.02, 0.02), 3)
        conn.execute(
            "INSERT OR IGNORE INTO series_temporales "
            "(lote_id, fecha, indice, valor, valor_min, valor_max, "
            " valor_std, sensor, orbita, pct_valido) "
            "VALUES (?, ?, 'NDWI', ?, ?, ?, 0, 'Sentinel-2 (sintético)', '', 100)",
            (lote_id, fecha, val_ndwi, val_ndwi, val_ndwi),
        )
        
        # RVI (radar Sentinel-1): oscila de 1.0 (suelo desnudo) a ~2.6 (vigor de biomasa)
        val_rvi = round(1.0 + (val_ndvi - 0.15) * 1.8 + rnd.uniform(-0.04, 0.04), 3)
        val_rvi = max(1.0, val_rvi)
        conn.execute(
            "INSERT OR IGNORE INTO series_temporales "
            "(lote_id, fecha, indice, valor, valor_min, valor_max, "
            " valor_std, sensor, orbita, pct_valido) "
            "VALUES (?, ?, 'RVI', ?, ?, ?, 0, 'Sentinel-1 (sintético)', '', 100)",
            (lote_id, fecha, val_rvi, val_rvi, val_rvi),
        )


def _seed_demo_lotes() -> dict[str, int]:
    """Siembra los lotes experimentales del INTA en la base de datos."""
    nombre_a_id = {}
    with db_cursor() as conn:
        for dl in DEMO_LOTES:
            geom = Polygon(dl["geom_coords"])
            lote_id = _insert_lote(
                conn, dl["nombre"], dl["cultivo"], dl["campo"], dl["lote_num"], geom
            )
            nombre_a_id[dl["nombre"]] = lote_id
            
            # Generar e insertar series completas (NDVI, NDWI, RVI) para todos los lotes demo
            _insertar_indices_completos(conn, lote_id, dl["cultivo"], seed=hash(dl["nombre"]))
            
        _seed_cohorte_sombra(conn)
    return nombre_a_id



def _insert_lote(conn, nombre, cultivo, campo, lote_num, geom) -> int:
    """Inserta un lote calculando centroide y área. Devuelve su id."""
    centroide = geom.centroid
    lon, lat = float(centroide.x), float(centroide.y)
    try:
        area_ha = round(calculate_area_hectares(geom, lon, lat), 2)
    except Exception:
        area_ha = None
    cur = conn.execute(
        "INSERT INTO lotes "
        "(nombre, cultivo, campo, lote_num, geom_geojson, "
        " centroide_lat, centroide_lon, crs_original, area_ha) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'EPSG:4326', ?)",
        (nombre, cultivo or None, campo or None, lote_num or None,
         json.dumps(geom.__geo_interface__), lat, lon, area_ha),
    )
    return cur.lastrowid


def _seed_lotes_geojson() -> dict[str, int]:
    """Siembra todos los lotes desde data/lotes.geojson (snapshot completo)."""
    with open(LOTES_GEOJSON, "r", encoding="utf-8") as f:
        fc = json.load(f)
    nombre_a_id: dict[str, int] = {}
    with db_cursor() as conn:
        for feat in fc.get("features", []):
            p = feat.get("properties", {})
            nombre = p.get("nombre")
            if not nombre:
                continue
            geom = shape(feat["geometry"])
            nombre_a_id[nombre] = _insert_lote(
                conn, nombre, p.get("cultivo"), p.get("campo"), p.get("lote_num"), geom)
    return nombre_a_id


def _build_lote_id(idx: int, row) -> str:
    """Reproduce el identificador usado por extractor_temporal.run_extraction."""
    camp = str(row.get("Nomb_campo", "Unknown")).strip().replace(" ", "_")
    lote_id = f"Lote_{idx:02d}_{camp}"
    lote_num = str(row.get("Lote", "None")).strip()
    if lote_num and lote_num not in ("None", "nan"):
        lote_id += f"_L{lote_num}"
    return lote_id


def _seed_lotes() -> dict[str, int]:
    """Inserta los lotes desde el shapefile original. Mapping nombre -> id."""
    gdf = gpd.read_file(SHAPEFILE_PATH)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    nombre_a_id: dict[str, int] = {}
    with db_cursor() as conn:
        for idx, row in gdf.iterrows():
            nombre = _build_lote_id(idx, row)
            nombre_a_id[nombre] = _insert_lote(
                conn, nombre, str(row.get("cultivo", "")),
                str(row.get("Nomb_campo", "")), str(row.get("Lote", "")),
                row["geometry"])
    return nombre_a_id


def _seed_series(nombre_a_id: dict[str, int]) -> tuple[int, int, int]:
    """Carga NDVI, RVI y NDWI desde los CSV. Devuelve (n_ndvi, n_rvi, n_ndwi)."""
    n_ndvi = n_rvi = n_ndwi = 0
    with db_cursor() as conn:
        if CSV_NDVI.exists():
            for _, r in pd.read_csv(CSV_NDVI).iterrows():
                lote_id = nombre_a_id.get(r["lote_id"])
                if not lote_id:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO series_temporales "
                    "(lote_id, fecha, indice, valor, valor_min, valor_max, "
                    " valor_std, sensor, orbita, pct_valido) "
                    "VALUES (?, ?, 'NDVI', ?, ?, ?, ?, ?, '', ?)",
                    (lote_id, r["fecha"], r["ndvi_medio"], r["ndvi_min"],
                     r["ndvi_max"], r["ndvi_std"], r["sensor"], r["pct_valido"]),
                )
                n_ndvi += 1
        if CSV_SAR.exists():
            for _, r in pd.read_csv(CSV_SAR).iterrows():
                lote_id = nombre_a_id.get(r["lote_id"])
                if not lote_id:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO series_temporales "
                    "(lote_id, fecha, indice, valor, valor_min, valor_max, "
                    " valor_std, sensor, orbita, pct_valido) "
                    "VALUES (?, ?, 'RVI', ?, ?, ?, ?, ?, ?, ?)",
                    (lote_id, r["fecha"], r["rvi_medio"], r["rvi_min"],
                     r["rvi_max"], r["rvi_std"], r["sensor"], r["orbita"],
                     r["pct_valido"]),
                )
                n_rvi += 1
        if CSV_NDWI.exists():
            for _, r in pd.read_csv(CSV_NDWI).iterrows():
                lote_id = nombre_a_id.get(r["lote_id"])
                if not lote_id:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO series_temporales "
                    "(lote_id, fecha, indice, valor, valor_min, valor_max, "
                    " valor_std, sensor, orbita, pct_valido) "
                    "VALUES (?, ?, 'NDWI', ?, ?, ?, ?, ?, '', ?)",
                    (lote_id, r["fecha"], r["ndwi_medio"], r["ndwi_min"],
                     r["ndwi_max"], r["ndwi_std"], r["sensor"], r["pct_valido"]),
                )
                n_ndwi += 1
    return n_ndvi, n_rvi, n_ndwi


def seed_if_empty() -> dict:
    """Siembra la BD solo si la tabla `lotes` está vacía o si faltan las series. Idempotente."""
    conn = get_conn()
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM lotes").fetchone()["c"]
        series_count = conn.execute("SELECT COUNT(*) AS c FROM series_temporales").fetchone()["c"]
    finally:
        conn.close()

    # Si hay lotes pero no hay series (caso del deploy viejo roto), limpiamos y re-sembramos
    if count > 0 and series_count == 0:
        with db_cursor() as conn_clear:
            conn_clear.execute("DELETE FROM series_temporales")
            conn_clear.execute("DELETE FROM lotes")
            conn_clear.execute("DELETE FROM zonificaciones")
        count = 0

    if count > 0 and series_count > 0:
        return {"seeded": False, "lotes": count}


    # Detectar si corre en Hugging Face o si no tiene las fuentes de datos locales
    is_hf = os.environ.get("SPACE_ID") is not None
    if is_hf or (not LOTES_GEOJSON.exists() and not SHAPEFILE_PATH.exists()):
        nombre_a_id = _seed_demo_lotes()
        conn_check = get_conn()
        try:
            n_ndvi = conn_check.execute("SELECT COUNT(*) AS c FROM series_temporales WHERE indice='NDVI'").fetchone()["c"]
            n_ndwi = conn_check.execute("SELECT COUNT(*) AS c FROM series_temporales WHERE indice='NDWI'").fetchone()["c"]
            n_rvi = conn_check.execute("SELECT COUNT(*) AS c FROM series_temporales WHERE indice='RVI'").fetchone()["c"]
        finally:
            conn_check.close()
        return {
            "seeded": True,
            "lotes": len(nombre_a_id),
            "ndvi": n_ndvi,
            "rvi": n_rvi,
            "ndwi": n_ndwi,
            "info": "Demostración pública (INTA)"
        }


    if LOTES_GEOJSON.exists():
        nombre_a_id = _seed_lotes_geojson()
    elif SHAPEFILE_PATH.exists():
        nombre_a_id = _seed_lotes()
    else:
        return {"seeded": False, "error": "sin fuente de lotes (geojson/shapefile)"}
    n_ndvi, n_rvi, n_ndwi = _seed_series(nombre_a_id)
    return {"seeded": True, "lotes": len(nombre_a_id),
            "ndvi": n_ndvi, "rvi": n_rvi, "ndwi": n_ndwi}
