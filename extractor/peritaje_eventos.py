# -*- coding: utf-8 -*-
"""Peritaje satelital de eventualidades (granizo, helada, viento, sequía, inundación).

Reemplazo directo de los notebooks AgroIA_Eventualidades y su variante v2.0,
saltando Google Earth Engine y hablando en su lugar con las APIs de Copernicus
DataSpace (CDSE): la Process API para bajar rásters NDVI (mediana composita),
la Statistical API queda como fallback rápido cuando alcanza sólo con métricas
agregadas.

Salidas por caso (todas opcionales, controladas por `output_dir`):
  * metricas.csv     -> métricas del análisis
  * severidad.png    -> mapa de severidad + leyenda
  * comparativa.png  -> NDVI PRE / POST / severidad en 3 paneles
  * distribucion.png -> barras de ha por categoría
  * reporte.html     -> informe ejecutivo (paletas coinciden con los notebooks)
  * peritaje.csv     -> puntos de muestreo estratificados para peritos
  * peritaje.kml     -> los mismos puntos, para Google Earth / GPS
  * visor_campo.html -> mapa Folium offline con NDVI post + marcadores + GPS

Las tres deps "de reporte" (matplotlib / folium / simplekml) se importan
perezosamente: si faltan, el análisis numérico sigue corriendo y sólo se
loguea qué salida quedó afuera.
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import requests
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

# Auth/geom: reutilizamos lo que ya está probado en el extractor temporal.
try:
    from extractor.extractor_temporal import (
        get_cdse_token,
        get_clean_geometry,
        get_credentials,
    )
except ImportError:  # ejecutado desde dentro de extractor/
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from extractor.extractor_temporal import (  # noqa: E402
        get_cdse_token,
        get_clean_geometry,
        get_credentials,
    )

# Overshooting tops GOES-19: escaneo forense independiente del NDVI/ERA5, sólo
# tiene sentido físico para eventos convectivos (granizo/viento). Import
# perezoso con degradación explícita si faltan netCDF4/numpy o el módulo.
try:
    from app.services.goes_ot import overshooting_historico
except ImportError:
    overshooting_historico = None

# GOES-19 GLM histórico (rayos): cuarta fuente forense independiente. El bucket
# S3 es archivo permanente; granules ~0.62 MB — mucho más liviano que ABI.
try:
    from app.services.goes_glm import rayos_historicos
except ImportError:
    rayos_historicos = None

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# =============================================================================
# Endpoints CDSE
# =============================================================================
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
STATS_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"

# Reanálisis histórico (Open-Meteo Archive → ERA5-Land) para confirmar heladas.
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
TZ_ARG = "America/Argentina/Cordoba"
# Mismos umbrales que app/services/clima.py, para que la prospectiva (GFS) y el
# peritaje (ERA5-Land) hablen el mismo idioma:
HELADA_METEO_C = 0.0   # mínima < 0 °C  -> helada meteorológica (abrigo, 2 m)
HELADA_AGRO_C = 2.0    # mínima < 2 °C  -> helada agrometeorológica (nivel canopeo ~2 °C < abrigo)


# =============================================================================
# Umbrales por tipo de evento (del notebook v2.0 — valores calibrados campo)
# =============================================================================
UMBRALES_POR_EVENTO: dict[str, dict[str, Any]] = {
    "granizo": {
        "leve": -0.08, "moderado": -0.18, "severo": -0.30, "total": -0.45,
        "descripcion": "daño mecánico por impacto de granizo",
        "recomendacion_severo": "inspección inmediata + evaluación de resiembra",
        "badge_color": "#4A90D9",
    },
    "helada": {
        "leve": -0.06, "moderado": -0.14, "severo": -0.25, "total": -0.40,
        "descripcion": "quemadura foliar por temperaturas bajo cero",
        "recomendacion_severo": "evaluar recuperación en 10–15 días antes de decidir resiembra",
        "badge_color": "#8E44AD",
    },
    "viento": {
        "leve": -0.07, "moderado": -0.15, "severo": -0.27, "total": -0.42,
        "descripcion": "acame y defoliación por vientos fuertes",
        "recomendacion_severo": "inspección de acame + evaluación de encamado irreversible",
        "badge_color": "#27AE60",
    },
    "sequia": {
        "leve": -0.10, "moderado": -0.20, "severo": -0.35, "total": -0.50,
        "descripcion": "estrés hídrico acumulado",
        "recomendacion_severo": "monitorear cierre de ciclo + proyectar pérdida de rinde",
        "badge_color": "#E67E22",
    },
    "inundacion": {
        "leve": -0.09, "moderado": -0.18, "severo": -0.30, "total": -0.48,
        "descripcion": "anegamiento y asfixia radicular",
        "recomendacion_severo": "relevamiento de lámina de agua + días de anegamiento acumulados",
        "badge_color": "#2980B9",
    },
}


# =============================================================================
# Evalscripts (Process API v3, mosaico TILE para poder mediar en JS)
# =============================================================================
def _ndvi_median_evalscript() -> str:
    """NDVI mediano por píxel sobre todas las escenas S2L2A limpias del rango.

    Mosaicking TILE devuelve un array por píxel con TODAS las observaciones
    del período, lo mismo que hacía `.median()` en GEE. Cielo/nube/sombra/
    cirrus se filtran vía SCL antes de mediar; sin observaciones válidas
    devuelve NaN (se convierte en NoData del GeoTIFF).
    """
    return """
    //VERSION=3
    function setup() {
      return {
        input: [{
          bands: ["B04", "B08", "SCL", "dataMask"]
        }],
        output: { bands: 1, sampleType: "FLOAT32" },
        mosaicking: "TILE"
      };
    }
    function evaluatePixel(samples) {
      let vals = [];
      for (let i = 0; i < samples.length; i++) {
        let s = samples[i];
        if (!s.dataMask) continue;
        // SCL: 3=cloud shadow, 8=cloud med, 9=cloud high, 10=cirrus, 11=snow
        let scl = s.SCL;
        if (scl === 3 || scl === 8 || scl === 9 || scl === 10 || scl === 11) continue;
        let denom = s.B08 + s.B04;
        if (denom <= 0) continue;
        vals.push((s.B08 - s.B04) / denom);
      }
      if (vals.length === 0) return [NaN];
      vals.sort(function(a, b) { return a - b; });
      let m = vals.length;
      let mid = (m % 2 === 1)
        ? vals[(m - 1) / 2]
        : 0.5 * (vals[m / 2 - 1] + vals[m / 2]);
      return [mid];
    }
    """


# =============================================================================
# Núcleo — descarga de un composito NDVI vía Process API
# =============================================================================
@dataclass
class NdviRaster:
    """Composito NDVI mediano ya cargado en memoria como numpy array."""
    arr: np.ndarray         # (H, W) float32, NaN donde no hay dato limpio
    transform: Any          # rasterio.Affine
    crs: Any                # rasterio.CRS
    bounds: tuple           # (left, bottom, right, top)
    n_escenas_hint: int     # cota inferior; el catálogo devolvería el número exacto
    ventana: tuple[str, str]

    @property
    def valid(self) -> bool:
        return self.arr.size > 0 and np.isfinite(self.arr).any()

    @property
    def mean(self) -> float | None:
        if not self.valid:
            return None
        return float(np.nanmean(self.arr))


def _bbox_from_geom(geom: BaseGeometry) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = geom.bounds
    return (minx, miny, maxx, maxy)


def _fetch_ndvi_median(
    token: str,
    geom_mapping: dict,
    start: str,
    end: str,
    *,
    resx: float = 0.0001,
    resy: float = 0.0001,
    timeout: int = 120,
    max_intentos: int = 3,
) -> NdviRaster | None:
    """Baja un composito NDVI mediano vía Process API en formato GeoTIFF.

    Retorna None si el request falla o si CDSE devuelve un raster vacío
    (típicamente sin escenas en la ventana).
    """
    import rasterio
    from rasterio.io import MemoryFile

    payload = {
        "input": {
            "bounds": {
                "geometry": geom_mapping,
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
            },
            "data": [{
                "type": "S2L2A",
                "dataFilter": {
                    "timeRange": {"from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"},
                    "mosaickingOrder": "leastCC",
                    "maxCloudCoverage": 80,
                },
            }],
        },
        "output": {
            "resx": resx,
            "resy": resy,
            "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}],
        },
        "evalscript": _ndvi_median_evalscript(),
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "image/tiff",
    }

    ultimo_error = None
    for intento in range(1, max_intentos + 1):
        try:
            r = requests.post(PROCESS_URL, headers=headers, json=payload, timeout=timeout)
            if r.status_code == 200 and r.content:
                with MemoryFile(r.content) as mem:
                    with mem.open() as ds:
                        arr = ds.read(1).astype(np.float32)
                        # rasterio lee NoData como el fill del GeoTIFF; forzamos NaN
                        nodata = ds.nodata
                        if nodata is not None:
                            arr = np.where(arr == nodata, np.nan, arr)
                        return NdviRaster(
                            arr=arr,
                            transform=ds.transform,
                            crs=ds.crs,
                            bounds=tuple(ds.bounds),
                            n_escenas_hint=1,
                            ventana=(start, end),
                        )
            ultimo_error = f"HTTP {r.status_code}: {r.text[:180]}"
        except requests.exceptions.RequestException as exc:
            ultimo_error = str(exc)
        time.sleep(2.0)
    print(f"  [WARN] Process API {start}→{end}: {ultimo_error}")
    return None


def _catalog_scene_count(
    token: str,
    geom_mapping: dict,
    start: str,
    end: str,
    *,
    max_cloud: float = 80,
    timeout: int = 30,
) -> int:
    """Cuenta escenas S2L2A candidatas en la ventana vía Catalog API.

    Usado sólo para reportar el nivel de confianza; si falla se degrada a 0
    y no bloquea el análisis.
    """
    url = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"
    payload = {
        "bbox": list(shape(geom_mapping).bounds),
        "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
        "collections": ["sentinel-2-l2a"],
        "filter": f"eo:cloud_cover < {max_cloud}",
        "filter-lang": "cql2-text",
        "limit": 100,
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            return len(data.get("features", []))
    except requests.exceptions.RequestException:
        pass
    return 0


# =============================================================================
# Confirmación térmica de heladas (ERA5-Land vía Open-Meteo Archive)
# =============================================================================
def verificar_evento_clima_era5(
    lat: float,
    lon: float,
    fecha_evento: str,
    tipo_evento: str,
    *,
    ventana_dias: int = 3,
    timeout: int = 20,
    max_intentos: int = 3,
) -> dict:
    """Valida físicamente un siniestro usando el reanálisis climatológico ERA5-Land/Open-Meteo.
    Soporta los 5 tipos de siniestros agrícolas (helada, viento, sequia, inundacion, granizo).
    """
    ev = date.fromisoformat(fecha_evento)
    
    # Para sequía, necesitamos una ventana histórica más larga (30 días antes del evento).
    # Para los demás, basta con la ventana corta (±ventana_dias).
    if tipo_evento == "sequia":
        ini = (ev - timedelta(days=30)).isoformat()
        fin = (ev + timedelta(days=3)).isoformat()
    else:
        ini = (ev - timedelta(days=ventana_dias)).isoformat()
        fin = (ev + timedelta(days=ventana_dias)).isoformat()

    # Sin `models=era5_land`: ese modelo NO devuelve precipitación ni viento
    # (sólo variables terrestres puras). Dejamos que Open-Meteo Archive use su
    # default (mezcla ERA5 + ERA5-Land): trae temperatura de ERA5-Land para
    # resolución ~9km y precipitación/viento de ERA5. Antes venían todas 0.
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": ini,
        "end_date": fin,
        "daily": "temperature_2m_min,precipitation_sum,wind_gusts_10m_max,wind_speed_10m_max",
        "timezone": TZ_ARG,
    }
    headers = {"User-Agent": "AgroIA-Eventualidades/1.0 (+peritaje-clima)"}

    ultimo_error = None
    data = None
    for intento in range(1, max_intentos + 1):
        try:
            r = requests.get(OPEN_METEO_ARCHIVE, params=params,
                             headers=headers, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                break
            ultimo_error = f"HTTP {r.status_code}: {r.text[:150]}"
        except requests.exceptions.RequestException as exc:
            ultimo_error = str(exc)
        time.sleep(1.5)

    if data is None:
        return {"disponible": False, "fuente": "era5-land", "error": ultimo_error}

    diario = data.get("daily", {})
    fechas = diario.get("time", []) or []
    tmins = diario.get("temperature_2m_min", []) or []
    precips = diario.get("precipitation_sum", []) or []
    gusts = diario.get("wind_gusts_10m_max", []) or []
    speeds = diario.get("wind_speed_10m_max", []) or []

    # Filtrar días con datos válidos
    dias_validos = []
    for i in range(len(fechas)):
        t_min = tmins[i] if i < len(tmins) else None
        prec = precips[i] if i < len(precips) else None
        gust = gusts[i] if i < len(gusts) else None
        speed = speeds[i] if i < len(speeds) else None
        dias_validos.append({
            "fecha": fechas[i],
            "t_min": t_min,
            "prec": prec,
            "gust": gust,
            "speed": speed
        })

    if not dias_validos:
        return {"disponible": False, "fuente": "era5-land",
                "error": "ERA5-Land sin datos en la ventana (evento demasiado reciente)."}

    confirmada = False
    clasificacion = "no confirmado"
    detalles = ""

    # Noches/días del evento ±1 día para buscar coincidencia cercana
    dias_cercanos = [d for d in dias_validos if abs((date.fromisoformat(d["fecha"]) - ev).days) <= 1]

    if tipo_evento == "helada":
        # Encontrar la noche más fría de toda la ventana
        valid_tmins = [d for d in dias_validos if d["t_min"] is not None]
        if not valid_tmins:
            return {"disponible": False, "fuente": "era5-land", "error": "Sin datos de temperatura."}
        fria = min(valid_tmins, key=lambda d: d["t_min"])
        t_min_c = fria["t_min"]
        fecha_min = fria["fecha"]
        
        helada_meteo = t_min_c < HELADA_METEO_C
        helada_agro = t_min_c < HELADA_AGRO_C
        confirmada = helada_agro
        
        if helada_meteo:
            clasificacion = "helada meteorológica registrada"
        elif helada_agro:
            clasificacion = "helada agrometeorológica registrada"
        else:
            clasificacion = "temperaturas sobre cero (sin helada)"
            
        coincide_fecha = abs((date.fromisoformat(fecha_min) - ev).days) <= 1
        detalles = f"mínima canopeo {round(t_min_c, 1)} °C el {fecha_min}{'' if coincide_fecha else ' (⚠️ otra fecha)'}"
        
        return {
            "disponible": True,
            "fuente": "era5-land",
            "confirmada": confirmada,
            "clasificacion": clasificacion,
            "detalles": detalles,
            # campos legacy para compatibilidad:
            "t_min_c": round(t_min_c, 1),
            "noche_mas_fria": fecha_min,
            "coincide_con_evento": coincide_fecha,
            "umbral_meteo_c": HELADA_METEO_C,
            "umbral_agro_c": HELADA_AGRO_C
        }

    elif tipo_evento == "viento":
        # Encontrar ráfagas máximas en la ventana corta
        valid_gusts = [d for d in dias_validos if d["gust"] is not None]
        if not valid_gusts:
            return {"disponible": False, "fuente": "era5-land", "error": "Sin datos de viento/ráfagas."}
        max_g = max(valid_gusts, key=lambda d: d["gust"])
        max_gust = max_g["gust"]
        fecha_max = max_g["fecha"]
        
        confirmada = max_gust > 45.0  # umbral ráfagas para daño mecánico de cultivos
        if max_gust > 60.0:
            clasificacion = "temporal de viento severo registrado"
        elif max_gust > 40.0:
            clasificacion = "viento fuerte registrado"
        else:
            clasificacion = "viento normal o calmo registrado"
            
        coincide_fecha = abs((date.fromisoformat(fecha_max) - ev).days) <= 1
        detalles = f"ráfagas máximas de {round(max_gust, 1)} km/h el {fecha_max}{'' if coincide_fecha else ' (⚠️ otra fecha)'}"

    elif tipo_evento == "inundacion":
        # Lluvia acumulada en la ventana corta (±ventana_dias)
        valid_prec = [d["prec"] for d in dias_validos if d["prec"] is not None]
        total_prec = sum(valid_prec) if valid_prec else 0.0
        
        confirmada = total_prec > 50.0
        if total_prec > 80.0:
            clasificacion = "lluvias acumuladas extremas registradas"
        elif total_prec > 30.0:
            clasificacion = "lluvias moderadas registradas"
        else:
            clasificacion = "lluvias escasas o nulas en la ventana"
            
        detalles = f"lluvia acumulada de {round(total_prec, 1)} mm en la ventana (±{ventana_dias} días)"

    elif tipo_evento == "sequia":
        # Lluvia acumulada en los 30 días previos al evento
        dias_previos = [d for d in dias_validos if (ev - date.fromisoformat(d["fecha"])).days >= 0]
        valid_prec = [d["prec"] for d in dias_previos if d["prec"] is not None]
        total_prec = sum(valid_prec) if valid_prec else 0.0
        
        confirmada = total_prec < 25.0
        if total_prec < 10.0:
            clasificacion = "sequía extrema por déficit hídrico registrado"
        elif total_prec < 25.0:
            clasificacion = "déficit hídrico moderado registrado"
        else:
            clasificacion = "lluvias normales o abundantes registradas"
            
        detalles = f"lluvia acumulada en los últimos 30 días: {round(total_prec, 1)} mm (umbral sequía < 25 mm)"

    elif tipo_evento == "granizo":
        # Granizo ocurre en tormentas convectivas severas (mucha lluvia o ráfagas fuertes cerca de la fecha)
        valid_cercanos = [d for d in dias_cercanos if d["prec"] is not None and d["gust"] is not None]
        if not valid_cercanos:
            valid_cercanos = [d for d in dias_validos if d["prec"] is not None and d["gust"] is not None]
            
        if valid_cercanos:
            dia_max_storm = max(valid_cercanos, key=lambda d: d["prec"])
            max_prec = dia_max_storm["prec"]
            max_gust = dia_max_storm["gust"]
            fecha_storm = dia_max_storm["fecha"]
            
            confirmada = max_prec > 15.0 or max_gust > 45.0
            if max_prec > 25.0 and max_gust > 55.0:
                clasificacion = "condiciones de tormenta severa extrema (granizo muy probable)"
            elif confirmada:
                clasificacion = "condiciones de tormenta convectiva registradas"
            else:
                clasificacion = "sin condiciones de tormenta severa en la fecha"
                
            detalles = f"lluvia máx: {round(max_prec, 1)} mm, ráfagas máx: {round(max_gust, 1)} km/h el {fecha_storm}"
        else:
            clasificacion = "sin datos de tormenta en la ventana"
            detalles = "no se pudieron verificar variables de lluvia/ráfagas"

    return {
        "disponible": True,
        "fuente": "era5-land",
        "confirmada": confirmada,
        "clasificacion": clasificacion,
        "detalles": detalles
    }


# =============================================================================
# Clasificación y estadísticas locales (reemplazan a ee.Reducer)
# =============================================================================
def _clasificar_severidad(delta_adj: np.ndarray, umbrales: dict) -> np.ndarray:
    """Clasifica ΔNDVI ajustado en 0/1/2/3 (sin/leve/moderado/severo).

    NaN queda como 0 (sin dato ≠ sin daño; se aparta con la máscara `valid`
    que reporta stats). Categorías mutuamente excluyentes.
    """
    sev = np.zeros(delta_adj.shape, dtype=np.uint8)
    with np.errstate(invalid="ignore"):
        sev[(delta_adj < umbrales["leve"]) & (delta_adj >= umbrales["moderado"])] = 1
        sev[(delta_adj < umbrales["moderado"]) & (delta_adj >= umbrales["severo"])] = 2
        sev[delta_adj < umbrales["severo"]] = 3
    return sev


def _area_hectareas_lote(geom_shapely: BaseGeometry) -> float:
    """Área en ha usando proyección equal-area local (WGS84 → EPSG:6933)."""
    import pyproj
    from shapely.ops import transform as shp_transform

    proj = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True).transform
    return shp_transform(proj, geom_shapely).area / 10_000.0


def _stats_por_categoria(
    severidad: np.ndarray,
    valid_mask: np.ndarray,
    area_total_ha: float,
) -> dict[str, float]:
    """Ha por categoría, prorrateadas por proporción de píxeles válidos.

    Cada píxel vale (area_total / n_pixeles_validos_del_lote). Es equivalente
    a lo que devolvía `calculate_area` en el notebook (masked pixel * area
    unitaria), pero sin requerir reproyectar el raster.
    """
    n_valid = int(valid_mask.sum())
    if n_valid == 0:
        return {"leve": 0.0, "moderada": 0.0, "severa": 0.0, "afectada": 0.0,
                "sin_dano": area_total_ha}
    ha_por_pixel = area_total_ha / n_valid
    n_leve = int(((severidad == 1) & valid_mask).sum())
    n_mod = int(((severidad == 2) & valid_mask).sum())
    n_sev = int(((severidad == 3) & valid_mask).sum())
    area_leve = n_leve * ha_por_pixel
    area_mod = n_mod * ha_por_pixel
    area_sev = n_sev * ha_por_pixel
    area_afectada = area_leve + area_mod + area_sev
    return {
        "leve": area_leve,
        "moderada": area_mod,
        "severa": area_sev,
        "afectada": area_afectada,
        "sin_dano": max(0.0, area_total_ha - area_afectada),
    }


def _puntos_muestreo(
    severidad: np.ndarray,
    valid_mask: np.ndarray,
    transform_affine,
    n_por_categoria: int = 5,
    seed: int = 42,
) -> pd.DataFrame:
    """Muestreo estratificado por categoría (reemplaza `stratifiedSample`).

    Devuelve un DataFrame con Categoria/Latitud/Longitud/Google_Maps. Elige
    píxeles aleatorios de cada clase; si una clase tiene menos píxeles que
    `n_por_categoria`, toma todos.
    """
    rng = np.random.default_rng(seed)
    filas = []
    for cat_id, cat_nombre in [(1, "Leve"), (2, "Moderado"), (3, "Severo")]:
        mask_cat = (severidad == cat_id) & valid_mask
        ys, xs = np.where(mask_cat)
        if len(ys) == 0:
            continue
        n_pick = min(n_por_categoria, len(ys))
        idxs = rng.choice(len(ys), size=n_pick, replace=False)
        for i in idxs:
            row, col = int(ys[i]), int(xs[i])
            # affine * (col + 0.5, row + 0.5) => centro del píxel
            lon, lat = transform_affine * (col + 0.5, row + 0.5)
            filas.append({
                "Categoria": cat_nombre,
                "Latitud": round(float(lat), 6),
                "Longitud": round(float(lon), 6),
                "Google_Maps": f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}",
            })
    return pd.DataFrame(filas)


# =============================================================================
# Ventanas fenológicas
# =============================================================================
def _ventanas_evento(
    fecha_evento: str,
    ventana_dias: int,
) -> tuple[tuple[str, str], tuple[str, str]]:
    """(pre_ini, pre_fin), (post_ini, post_fin) alrededor del evento."""
    ev = date.fromisoformat(fecha_evento)
    pre_ini = ev - timedelta(days=ventana_dias)
    pre_fin = ev - timedelta(days=1)
    post_ini = ev + timedelta(days=1)
    post_fin = ev + timedelta(days=ventana_dias)
    return ((pre_ini.isoformat(), pre_fin.isoformat()),
            (post_ini.isoformat(), post_fin.isoformat()))


def _mismas_ventanas_en_ano(
    pre: tuple[str, str],
    post: tuple[str, str],
    ano: int,
) -> tuple[tuple[str, str], tuple[str, str]]:
    """Reproyecta las ventanas PRE/POST al año `ano` (misma ventana fenológica)."""
    def _swap(s: str) -> str:
        return f"{ano}{s[4:]}"
    return ((_swap(pre[0]), _swap(pre[1])),
            (_swap(post[0]), _swap(post[1])))


# =============================================================================
# Interpretación agronómica automática
# =============================================================================
def _confianza(n_pre: int, n_post: int) -> tuple[str, str]:
    """Confianza del análisis según cantidad de escenas limpias.

    n_post==1 (una sola imagen post-evento) NO alcanza MEDIA: con un único pase
    satelital, el ruido de píxel (bordes de nube que el SCL no cachea del todo,
    píxeles mixtos de borde de lote) puede producir un % de superficie
    "afectada" que en realidad es ruido de una sola escena, no una serie que
    lo confirme. Se exige al menos 2 escenas post para MEDIA.
    """
    if n_post >= 3 and n_pre >= 3:
        return "ALTA", "🟢"
    if n_post >= 2 and n_pre >= 1:
        return "MEDIA", "🟠"
    return "BAJA", "🔴"


# Umbrales de SUPERFICIE afectada (fracción del lote) para clasificar el evento.
# La interpretación se guía por la extensión del daño —como razona un perito—,
# no por el ΔNDVI medio, que en lotes heterogéneos diluye los parches severos.
PCT_SEVERO_CRITICO = 10.0     # ≥10% del lote en daño severo -> evento crítico
PCT_AFECTADO_SISTEMICO = 30.0  # ≥30% afectado -> impacto sistémico
PCT_AFECTADO_LOCAL = 8.0       # ≥8% afectado -> daño localizado

# NDVI PRE esperable por cultivo y mes (rangos de referencia agronómicos).
# Si el NDVI pre-evento sale MUY por debajo del piso esperable, es señal de que
# el lote probablemente no tenía ese cultivo activo (rastrojo, otra rotación,
# suelo desnudo) — cualquier peritaje ahí es engañoso y hay que advertirlo.
NDVI_PRE_ESPERABLE = {
    # (cultivo, mes) -> (piso_saludable, techo)
    ("mani", 1): (0.55, 0.90), ("mani", 2): (0.60, 0.90),
    ("mani", 3): (0.50, 0.85), ("mani", 4): (0.30, 0.75),
    ("mani", 11): (0.20, 0.50), ("mani", 12): (0.40, 0.75),
    ("soja", 1): (0.55, 0.90), ("soja", 2): (0.60, 0.90),
    ("soja", 3): (0.50, 0.85), ("soja", 12): (0.35, 0.75),
    ("maiz", 1): (0.55, 0.90), ("maiz", 2): (0.60, 0.90),
    ("maiz", 12): (0.40, 0.80),
    ("trigo", 8): (0.30, 0.65), ("trigo", 9): (0.50, 0.85),
    ("trigo", 10): (0.55, 0.90), ("trigo", 11): (0.40, 0.75),
}


def _warning_ndvi_base(ndvi_pre: float, cultivo: str | None,
                       fecha_evento: str) -> str | None:
    """Devuelve una advertencia si el NDVI PRE es incongruente con el cultivo/mes.

    Si el NDVI cae bien por debajo del piso esperable, avisa que el lote
    probablemente no tenía el cultivo activo — hace inválido el peritaje
    aunque los números "cierren".
    """
    if not cultivo or ndvi_pre is None or not math.isfinite(ndvi_pre):
        return None
    clave = cultivo.strip().lower()
    # Normalización básica de acentos.
    clave = (clave.replace("í", "i").replace("á", "a").replace("é", "e")
                  .replace("ó", "o").replace("ú", "u"))
    mes = int(fecha_evento.split("-")[1])
    rango = NDVI_PRE_ESPERABLE.get((clave, mes))
    if rango is None:
        return None
    piso, techo = rango
    if ndvi_pre < piso - 0.10:
        return (f"⚠️ NDVI pre-evento anormalmente bajo ({ndvi_pre:.2f}) para "
                f"{cultivo} en el mes {mes} (rango esperable {piso:.2f}–"
                f"{techo:.2f}). Es posible que el lote no tuviera el cultivo "
                f"activo en la campaña (rastrojo, otra rotación, suelo "
                f"desnudo). El peritaje puede ser engañoso — validar la "
                f"cobertura real antes de emitir informe.")
    return None


def _interpretacion(
    delta_adj_val: float | None,
    umbrales: dict,
    stats: dict[str, float],
    area_total_ha: float,
    n_post: int,
    confirmacion_termica: dict | None = None,
    tipo_evento: str | None = None,
    overshooting_goes: dict | None = None,
    rayos_goes: dict | None = None,
) -> str:
    """Interpretación agronómica cruzando SUPERFICIE afectada + verificación física.

    Regla clave (anti-falsos-positivos): si la verificación climática de ERA5
    tiene datos y devuelve `confirmada=False`, la caída de NDVI NO se atribuye
    al evento declarado — se degrada a "anomalía no atribuible". Si la
    verificación no está disponible (evento reciente o falla de la API), la
    interpretación mantiene el lenguaje "compatible con..." pero explicita la
    reserva. Si `confirmada=True`, se refuerza la atribución.
    """
    if n_post == 0 or delta_adj_val is None:
        return ("❌ ALERTA: sin imágenes post-evento con calidad suficiente. "
                "Ampliar la ventana o complementar con Sentinel-1 (radar).")

    pct_sev = stats["severa"] / area_total_ha * 100 if area_total_ha else 0.0
    pct_afect = stats["afectada"] / area_total_ha * 100 if area_total_ha else 0.0

    # El lote, EN PROMEDIO, ¿perdió vigor respecto de lo esperado? Los
    # triggers basados en % de SUPERFICIE sólo cuentan como "daño" si además
    # el promedio del lote declinó: si delta_adj_val >= 0 (el lote mejoró o
    # quedó igual que el histórico), un % de píxeles por debajo del umbral es
    # heterogeneidad natural o ruido de escena, no evidencia de siniestro.
    # El trigger directo por ΔNDVI medio (delta_adj_val <= umbrales[...]) no
    # necesita este resguardo: ya es negativo por construcción.
    declino = delta_adj_val < 0
    critico = (declino and pct_sev >= PCT_SEVERO_CRITICO) or delta_adj_val <= umbrales["severo"]
    sistemico = (declino and pct_afect >= PCT_AFECTADO_SISTEMICO) or delta_adj_val <= umbrales["moderado"]
    local = (declino and pct_afect >= PCT_AFECTADO_LOCAL) or delta_adj_val <= umbrales["leve"]

    # Estado de la verificación física para calibrar el lenguaje.
    ct = confirmacion_termica or {}
    ct_disp = bool(ct.get("disponible"))
    ct_conf = bool(ct.get("confirmada"))
    ct_detalle = ct.get("detalles") or ct.get("clasificacion") or ""

    # Caso especial: hay daño visible en el NDVI PERO la verificación física
    # tiene datos y desmiente el evento -> reportar como anomalía no atribuible.
    hay_dano = critico or sistemico or local
    if hay_dano and ct_disp and not ct_conf and tipo_evento:
        return (f"⚠️  ANOMALÍA NO ATRIBUIBLE AL EVENTO DECLARADO: se detectó una "
                f"caída de vigor en el {pct_afect:.1f}% del lote "
                f"({stats['afectada']:.1f} ha, ΔNDVI ajustado medio "
                f"{delta_adj_val:+.3f}), pero la verificación climática con "
                f"reanálisis ERA5 NO confirma condiciones de {tipo_evento} en "
                f"la fecha declarada ({ct_detalle}). Posibles causas alternativas: "
                f"estrés hídrico previo, cambio fenológico, cambio de cobertura, "
                f"nubes residuales. NO SE CONFIRMA el siniestro por esta vía.")

    # Sufijos según el estado de la verificación.
    if ct_disp and ct_conf:
        sufijo = f" ✅ Verificación climática CONFIRMA el evento ({ct_detalle})."
    elif ct_disp and not ct_conf:
        sufijo = ""  # ya no debería llegar acá si hay daño (caso arriba)
    else:
        sufijo = " ⚠️ Verificación climática no disponible — atribución sujeta a validación en campo."

    # Tercera fuente independiente: overshooting tops GOES-19 (sólo si se
    # escaneó). Cuando confirma junto con ERA5, es evidencia satelital directa
    # de convección severa — el diferencial del sistema frente a un peritaje
    # que sólo mira NDVI.
    og = overshooting_goes or {}
    if og.get("disponible") and og.get("overshooting"):
        sufijo += (f" 🧊 GOES-19 detectó un overshooting top esa noche "
                  f"(tope {og['ctt_min_c']}°C, {og['delta_k']}K bajo el yunque, "
                  f"{og['hora_pico'][11:16]} UTC) — evidencia satelital directa "
                  f"de convección severa.")

    # Cuarta fuente independiente: rayos GOES-19 GLM. Actividad eléctrica
    # DIRECTAMENTE medida por el satélite (no inferida) — la firma más
    # instrumental de convección severa.
    rg = rayos_goes or {}
    if rg.get("disponible") and rg.get("nivel") in ("alta", "media"):
        etiqueta = ("actividad eléctrica SEVERA" if rg["nivel"] == "alta"
                    else "actividad eléctrica sostenida")
        pico_hora = (rg.get("hora_pico") or "")[11:16] + " UTC" if rg.get("hora_pico") else ""
        sufijo += (f" ⚡ GOES-19 GLM registró {etiqueta}: "
                   f"{rg['descargas_totales']} rayos esa noche "
                   f"(pico {rg['descargas_pico_hora']}/h{' a las ' + pico_hora if pico_hora else ''}) — "
                   f"medición directa de la tormenta.")

    if critico:
        return (f"⚠️  ANOMALÍA CRÍTICA: {pct_sev:.1f}% del lote con daño severo "
                f"({stats['severa']:.1f} ha) y {pct_afect:.1f}% afectado en total. "
                f"Patrón consistente con {umbrales['descripcion']} "
                f"(ΔNDVI ajustado medio {delta_adj_val:+.3f})."
                f"{sufijo} → {umbrales['recomendacion_severo']}")
    if sistemico:
        return (f"🟠 IMPACTO SISTÉMICO: {pct_afect:.1f}% del lote afectado "
                f"({stats['afectada']:.1f} ha), de los cuales {pct_sev:.1f}% severo. "
                f"Afectación extendida compatible con {umbrales['descripcion']} "
                f"(ΔNDVI ajustado medio {delta_adj_val:+.3f}).{sufijo}")
    if local:
        return (f"🟡 DAÑO LOCALIZADO: {pct_afect:.1f}% del lote afectado "
                f"({stats['afectada']:.1f} ha), concentrado en parches. "
                f"ΔNDVI ajustado medio {delta_adj_val:+.3f} dentro del rango, "
                f"pero con sectores por debajo del umbral.{sufijo}")

    if not declino and pct_afect >= PCT_AFECTADO_LOCAL:
        # El promedio del lote NO declinó (delta_adj_val >= 0) pero igual hay
        # un % de píxeles clasificados por debajo del umbral: es heterogeneidad
        # espacial normal (o ruido de una sola escena), no daño atribuible.
        nota_n1 = (" Con una sola escena post-evento (n_post=1), el ruido de "
                  "píxel (bordes de nube residuales, mezcla de borde de lote) "
                  "es la explicación más probable." if n_post == 1 else "")
        return (f"🟢 SIN IMPACTO A NIVEL DE LOTE: el promedio está dentro (o por "
                f"encima) del rango histórico (ΔNDVI ajustado medio "
                f"{delta_adj_val:+.3f}). El {pct_afect:.1f}% de píxeles "
                f"clasificados por debajo del umbral ({stats['afectada']:.1f} ha) "
                f"es consistente con heterogeneidad natural del lote, no con "
                f"daño atribuible al evento declarado.{nota_n1}")

    return (f"🟢 SIN IMPACTO MAYOR: {pct_afect:.1f}% afectado, valores dentro del "
            f"rango histórico (ΔNDVI ajustado medio {delta_adj_val:+.3f}).")


# =============================================================================
# Salidas visuales (matplotlib / folium / simplekml — imports perezosos)
# =============================================================================
_PALETTE_NDVI = ["#8B4513", "#DAA520", "#228B22", "#006400", "#00FF00"]
_PALETTE_DELTA = ["#8B0000", "#DC143C", "#FF6347", "#FFD700", "#F0FFF0"]
_COLOR_SEV = {0: "#00000000", 1: "#FFD700", 2: "#FF8C00", 3: "#8B0000"}


def _colorize_ndvi(arr: np.ndarray) -> "np.ndarray":
    """NDVI [-1,1] → RGB uint8 usando la paleta del notebook."""
    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list("ndvi", _PALETTE_NDVI, N=256)
    vmin, vmax = 0.0, 0.8
    norm = np.clip((arr - vmin) / (vmax - vmin), 0.0, 1.0)
    rgba = (cmap(norm) * 255).astype(np.uint8)
    rgba[~np.isfinite(arr)] = [0, 0, 0, 0]
    return rgba


def _colorize_severidad(sev: np.ndarray) -> np.ndarray:
    """Severidad {0,1,2,3} → RGBA uint8."""
    from matplotlib.colors import to_rgba

    out = np.zeros(sev.shape + (4,), dtype=np.uint8)
    for cat, hex_col in _COLOR_SEV.items():
        rgba = (np.array(to_rgba(hex_col)) * 255).astype(np.uint8)
        out[sev == cat] = rgba
    return out


def _generar_png_comparativa(
    ndvi_pre: np.ndarray, ndvi_post: np.ndarray, severidad: np.ndarray,
    nombre_caso: str, fecha_evento: str, tipo_evento: str,
    ventanas: tuple[tuple[str, str], tuple[str, str]],
    out_path: Path,
) -> bool:
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch
    except ImportError:
        print("  [WARN] matplotlib no instalado — no genero comparativa.png")
        return False
    (pre_ini, pre_fin), (post_ini, post_fin) = ventanas
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    axes[0].imshow(_colorize_ndvi(ndvi_pre))
    axes[0].set_title(f"NDVI PRE-evento\n({pre_ini} → {pre_fin})",
                      fontsize=13, fontweight="bold")
    axes[0].axis("off")
    axes[1].imshow(_colorize_ndvi(ndvi_post))
    axes[1].set_title(f"NDVI POST-evento\n({post_ini} → {post_fin})",
                      fontsize=13, fontweight="bold")
    axes[1].axis("off")
    axes[2].imshow(_colorize_severidad(severidad))
    axes[2].set_title(f"Severidad del daño\n({tipo_evento.capitalize()} — {fecha_evento})",
                      fontsize=13, fontweight="bold")
    axes[2].legend(handles=[
        Patch(facecolor="#FFD700", label="Leve"),
        Patch(facecolor="#FF8C00", label="Moderado"),
        Patch(facecolor="#8B0000", label="Severo"),
    ], loc="lower left", fontsize=9)
    axes[2].axis("off")
    plt.suptitle(f"AgroIA Eventualidades — {nombre_caso}",
                 fontsize=15, fontweight="bold", y=1.02)
    plt.tight_layout()
    plt.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return True


def _generar_png_barras(
    stats: dict[str, float], area_total_ha: float,
    nombre_caso: str, tipo_evento: str, out_path: Path,
) -> bool:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    fig, ax = plt.subplots(figsize=(10, 6))
    cats = ["Sin daño", "Leve", "Moderado", "Severo"]
    vals = [stats["sin_dano"], stats["leve"], stats["moderada"], stats["severa"]]
    colores = ["#4CAF50", "#FFD700", "#FF8C00", "#8B0000"]
    bars = ax.bar(cats, vals, color=colores, edgecolor="black", linewidth=1.2)
    for bar, v in zip(bars, vals):
        pct = v / area_total_ha * 100 if area_total_ha else 0
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + area_total_ha * 0.01,
                f"{v:.0f} ha\n({pct:.1f}%)",
                ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax.set_ylabel("Superficie (ha)", fontsize=12, fontweight="bold")
    ax.set_title(f"Distribución del daño — {nombre_caso} ({tipo_evento.capitalize()})",
                 fontsize=13, fontweight="bold")
    ax.axhline(area_total_ha, color="red", ls="--", lw=2,
               label=f"Área total: {area_total_ha:.0f} ha")
    ax.set_ylim(0, area_total_ha * 1.15)
    ax.grid(axis="y", alpha=0.3)
    ax.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return True


def _generar_html_reporte(
    ctx: dict, out_path: Path,
) -> bool:
    """Reporte HTML ejecutivo — mismo layout que el notebook v2.0, sin GEE."""
    badge = ctx["badge_color"]
    confianza = ctx["confianza"]
    color_conf = {"ALTA": ("#d4edda", "#155724"), "MEDIA": ("#fff3cd", "#856404"),
                  "BAJA": ("#f8d7da", "#721c24")}[confianza]

    # Bloque opcional de confirmación física (ERA5-Land)
    bloque_termico = ""
    ct = ctx.get("confirmacion_termica")
    if ct is not None:
        if ct.get("disponible"):
            confirmada = ct["confirmada"]
            fondo = "#e8f4fd" if confirmada else "#f8f9fa"
            borde = "#2980B9" if confirmada else "#aaa"
            icono = "✅" if confirmada else "⚪"
            
            # Si es helada, usamos la visualización detallada de temperaturas
            if ctx.get("tipo_evento") == "helada" and "t_min_c" in ct:
                coincide = ("coincide con la fecha declarada"
                            if ct["coincide_con_evento"]
                            else "⚠️ la noche más fría cae en otra fecha")
                bloque_termico = f"""
  <h2>🌡️ Confirmación térmica independiente (ERA5-Land)</h2>
  <div class="interp-box" style="background:{fondo}; border-left-color:{borde};">
    <strong>{icono} {ct['clasificacion'].capitalize()}.</strong><br>
    Mínima nocturna de <strong>{ct['t_min_c']} °C</strong> el
    <strong>{ct['noche_mas_fria']}</strong> ({coincide}).<br>
    Reanálisis ERA5-Land (~9 km) contrastado con umbrales
    meteorológico (&lt;{ct.get('umbral_meteo_c', 0.0):.0f} °C) y
    agrometeorológico (&lt;{ct.get('umbral_agro_c', 2.0):.0f} °C). Confirma
    <em>físicamente</em> la causa del daño foliar observado por satélite.
  </div>"""
            else:
                # Para otros tipos de eventos, usamos un bloque genérico de validación climática
                bloque_termico = f"""
  <h2>📊 Verificación física independiente (Open-Meteo Archive / ERA5-Land)</h2>
  <div class="interp-box" style="background:{fondo}; border-left-color:{borde};">
    <strong>{icono} {ct['clasificacion'].capitalize()}.</strong><br>
    {ct.get('detalles', '')}.<br>
    Reanálisis ERA5-Land/Open-Meteo contrastado con umbrales físicos del siniestro.
    Verifica si las variables del clima de la fecha corresponden al daño observado satelitalmente.
  </div>"""
        else:
            titulo = "🌡️ Confirmación térmica" if ctx.get("tipo_evento") == "helada" else "📊 Verificación física"
            bloque_termico = f"""
  <h2>{titulo} independiente (ERA5-Land)</h2>
  <div class="interp-box" style="background:#fff3cd; border-left-color:#856404;">
    ⚠️ No verificable: {ct.get('error', 'sin datos de reanálisis')}.
    El daño foliar satelital no pudo contrastarse con variables físicas de reanálisis.
  </div>"""

    # Bloque opcional de overshooting tops GOES-19 (tercera fuente, forense,
    # sólo escaneada para granizo/viento). Es evidencia satelital DIRECTA de
    # convección severa, independiente del NDVI y del reanálisis ERA5.
    bloque_overshooting = ""
    og = ctx.get("overshooting_goes")
    if og is not None:
        if og.get("disponible") and og.get("granules_con_dato", 0) > 0:
            ot = og.get("overshooting")
            fondo = "#fde8e8" if ot else "#f8f9fa"
            borde = "#c0392b" if ot else "#aaa"
            icono = "🧊" if ot else "⚪"
            estado = ("Overshooting top detectado" if ot
                      else f"Sin firma de overshooting (nivel: {og['nivel']})")
            bloque_overshooting = f"""
  <h2>🛰️ Overshooting tops — GOES-19 ABI (evidencia satelital directa)</h2>
  <div class="interp-box" style="background:{fondo}; border-left-color:{borde};">
    <strong>{icono} {estado}.</strong><br>
    Tope nuboso más frío: <strong>{og['ctt_min_c']} °C</strong> a las
    <strong>{og['hora_pico'][11:16]} UTC</strong>, {og['delta_k']} K por debajo
    del yunque circundante (umbral tropopausa: {og['umbral_tropopausa_c']} °C).<br>
    Escaneo de {og['granules_leidos']} imágenes de disco completo durante la
    noche del evento. Un tope que penetra la tropopausa es la firma satelital
    más directa de una corriente ascendente lo bastante intensa como para
    producir granizo grande.
  </div>"""
        elif og.get("disponible"):
            bloque_overshooting = """
  <h2>🛰️ Overshooting tops — GOES-19 ABI</h2>
  <div class="interp-box" style="background:#f8f9fa; border-left-color:#aaa;">
    ⚪ Cielo despejado en todos los granules muestreados de la ventana (sin
    datos de tope nuboso) — no aporta evidencia adicional de convección.
  </div>"""
        else:
            bloque_overshooting = f"""
  <h2>🛰️ Overshooting tops — GOES-19 ABI</h2>
  <div class="interp-box" style="background:#fff3cd; border-left-color:#856404;">
    ⚠️ No verificable: {og.get('error', 'sin datos satelitales')}.
  </div>"""

    # Bloque opcional de rayos GOES-19 GLM (cuarta fuente independiente).
    # Actividad eléctrica medida DIRECTAMENTE por el satélite — el dato más
    # instrumental de convección severa.
    bloque_rayos = ""
    rg = ctx.get("rayos_goes")
    if rg is not None:
        if rg.get("disponible"):
            nivel = rg.get("nivel", "nula")
            fondo = ("#fde8e8" if nivel == "alta"
                     else "#fff8e1" if nivel == "media"
                     else "#f8f9fa")
            borde = ("#c0392b" if nivel == "alta"
                     else "#f39c12" if nivel == "media"
                     else "#aaa")
            icono = "⚡" if nivel in ("alta", "media") else "⚪"
            hora_pico_txt = ""
            if rg.get("hora_pico"):
                hora_pico_txt = f" a las {rg['hora_pico'][11:16]} UTC"
            bloque_rayos = f"""
  <h2>⚡ Rayos — GOES-19 GLM (actividad eléctrica directamente medida)</h2>
  <div class="interp-box" style="background:{fondo}; border-left-color:{borde};">
    <strong>{icono} Nivel {nivel}.</strong>
    <strong>~{rg['descargas_totales']}</strong> descargas eléctricas totales
    estimadas en la ventana escaneada, con un pico de
    <strong>~{rg['descargas_pico_hora']}</strong> descargas/hora{hora_pico_txt}.<br>
    Escaneo submuestreado de {rg['granules_leidos']} archivos GLM (1 cada
    {rg.get('paso_seg', 120)} s) sobre {rg['radio_km']} km del lote, con el
    conteo escalado al total de granules disponibles por hora — es una
    <em>estimación</em>, no un conteo exacto rayo-por-rayo, pero suficiente
    para calibrar el nivel de actividad. GLM (Geostationary Lightning Mapper)
    detecta las descargas eléctricas en tiempo real — la firma más
    instrumental y directa de convección severa.
  </div>"""
        else:
            bloque_rayos = f"""
  <h2>⚡ Rayos — GOES-19 GLM</h2>
  <div class="interp-box" style="background:#fff3cd; border-left-color:#856404;">
    ⚠️ No verificable: {rg.get('error', 'sin datos satelitales')}.
  </div>"""

    # Bloque opcional de metadatos de póliza/siniestro (sólo si vinieron datos).
    # No afecta el análisis satelital; es la capa administrativa del reporte.
    meta = ctx.get("metadata") or {}
    campos_meta = [(k, v) for k, v in (
        ("🏢 Aseguradora", meta.get("aseguradora")),
        ("📄 N° de póliza", meta.get("numero_poliza")),
        ("👤 Productor / asegurado", meta.get("productor")),
    ) if v]
    bloque_metadata = ""
    if campos_meta:
        filas_meta = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in campos_meta)
        bloque_metadata = f"""
  <h2>🗂️ Datos del siniestro</h2>
  <table>
    <tr><th>Campo</th><th>Valor</th></tr>
    {filas_meta}
  </table>"""

    bloque_comentarios = ""
    comentarios = meta.get("comentarios_perito")
    if comentarios:
        comentarios_html = (comentarios.replace("&", "&amp;").replace("<", "&lt;")
                            .replace(">", "&gt;").replace("\n", "<br>"))
        bloque_comentarios = f"""
  <h2>📝 Comentarios del perito</h2>
  <div class="interp-box">{comentarios_html}</div>"""

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>AgroIA Eventualidades — {ctx['nombre_caso']}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Segoe UI', Arial, sans-serif;
           background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding: 24px; }}
    .container {{ max-width: 1100px; margin: 0 auto; background: white;
                 border-radius: 16px; padding: 36px;
                 box-shadow: 0 24px 48px rgba(0,0,0,0.3); }}
    .header {{ text-align: center; margin-bottom: 32px; }}
    .header h1 {{ font-size: 24px; color: #1a1a2e; margin-bottom: 8px; }}
    .badge {{ display: inline-block; padding: 6px 18px; border-radius: 20px;
             background: {badge}; color: white; font-weight: bold; font-size: 13px;
             letter-spacing: 1px; text-transform: uppercase; margin-bottom: 12px; }}
    .meta {{ color: #666; font-size: 13px; line-height: 1.8; }}
    .stats-grid {{ display: grid;
                  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                  gap: 16px; margin: 28px 0; }}
    .stat-card {{ background: linear-gradient(135deg, #f8f9fa, #e9ecef);
                 border-radius: 12px; padding: 20px; text-align: center;
                 border-top: 4px solid {badge}; }}
    .stat-label {{ font-size: 12px; color: #666; margin-bottom: 6px; }}
    .stat-number {{ font-size: 30px; font-weight: bold; color: #1a1a2e; }}
    .stat-sub {{ font-size: 12px; color: #888; margin-top: 4px; }}
    h2 {{ font-size: 17px; color: #1a1a2e; border-left: 4px solid {badge};
          padding-left: 12px; margin: 28px 0 14px; }}
    .severity-bar {{ display: flex; border-radius: 8px; overflow: hidden;
                    height: 36px; margin: 16px 0; font-size: 13px; font-weight: bold; }}
    .sev-severo   {{ background: #8B0000; color: white; display: flex;
                    align-items: center; justify-content: center; }}
    .sev-moderado {{ background: #FF8C00; color: white; display: flex;
                    align-items: center; justify-content: center; }}
    .sev-leve     {{ background: #FFD700; color: #333; display: flex;
                    align-items: center; justify-content: center; }}
    .sev-ok       {{ background: #4CAF50; color: white; display: flex;
                    align-items: center; justify-content: center; }}
    .interp-box {{ background: #f8f9fa; border-left: 5px solid {badge};
                  border-radius: 8px; padding: 16px; font-size: 14px;
                  line-height: 1.7; margin: 16px 0; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th {{ background: #1a1a2e; color: white; padding: 10px 14px; text-align: left; }}
    td {{ padding: 9px 14px; border-bottom: 1px solid #eee; }}
    tr:nth-child(even) {{ background: #f8f9fa; }}
    ul {{ padding-left: 20px; font-size: 14px; line-height: 2; }}
    .footer {{ text-align: center; margin-top: 32px; padding-top: 20px;
              border-top: 1px solid #ddd; color: #aaa; font-size: 12px; }}
    .confidence {{ display: inline-block; padding: 4px 12px; border-radius: 12px;
                  font-size: 12px; font-weight: bold;
                  background: {color_conf[0]}; color: {color_conf[1]}; }}
  </style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="badge">🌩️ {ctx['tipo_evento']}</div>
    <h1>🌾 INFORME AgroIA EVENTUALIDADES</h1>
    <div class="meta">
      <strong>Caso:</strong> {ctx['nombre_caso']} &nbsp;|&nbsp;
      <strong>Fecha del evento:</strong> {ctx['fecha_evento']} &nbsp;|&nbsp;
      <strong>Fuente:</strong> Copernicus / Sentinel-2 L2A (10 m) &nbsp;|&nbsp;
      <strong>Método:</strong> ΔNDVI con baseline histórico {ctx['n_baseline']} años (CDSE Process API)
    </div>
  </div>
{bloque_metadata}
  <div class="stats-grid">
    <div class="stat-card"><div class="stat-label">📐 Área total</div>
      <div class="stat-number">{ctx['area_total_ha']:.0f}</div>
      <div class="stat-sub">hectáreas</div></div>
    <div class="stat-card"><div class="stat-label">💥 Área afectada</div>
      <div class="stat-number">{ctx['area_afectada']:.0f}</div>
      <div class="stat-sub">{ctx['pct_afectado']:.1f}% del lote</div></div>
    <div class="stat-card"><div class="stat-label">🌿 NDVI Pre</div>
      <div class="stat-number">{ctx['ndvi_pre']:.3f}</div>
      <div class="stat-sub">{ctx['pre_ini']} → {ctx['pre_fin']}</div></div>
    <div class="stat-card"><div class="stat-label">🌾 NDVI Post</div>
      <div class="stat-number">{ctx['ndvi_post']:.3f}</div>
      <div class="stat-sub">{ctx['post_ini']} → {ctx['post_fin']}</div></div>
    <div class="stat-card"><div class="stat-label">📉 ΔNDVI ajustado</div>
      <div class="stat-number">{ctx['delta_adj']:+.3f}</div>
      <div class="stat-sub">anomalía sobre baseline</div></div>
    <div class="stat-card"><div class="stat-label">🔎 Confianza</div>
      <div class="stat-number" style="font-size:20px">{ctx['emoji_conf']} {confianza}</div>
      <div class="stat-sub">{ctx['n_pre']} esc PRE / {ctx['n_post']} esc POST</div></div>
  </div>

  <h2>📊 Distribución del daño</h2>
  <div class="severity-bar">
    <div class="sev-severo"   style="width:{ctx['pct_sev']:.1f}%">🔴 {ctx['area_severa']:.0f} ha ({ctx['pct_sev']:.1f}%)</div>
    <div class="sev-moderado" style="width:{ctx['pct_mod']:.1f}%">🟠 {ctx['area_moderada']:.0f} ha ({ctx['pct_mod']:.1f}%)</div>
    <div class="sev-leve"     style="width:{ctx['pct_leve']:.1f}%">🟡 {ctx['area_leve']:.0f} ha ({ctx['pct_leve']:.1f}%)</div>
    <div class="sev-ok"       style="flex:1">✅ {ctx['sin_dano']:.0f} ha ({ctx['pct_sin']:.1f}%)</div>
  </div>

  <h2>📋 Tabla de métricas</h2>
  <table>
    <tr><th>Métrica</th><th>Valor</th></tr>
    <tr><td>Área total</td><td>{ctx['area_total_ha']:.1f} ha</td></tr>
    <tr><td>NDVI pre-evento</td><td>{ctx['ndvi_pre']:.4f}</td></tr>
    <tr><td>NDVI post-evento</td><td>{ctx['ndvi_post']:.4f}</td></tr>
    <tr><td>ΔNDVI observado</td><td>{ctx['delta_obs']:+.4f}</td></tr>
    <tr><td>Baseline histórico ({ctx['n_baseline']} años)</td><td>{ctx['baseline']:+.4f}</td></tr>
    <tr><td>ΔNDVI ajustado (anomalía)</td><td><strong>{ctx['delta_adj']:+.4f}</strong></td></tr>
    <tr><td>Área leve</td><td>{ctx['area_leve']:.1f} ha ({ctx['pct_leve']:.1f}%)</td></tr>
    <tr><td>Área moderada</td><td>{ctx['area_moderada']:.1f} ha ({ctx['pct_mod']:.1f}%)</td></tr>
    <tr><td>Área severa</td><td>{ctx['area_severa']:.1f} ha ({ctx['pct_sev']:.1f}%)</td></tr>
    <tr><td>Total afectado</td><td><strong>{ctx['area_afectada']:.1f} ha ({ctx['pct_afectado']:.1f}%)</strong></td></tr>
  </table>

  <h2>🎯 Interpretación agronómica</h2>
  <div class="interp-box">{ctx['interpretacion']}</div>
{bloque_termico}
{bloque_overshooting}
{bloque_rayos}
{bloque_comentarios}
  <h2>✅ Recomendaciones</h2>
  <ul>
    <li>Priorizar inspección en campo del área severa ({ctx['area_severa']:.0f} ha)</li>
    <li>{ctx['recomendacion_severo']}</li>
    <li>Documentar con fotografías georreferenciadas en los puntos de muestreo exportados</li>
    <li>Monitorear evolución post-evento con nueva imagen en 15 días</li>
    <li>Verificar consistencia con registros meteorológicos del evento ({ctx['fecha_evento']})</li>
  </ul>

  <h2>🔬 Trazabilidad del análisis</h2>
  <table>
    <tr><th>Parámetro</th><th>Detalle</th></tr>
    <tr><td>Sensor</td><td>Sentinel-2 MSI L2A — Copernicus / ESA</td></tr>
    <tr><td>Fuente</td><td>Copernicus DataSpace (CDSE) — Process API v1 (sh.dataspace.copernicus.eu)</td></tr>
    <tr><td>Resolución espacial</td><td>~10 m/píxel (resx={ctx['resx']:.5f}°)</td></tr>
    <tr><td>Índice utilizado</td><td>NDVI = (B08 − B04) / (B08 + B04)</td></tr>
    <tr><td>Máscara de nubes</td><td>SCL (3/8/9/10/11 descartados) por píxel y escena</td></tr>
    <tr><td>Composito por ventana</td><td>Mediana pixel-a-pixel (evalscript v3, mosaicking TILE)</td></tr>
    <tr><td>Baseline fenológico</td><td>{ctx['anos_baseline_txt']} (misma ventana del calendario)</td></tr>
    <tr><td>Confianza del análisis</td><td><span class="confidence">{confianza}</span> — {ctx['n_pre']} esc PRE / {ctx['n_post']} esc POST</td></tr>
    <tr><td>Fecha de generación</td><td>{ctx['generado']}</td></tr>
  </table>

  <div class="footer">
    Reporte generado por <strong>AgroIA Eventualidades</strong> — Pipeline satelital automático sobre Copernicus/Sentinel-2 (CDSE).<br>
    Los valores son estimaciones satelitales. Se recomienda validación en campo para siniestros formales.
  </div>
</div>
</body>
</html>"""
    out_path.write_text(html, encoding="utf-8")
    return True


# =============================================================================
# Reporte PDF cerrado (documento oficial para el expediente del siniestro)
# =============================================================================
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"   # pictogramas, emoticones, transporte, suplementarios
    "\U00002600-\U000027BF"   # símbolos varios, dingbats
    "\U0001F1E6-\U0001F1FF"   # indicadores regionales (banderas)
    "️"                   # selector de variación
    "]+"
)


# Símbolos tipográficos/matemáticos usados en los textos generados (interpre-
# tación, warnings, clasificaciones) que quedan FUERA de Latin-1 pero no son
# emoji — hay que traducirlos a ASCII explícitamente o fpdf2 revienta con
# "Character ... is outside the range of characters supported by the font".
_REEMPLAZOS_PDF = {
    "Δ": "Delta ", "–": "-", "—": "-", "−": "-", "…": "...",
    "→": "->", "←": "<-", "≥": ">=", "≤": "<=", "≈": "~", "≠": "!=",
    "─": "-",
}


def _limpiar_pdf(txt: str | None) -> str:
    """Sanea un texto para las fuentes core de fpdf2 (sólo Latin-1).

    Las tildes/ñ del español SÍ se preservan (están en Latin-1). Los símbolos
    tipográficos fuera de rango (Δ, guiones largos, flechas, ≥…) se traducen a
    ASCII; los emoji se eliminan directamente (no tienen equivalente ASCII).
    """
    if not txt:
        return ""
    s = str(txt)
    for k, v in _REEMPLAZOS_PDF.items():
        s = s.replace(k, v)
    s = _EMOJI_RE.sub("", s)
    s = re.sub(r"[ \t]{2,}", " ", s).strip()
    # Red de seguridad: cualquier carácter no anticipado se descarta en vez de
    # romper la generación del PDF entero (fpdf2 con fuentes core es Latin-1).
    return s.encode("latin-1", errors="ignore").decode("latin-1")


def _hex_a_rgb(hexcolor: str) -> tuple[int, int, int]:
    h = hexcolor.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _generar_pdf_reporte(
    ctx: dict, out_path: Path,
    severidad_png: Path | None = None,
    comparativa_png: Path | None = None,
) -> bool:
    """Reporte PDF cerrado — el documento que se adjunta al expediente.

    Reutiliza el mismo `ctx` que arma el reporte HTML (misma fuente de verdad,
    sin duplicar cálculos). Usa fpdf2 con fuentes core (sin dependencias de
    fuentes externas) — por eso el texto libre se limpia de emojis con
    `_limpiar_pdf`; las imágenes SÍ pueden llevar emoji, son bitmaps.
    """
    try:
        from fpdf import FPDF
    except ImportError:
        print("  [WARN] fpdf2 no instalado — no genero reporte.pdf")
        return False

    try:
        T = _limpiar_pdf
        badge_rgb = _hex_a_rgb(ctx["badge_color"])

        pdf = FPDF(orientation="P", unit="mm", format="A4")
        pdf.set_auto_page_break(auto=True, margin=18)
        pdf.set_margins(15, 15, 15)
        pdf.add_page()

        # --- Encabezado ------------------------------------------------
        pdf.set_fill_color(*badge_rgb)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, T(ctx["tipo_evento"]).upper(), fill=True, align="C")
        pdf.ln(10)

        pdf.set_text_color(20, 20, 30)
        pdf.set_font("Helvetica", "B", 17)
        pdf.cell(0, 9, "INFORME DE PERITAJE SATELITAL", align="C")
        pdf.ln(9)

        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(90, 90, 90)
        pdf.multi_cell(
            0, 5,
            f"Caso: {T(ctx['nombre_caso'])}   |   Fecha del evento: {ctx['fecha_evento']}   |   "
            f"Metodo: NDVI Sentinel-2 (Copernicus CDSE) con baseline {ctx['n_baseline']} anios",
            align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        # --- Datos del siniestro (si vinieron metadatos) ----------------
        meta = ctx.get("metadata") or {}
        campos_meta = [(k, v) for k, v in (
            ("Aseguradora", meta.get("aseguradora")),
            ("N. de poliza", meta.get("numero_poliza")),
            ("Productor / asegurado", meta.get("productor")),
        ) if v]
        if campos_meta:
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(20, 20, 30)
            pdf.cell(0, 7, "Datos del siniestro")
            pdf.ln(8)
            for k, v in campos_meta:
                pdf.set_font("Helvetica", "B", 10)
                pdf.cell(48, 6, T(k) + ":")
                pdf.set_font("Helvetica", "", 10)
                pdf.cell(0, 6, T(v))
                pdf.ln(6)
            pdf.ln(2)

        # --- Métricas clave ----------------------------------------------
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(20, 20, 30)
        pdf.cell(0, 7, "Metricas clave")
        pdf.ln(8)
        filas_metricas = [
            ("Area total", f"{ctx['area_total_ha']:.1f} ha"),
            ("Area afectada", f"{ctx['area_afectada']:.1f} ha ({ctx['pct_afectado']:.1f}%)"),
            ("NDVI pre -> post", f"{ctx['ndvi_pre']:.3f} -> {ctx['ndvi_post']:.3f}"),
            ("Delta NDVI ajustado", f"{ctx['delta_adj']:+.3f}"),
            ("Baseline historico", f"{ctx['baseline']:+.3f} ({T(ctx['anos_baseline_txt'])})"),
            ("Confianza del analisis",
             f"{ctx['confianza']} ({ctx['n_pre']} esc PRE / {ctx['n_post']} esc POST)"),
        ]
        for k, v in filas_metricas:
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(55, 6, T(k))
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(0, 6, T(v))
            pdf.ln(6)
        pdf.ln(2)

        # --- Distribución del daño: barra + tabla -------------------------
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 7, "Distribucion del dano")
        pdf.ln(8)

        x0, y0 = pdf.get_x(), pdf.get_y()
        ancho_total, alto_barra = 180.0, 8.0
        segmentos = [
            (ctx["pct_sev"], (139, 0, 0)), (ctx["pct_mod"], (255, 140, 0)),
            (ctx["pct_leve"], (255, 215, 0)), (ctx["pct_sin"], (76, 175, 80)),
        ]
        x = x0
        for pct, rgb in segmentos:
            w = ancho_total * (pct / 100.0) if pct > 0 else 0.0
            if w > 0:
                pdf.set_fill_color(*rgb)
                pdf.rect(x, y0, w, alto_barra, style="F")
            x += w
        pdf.set_xy(x0, y0 + alto_barra + 3)

        tabla_sev = [
            ("Severo", ctx["area_severa"], ctx["pct_sev"], (139, 0, 0)),
            ("Moderado", ctx["area_moderada"], ctx["pct_mod"], (255, 140, 0)),
            ("Leve", ctx["area_leve"], ctx["pct_leve"], (255, 215, 0)),
            ("Sin dano", ctx["sin_dano"], ctx["pct_sin"], (76, 175, 80)),
        ]
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(30, 30, 30)
        for nombre, ha, pct, rgb in tabla_sev:
            yr = pdf.get_y()
            pdf.set_fill_color(*rgb)
            pdf.rect(pdf.get_x(), yr + 1, 4, 4, style="F")
            pdf.set_x(pdf.get_x() + 6)
            pdf.cell(0, 6, f"{nombre}: {ha:.1f} ha ({pct:.1f}%)")
            pdf.ln(6)
        pdf.ln(2)

        # --- Interpretación agronómica -------------------------------------
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(20, 20, 30)
        pdf.cell(0, 7, "Interpretacion agronomica")
        pdf.ln(8)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_fill_color(245, 245, 245)
        pdf.multi_cell(0, 5.2, T(ctx["interpretacion"]), fill=True,
                      new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        # --- Verificación climática ERA5 -----------------------------------
        ct = ctx.get("confirmacion_termica")
        if ct is not None:
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(0, 6, "Verificacion fisica independiente (Open-Meteo / ERA5)")
            pdf.ln(7)
            pdf.set_font("Helvetica", "", 9)
            if ct.get("disponible"):
                estado = "CONFIRMADO" if ct.get("confirmada") else "NO CONFIRMADO"
                detalle = ct.get("detalles") or ct.get("clasificacion") or ""
                pdf.multi_cell(0, 5, f"{estado}: {T(detalle)}",
                              new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.multi_cell(0, 5, f"No verificable: {T(ct.get('error', ''))}",
                              new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

        # --- Overshooting GOES-19 -------------------------------------------
        og = ctx.get("overshooting_goes")
        if og is not None:
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(0, 6, "Overshooting tops - GOES-19 ABI")
            pdf.ln(7)
            pdf.set_font("Helvetica", "", 9)
            if og.get("disponible") and og.get("granules_con_dato", 0) > 0:
                estado = ("OVERSHOOTING DETECTADO" if og.get("overshooting")
                         else f"Sin firma (nivel {og['nivel']})")
                pdf.multi_cell(
                    0, 5,
                    f"{estado}. Tope mas frio: {og['ctt_min_c']} C a las "
                    f"{og['hora_pico'][11:16]} UTC, {og['delta_k']} K bajo el yunque. "
                    f"{og['granules_leidos']} imagenes escaneadas.",
                    new_x="LMARGIN", new_y="NEXT")
            elif og.get("disponible"):
                pdf.multi_cell(0, 5, "Cielo despejado en toda la ventana escaneada.",
                              new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.multi_cell(0, 5, f"No verificable: {T(og.get('error', ''))}",
                              new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

        # --- Rayos GOES-19 GLM (cuarta fuente forense) ---------------------
        rg = ctx.get("rayos_goes")
        if rg is not None:
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(0, 6, "Rayos - GOES-19 GLM (actividad electrica directa)")
            pdf.ln(7)
            pdf.set_font("Helvetica", "", 9)
            if rg.get("disponible"):
                hora_pico_txt = ""
                if rg.get("hora_pico"):
                    hora_pico_txt = f" a las {rg['hora_pico'][11:16]} UTC"
                pdf.multi_cell(
                    0, 5,
                    f"Nivel {rg.get('nivel', 'nula')}. "
                    f"~{rg['descargas_totales']} descargas totales estimadas en la "
                    f"ventana escaneada, pico de ~{rg['descargas_pico_hora']} "
                    f"descargas/hora{hora_pico_txt}. Escaneo submuestreado de "
                    f"{rg['granules_leidos']} archivos GLM sobre {rg['radio_km']} km "
                    f"del lote (estimacion escalada, no conteo exacto).",
                    new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.multi_cell(0, 5, f"No verificable: {T(rg.get('error', ''))}",
                              new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

        # --- Comentarios del perito -----------------------------------------
        comentarios = meta.get("comentarios_perito")
        if comentarios:
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(0, 6, "Comentarios del perito")
            pdf.ln(7)
            pdf.set_font("Helvetica", "", 9)
            pdf.multi_cell(0, 5, T(comentarios), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

        # --- Página 2: imágenes ------------------------------------------------
        if (severidad_png and Path(severidad_png).exists()) or \
           (comparativa_png and Path(comparativa_png).exists()):
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 13)
            pdf.set_text_color(20, 20, 30)
            pdf.cell(0, 8, "Mapa de severidad y comparativa NDVI")
            pdf.ln(10)

            if severidad_png and Path(severidad_png).exists():
                pdf.set_font("Helvetica", "", 9)
                pdf.cell(0, 5, "Mapa de severidad del dano")
                pdf.ln(6)
                pdf.image(str(severidad_png), w=90)
                pdf.ln(4)

            if comparativa_png and Path(comparativa_png).exists():
                pdf.set_font("Helvetica", "", 9)
                pdf.cell(0, 5, "Comparativa NDVI pre / post / severidad")
                pdf.ln(6)
                pdf.image(str(comparativa_png), w=180)
                pdf.ln(4)

        # --- Recomendaciones + firmas ------------------------------------------
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(20, 20, 30)
        pdf.cell(0, 7, "Recomendaciones")
        pdf.ln(8)
        pdf.set_font("Helvetica", "", 10)
        recomendaciones = [
            f"Priorizar inspeccion en campo del area severa ({ctx['area_severa']:.0f} ha)",
            T(ctx["recomendacion_severo"]),
            "Documentar con fotografias georreferenciadas en los puntos de muestreo exportados",
            "Monitorear evolucion post-evento con nueva imagen en 15 dias",
            f"Verificar consistencia con registros meteorologicos del evento ({ctx['fecha_evento']})",
        ]
        for r in recomendaciones:
            pdf.multi_cell(0, 5.5, f"- {r}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(10)

        y_firma = pdf.get_y() + 15
        pdf.set_xy(15, y_firma)
        pdf.cell(85, 0, "", border="T")
        pdf.set_xy(110, y_firma)
        pdf.cell(85, 0, "", border="T")
        pdf.set_xy(15, y_firma + 2)
        pdf.cell(85, 5, "Firma del perito", align="C")
        pdf.set_xy(110, y_firma + 2)
        pdf.cell(85, 5, "Firma del productor / asegurado", align="C")

        # --- Footer -------------------------------------------------------------
        pdf.set_y(-20)
        pdf.set_font("Helvetica", "I", 7)
        pdf.set_text_color(140, 140, 140)
        pdf.multi_cell(
            0, 4,
            "Reporte generado por AgroIA Eventualidades - Pipeline satelital "
            "automatico sobre Copernicus/Sentinel-2. Los valores son estimaciones "
            "satelitales. Se recomienda validacion en campo para siniestros formales. "
            f"Generado: {ctx['generado']}", align="C", new_x="LMARGIN", new_y="NEXT")

        pdf.output(str(out_path))
        return True
    except Exception as exc:  # noqa: BLE001 — un PDF roto no debe tumbar el peritaje
        print(f"  [WARN] Error generando reporte.pdf: {exc}")
        return False


def _generar_kml_peritaje(
    df: pd.DataFrame, nombre_caso: str, tipo_evento: str, fecha_evento: str,
    out_path: Path,
) -> bool:
    try:
        import simplekml
    except ImportError:
        print("  [WARN] simplekml no instalado — no genero peritaje.kml")
        return False
    if df.empty:
        return False
    kml = simplekml.Kml(name=f"Muestreo AgroIA — {nombre_caso}")
    estilos = {"Severo": "ff0000ff", "Moderado": "ff00a5ff", "Leve": "ff00ffff"}
    for _, row in df.iterrows():
        pnt = kml.newpoint(
            name=row["Categoria"],
            coords=[(row["Longitud"], row["Latitud"])],
        )
        pnt.style.labelstyle.color = estilos.get(row["Categoria"], "ffffffff")
        pnt.style.iconstyle.icon.href = (
            "http://maps.google.com/mapfiles/kml/paddle/wht-circle.png"
        )
        pnt.description = (
            f"Punto de validación AgroIA\n"
            f"Caso: {nombre_caso}\n"
            f"Evento: {tipo_evento} — {fecha_evento}\n"
            f"Categoría: {row['Categoria']}"
        )
    kml.save(str(out_path))
    return True


def _generar_visor_folium(
    centro_lat_lon: tuple[float, float], df_puntos: pd.DataFrame,
    ndvi_post_png: Path | None, bounds: tuple[float, float, float, float],
    nombre_caso: str, tipo_evento: str, out_path: Path,
) -> bool:
    """Visor Folium offline con Google Satellite + NDVI post como overlay PNG.

    Bounds = (left, bottom, right, top) en WGS84.
    """
    try:
        import folium
        from folium import plugins
    except ImportError:
        print("  [WARN] folium no instalado — no genero visor_campo.html")
        return False
    m = folium.Map(
        location=list(centro_lat_lon), zoom_start=13, control_scale=True,
        tiles="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
        attr="Google Satellite",
    )
    if ndvi_post_png and ndvi_post_png.exists():
        left, bottom, right, top = bounds
        folium.raster_layers.ImageOverlay(
            name=f"NDVI post-evento — {nombre_caso}",
            image=str(ndvi_post_png),
            bounds=[[bottom, left], [top, right]],
            opacity=0.75,
        ).add_to(m)
    colores_folium = {"Severo": "red", "Moderado": "orange", "Leve": "cadetblue"}
    if not df_puntos.empty:
        for _, row in df_puntos.iterrows():
            folium.Marker(
                location=[row["Latitud"], row["Longitud"]],
                popup=folium.Popup(
                    f"<b>{row['Categoria']}</b><br>"
                    f"Lat: {row['Latitud']:.5f}<br>"
                    f"Lon: {row['Longitud']:.5f}<br>"
                    f"<a href='{row['Google_Maps']}' target='_blank'>Ver en Maps</a>",
                    max_width=220,
                ),
                tooltip=f"Punto {row['Categoria']} — {tipo_evento}",
                icon=folium.Icon(color=colores_folium.get(row["Categoria"], "blue"),
                                 icon="info-sign"),
            ).add_to(m)
    plugins.LocateControl(auto_start=False).add_to(m)
    plugins.MeasureControl(primary_length_unit="meters").add_to(m)
    folium.LayerControl().add_to(m)
    m.save(str(out_path))
    return True


def _guardar_ndvi_png(arr: np.ndarray, out_path: Path) -> bool:
    """Guarda un NDVI array como PNG con la paleta del notebook (transparente en NaN)."""
    try:
        from PIL import Image
    except ImportError:
        print("  [WARN] Pillow no instalado — no genero PNG del NDVI")
        return False
    rgba = _colorize_ndvi(arr)
    Image.fromarray(rgba, mode="RGBA").save(out_path)
    return True


# =============================================================================
# API pública — punto de entrada único
# =============================================================================
def peritar_evento(
    *,
    geom: dict | BaseGeometry,
    fecha_evento: str,
    tipo_evento: str = "granizo",
    nombre_caso: str | None = None,
    cultivo: str | None = None,
    metadata: dict | None = None,
    ventana_dias: int = 14,
    baseline_anos: int = 3,
    resx: float = 0.0001,
    resy: float = 0.0001,
    output_dir: str | Path | None = None,
    credenciales: tuple[str, str] | None = None,
    token: str | None = None,
    n_muestreo_por_categoria: int = 5,
) -> dict:
    """Ejecuta el peritaje completo para un lote y devuelve todas las métricas.

    Args:
        geom: GeoJSON dict (Feature/geometry/FeatureCollection) o shapely Geometry
              del lote en WGS84 (EPSG:4326).
        fecha_evento: "YYYY-MM-DD" — día del evento climático.
        tipo_evento: uno de {granizo, helada, viento, sequia, inundacion}.
        nombre_caso: etiqueta libre para reportes (default: derivada de la fecha).
        metadata: datos de póliza/siniestro opcionales (aseguradora, numero_poliza,
                  productor, comentarios_perito) — no afectan el cómputo, sólo se
                  guardan en el resultado y se muestran en el reporte HTML si vienen.
        ventana_dias: días antes/después del evento para el composito NDVI.
        baseline_anos: n de años previos (misma ventana calendario) para baseline.
        resx / resy: resolución del raster pedido a la Process API (grados WGS84).
        output_dir: si se pasa, se escriben todas las salidas ahí. Si es None,
                    sólo se computan las métricas y se devuelven en el dict.
        credenciales: (usuario, password) de CDSE. Si es None, se leen del .env.
        token: si ya se tiene, evita re-autenticar.
        n_muestreo_por_categoria: cuántos puntos para peritos por severidad.

    Returns:
        dict con métricas, paths de salida (si output_dir), interpretación,
        confianza y ventanas usadas.
    """
    if tipo_evento not in UMBRALES_POR_EVENTO:
        raise ValueError(f"tipo_evento debe ser uno de {list(UMBRALES_POR_EVENTO)}")
    umbrales = UMBRALES_POR_EVENTO[tipo_evento]

    # --- Normalizar geometría --------------------------------------------------
    if isinstance(geom, dict):
        if geom.get("type") == "FeatureCollection":
            feats = geom["features"]
            if not feats:
                raise ValueError("FeatureCollection vacío")
            geom_shp = shape(feats[0]["geometry"])
        elif geom.get("type") == "Feature":
            geom_shp = shape(geom["geometry"])
        else:
            geom_shp = shape(geom)
    elif isinstance(geom, BaseGeometry):
        geom_shp = geom
    else:
        raise TypeError(f"geom debe ser dict o shapely, no {type(geom)}")
    geom_shp = get_clean_geometry(geom_shp)
    geom_mapping = mapping(geom_shp)

    nombre_caso = nombre_caso or f"peritaje_{fecha_evento}_{tipo_evento}"
    print("=" * 72)
    print(f"🌾 PERITAJE AgroIA — {nombre_caso}")
    print(f"   Evento: {tipo_evento.upper()} — {fecha_evento}")
    print("=" * 72)

    # --- Auth CDSE ------------------------------------------------------------
    if token is None:
        if credenciales:
            user, pwd = credenciales
        else:
            user, pwd = get_credentials()
        if not user or not pwd:
            raise RuntimeError("Credenciales CDSE no encontradas (env EODAG__COP_DATASPACE__…)")
        token = get_cdse_token(user, pwd)
        if not token:
            raise RuntimeError("No se pudo obtener token CDSE (¿usuario/pass correcto?)")
    print("✅ Token CDSE listo.")

    # --- Ventanas ------------------------------------------------------------
    (pre, post) = _ventanas_evento(fecha_evento, ventana_dias)
    ano_evento = int(fecha_evento[:4])
    print(f"📅 PRE:  {pre[0]} → {pre[1]}")
    print(f"📅 POST: {post[0]} → {post[1]}")

    # --- 1. Composites NDVI del evento --------------------------------------
    print("🛰️  Bajando NDVI PRE del evento...")
    r_pre = _fetch_ndvi_median(token, geom_mapping, pre[0], pre[1], resx=resx, resy=resy)
    print("🛰️  Bajando NDVI POST del evento...")
    r_post = _fetch_ndvi_median(token, geom_mapping, post[0], post[1], resx=resx, resy=resy)
    if r_pre is None or r_post is None:
        raise RuntimeError("No se pudo obtener alguno de los composites PRE/POST desde CDSE.")

    # Alinear formas: por resolución+geom idénticas suelen coincidir; si difieren
    # por 1 px (redondeo interno de SH) recortamos al mínimo común.
    h = min(r_pre.arr.shape[0], r_post.arr.shape[0])
    w = min(r_pre.arr.shape[1], r_post.arr.shape[1])
    ndvi_pre = r_pre.arr[:h, :w]
    ndvi_post = r_post.arr[:h, :w]
    print(f"   Grilla: {h}×{w} px  (~{h * w * (resx * 111_000) * (resy * 111_000) / 10_000:.1f} ha bruto)")

    # --- 2. Baseline histórico (misma ventana calendario) ------------------
    print(f"📊 Calculando baseline histórico {baseline_anos} años (misma ventana)...")
    baselines = []
    anos_usados = []
    for offset in range(1, baseline_anos + 1):
        ano = ano_evento - offset
        pre_b, post_b = _mismas_ventanas_en_ano(pre, post, ano)
        rp = _fetch_ndvi_median(token, geom_mapping, pre_b[0], pre_b[1], resx=resx, resy=resy)
        rq = _fetch_ndvi_median(token, geom_mapping, post_b[0], post_b[1], resx=resx, resy=resy)
        if rp is None or rq is None:
            print(f"   ⚠️  Baseline {ano} descartado (sin datos)")
            continue
        d = rq.arr[:h, :w] - rp.arr[:h, :w]
        baselines.append(d)
        anos_usados.append(ano)
        print(f"   ✅ Baseline {ano}")

    if baselines:
        baseline_stack = np.stack(baselines, axis=0)
        baseline_delta = np.nanmean(baseline_stack, axis=0)
    else:
        print("   ⚠️  Sin baselines válidos — uso baseline = 0 (equivale a ΔNDVI observado)")
        baseline_delta = np.zeros_like(ndvi_pre)

    # --- 3. ΔNDVI observado y ajustado --------------------------------------
    delta_obs = ndvi_post - ndvi_pre
    delta_adj = delta_obs - baseline_delta
    valid_mask = np.isfinite(delta_adj)

    # --- 4. Clasificación de severidad --------------------------------------
    severidad = _clasificar_severidad(delta_adj, umbrales)

    # --- 5. Estadísticas ----------------------------------------------------
    area_total_ha = _area_hectareas_lote(geom_shp)
    stats = _stats_por_categoria(severidad, valid_mask, area_total_ha)

    ndvi_pre_val = float(np.nanmean(ndvi_pre)) if np.isfinite(ndvi_pre).any() else float("nan")
    ndvi_post_val = float(np.nanmean(ndvi_post)) if np.isfinite(ndvi_post).any() else float("nan")
    delta_obs_val = ndvi_post_val - ndvi_pre_val
    delta_adj_val = float(np.nanmean(delta_adj)) if valid_mask.any() else float("nan")
    baseline_val = float(np.nanmean(baseline_delta)) if np.isfinite(baseline_delta).any() else 0.0

    # --- 6. Confianza -------------------------------------------------------
    n_pre = _catalog_scene_count(token, geom_mapping, pre[0], pre[1])
    n_post = _catalog_scene_count(token, geom_mapping, post[0], post[1])
    confianza, emoji_conf = _confianza(n_pre, n_post)

    # --- 6b. Verificación climatológica física (ERA5) -----------------------
    # El ΔNDVI dice que la vegetación cayó; ERA5 dice si las variables físicas
    # del clima corresponden al siniestro declarado. Va ANTES de la
    # interpretación para que esta pueda cruzarlas y evitar falsos positivos.
    print(f"🌡️  Verificando evento ({tipo_evento}) con reanálisis Open-Meteo Archive...")
    confirmacion_termica = verificar_evento_clima_era5(
        geom_shp.centroid.y, geom_shp.centroid.x, fecha_evento, tipo_evento,
        ventana_dias=min(ventana_dias, 5))
    if confirmacion_termica.get("disponible"):
        ct = confirmacion_termica
        print(f"   → {ct['clasificacion'].upper()} "
              f"({'CONFIRMADO' if ct['confirmada'] else 'NO CONFIRMADO'}): "
              f"{ct.get('detalles', '')}")
    else:
        print(f"   ⚠️  No verificable: {confirmacion_termica.get('error')}")

    # --- 6b-bis. Overshooting tops GOES-19 (forense, solo eventos convectivos)
    # Señal satelital INDEPENDIENTE del NDVI y de ERA5: si hubo una cúpula que
    # penetró la tropopausa esa noche, es la evidencia más directa de
    # convección severa (granizo grande). Sólo aplica a granizo/viento —para
    # helada/sequía/inundación no tiene sentido físico buscar overshooting.
    overshooting_goes = None
    if tipo_evento in ("granizo", "viento") and overshooting_historico is not None:
        print("🧊 Escaneando overshooting tops GOES-19 (noche del evento)...")
        arg_tz = timezone(timedelta(hours=-3))
        ev_dt = datetime.combine(date.fromisoformat(fecha_evento), datetime.min.time())
        # Ventana: 20:00 del día anterior -> 06:00 del día del evento (cubre
        # "noche de + madrugada de", el patrón típico de estas tormentas).
        #
        # OJO — convención de `fecha_evento` para tormentas nocturnas: a
        # diferencia de la verificación ERA5 (que tolera ±ventana_dias/5 días
        # de margen), esta ventana es FIJA y NO perdona un día de diferencia.
        # Si la tormenta fue "la noche del 18 al 19", `fecha_evento` debe ser
        # "19" (el día DESPUÉS de la noche de tormenta) — con "18" esta
        # ventana escanea la noche del 17 al 18 (la noche ANTERIOR, sin
        # tormenta) y da un falso "sin overshooting/sin rayos". Verificado en
        # el caso real Tortugas 2026-02-18/19: declarar "18" da ctt_min=+6.4°C
        # y 0 rayos (cielo despejado, noche equivocada); declarar "19" da
        # -92.3°C y 11.742 rayos (la noche real del temporal).
        inicio_local = (ev_dt - timedelta(days=1)).replace(hour=20, tzinfo=arg_tz)
        fin_local = ev_dt.replace(hour=6, tzinfo=arg_tz)
        try:
            overshooting_goes = overshooting_historico(
                geom_shp.centroid.y, geom_shp.centroid.x,
                inicio_local.astimezone(timezone.utc), fin_local.astimezone(timezone.utc),
                radio_km=40, paso_min=30)
            if overshooting_goes.get("disponible") and overshooting_goes.get("overshooting"):
                print(f"   → OVERSHOOTING TOP detectado: {overshooting_goes['ctt_min_c']}°C "
                      f"({overshooting_goes['delta_k']}K bajo el yunque) a las "
                      f"{overshooting_goes['hora_pico']}")
            elif overshooting_goes.get("disponible"):
                print(f"   → sin firma de overshooting (nivel: {overshooting_goes['nivel']})")
            else:
                print(f"   ⚠️  No verificable: {overshooting_goes.get('error')}")
        except Exception as exc:  # noqa: BLE001 — nunca debe tumbar el peritaje
            overshooting_goes = {"disponible": False, "fuente": "goes19-abi-acht",
                                 "error": str(exc)[:300]}
            print(f"   ⚠️  Error escaneando GOES-19: {exc}")

    # --- 6b-ter. Rayos GOES-19 GLM (forense, cuarta fuente independiente) --
    # Complementa a overshooting: GLM mide DIRECTAMENTE la actividad eléctrica
    # (rayos) — es el dato instrumental más directo de convección severa. La
    # combinación NDVI+ERA5+ABI+GLM da 4 fuentes independientes convergiendo.
    # Reusa la misma ventana horaria que overshooting (misma convención de
    # `fecha_evento` = día DESPUÉS de la noche de tormenta; ver el comentario
    # extenso en el bloque de overshooting más arriba).
    rayos_goes = None
    if tipo_evento in ("granizo", "viento") and rayos_historicos is not None:
        print("⚡ Escaneando rayos GOES-19 GLM (noche del evento)...")
        arg_tz = timezone(timedelta(hours=-3))
        ev_dt = datetime.combine(date.fromisoformat(fecha_evento), datetime.min.time())
        inicio_local = (ev_dt - timedelta(days=1)).replace(hour=20, tzinfo=arg_tz)
        fin_local = ev_dt.replace(hour=6, tzinfo=arg_tz)
        try:
            rayos_goes = rayos_historicos(
                geom_shp.centroid.y, geom_shp.centroid.x,
                inicio_local.astimezone(timezone.utc), fin_local.astimezone(timezone.utc),
                radio_km=40)
            if rayos_goes.get("disponible"):
                print(f"   → {rayos_goes['descargas_totales']} rayos totales "
                      f"(pico {rayos_goes['descargas_pico_hora']}/h en {rayos_goes['hora_pico']}), "
                      f"nivel {rayos_goes['nivel']}")
            else:
                print(f"   ⚠️  No verificable: {rayos_goes.get('error')}")
        except Exception as exc:  # noqa: BLE001
            rayos_goes = {"disponible": False, "fuente": "goes19-glm",
                         "error": str(exc)[:300]}
            print(f"   ⚠️  Error escaneando GLM: {exc}")

    # --- 6c. Interpretación agronómica cruzada ------------------------------
    interp = _interpretacion(delta_adj_val, umbrales, stats, area_total_ha,
                              n_post, confirmacion_termica, tipo_evento,
                              overshooting_goes, rayos_goes)

    # --- 6d. Advertencias de sanidad del análisis ---------------------------
    warnings_analisis: list[str] = []
    w_ndvi = _warning_ndvi_base(ndvi_pre_val, cultivo, fecha_evento)
    if w_ndvi:
        warnings_analisis.append(w_ndvi)
        print(f"⚠️  {w_ndvi}")

    # --- Contexto para salidas ----------------------------------------------
    pct_leve = stats["leve"] / area_total_ha * 100 if area_total_ha else 0
    pct_mod = stats["moderada"] / area_total_ha * 100 if area_total_ha else 0
    pct_sev = stats["severa"] / area_total_ha * 100 if area_total_ha else 0
    pct_afect = stats["afectada"] / area_total_ha * 100 if area_total_ha else 0
    pct_sin = stats["sin_dano"] / area_total_ha * 100 if area_total_ha else 0

    resultado: dict[str, Any] = {
        "nombre_caso": nombre_caso,
        "tipo_evento": tipo_evento,
        "fecha_evento": fecha_evento,
        "ventanas": {"pre": pre, "post": post, "baseline_anos": anos_usados},
        "area_total_ha": round(area_total_ha, 2),
        "ndvi_pre": round(ndvi_pre_val, 4),
        "ndvi_post": round(ndvi_post_val, 4),
        "delta_obs": round(delta_obs_val, 4),
        "baseline": round(baseline_val, 4),
        "delta_adj": round(delta_adj_val, 4),
        "areas_ha": {
            "leve": round(stats["leve"], 2),
            "moderada": round(stats["moderada"], 2),
            "severa": round(stats["severa"], 2),
            "afectada": round(stats["afectada"], 2),
            "sin_dano": round(stats["sin_dano"], 2),
        },
        "pct": {
            "leve": round(pct_leve, 2),
            "moderada": round(pct_mod, 2),
            "severa": round(pct_sev, 2),
            "afectada": round(pct_afect, 2),
            "sin_dano": round(pct_sin, 2),
        },
        "confianza": confianza,
        "n_escenas": {"pre": n_pre, "post": n_post},
        "confirmacion_termica": confirmacion_termica,
        "overshooting_goes": overshooting_goes,
        "rayos_goes": rayos_goes,
        "interpretacion": interp,
        "warnings": warnings_analisis,
        "cultivo_declarado": cultivo,
        "metadata": metadata or {},
        "umbrales": {k: v for k, v in umbrales.items() if isinstance(v, (int, float))},
        "generado": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        # Bounds del raster en formato Leaflet [[south, west], [north, east]]
        # (mismo formato que ya usa la zonificación KMeans) para overlay directo
        # de severidad.png sobre el mapa, sin recalcular nada en el frontend.
        "bounds": [[r_post.bounds[1], r_post.bounds[0]],
                   [r_post.bounds[3], r_post.bounds[2]]],
        "outputs": {},
    }

    # --- 7. Reporte imprimible en consola -----------------------------------
    _imprimir_reporte(resultado)

    # --- 8. Salidas a disco (opcional) --------------------------------------
    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        outputs: dict[str, str] = {}

        # 8.1 métricas CSV
        p_csv = out / "metricas.csv"
        pd.DataFrame({
            "Metrica": [
                "Nombre_caso", "Tipo_evento", "Fecha_evento",
                "Area_total_ha", "NDVI_PRE", "NDVI_POST",
                "Delta_observado", "Baseline", "Delta_ajustado",
                "Area_leve_ha", "Area_moderada_ha", "Area_severa_ha",
                "Area_afectada_ha", "Pct_afectado",
                "Confianza", "N_escenas_PRE", "N_escenas_POST",
                "Baseline_anos_usados",
            ],
            "Valor": [
                nombre_caso, tipo_evento, fecha_evento,
                area_total_ha, ndvi_pre_val, ndvi_post_val,
                delta_obs_val, baseline_val, delta_adj_val,
                stats["leve"], stats["moderada"], stats["severa"],
                stats["afectada"], pct_afect,
                confianza, n_pre, n_post,
                ",".join(str(a) for a in anos_usados),
            ],
        }).to_csv(p_csv, index=False)
        outputs["metricas_csv"] = str(p_csv)
        print(f"  ✅ {p_csv.name}")

        # 8.2 comparativa PNG
        p_comp = out / "comparativa.png"
        if _generar_png_comparativa(ndvi_pre, ndvi_post, severidad,
                                    nombre_caso, fecha_evento, tipo_evento,
                                    (pre, post), p_comp):
            outputs["comparativa_png"] = str(p_comp)
            print(f"  ✅ {p_comp.name}")

        # 8.3 severidad PNG puro (para overlay Folium)
        p_sev = out / "severidad.png"
        try:
            from PIL import Image
            Image.fromarray(_colorize_severidad(severidad), mode="RGBA").save(p_sev)
            outputs["severidad_png"] = str(p_sev)
            print(f"  ✅ {p_sev.name}")
        except ImportError:
            pass

        # 8.4 NDVI post PNG (para el visor Folium)
        p_ndvi_post = out / "ndvi_post.png"
        if _guardar_ndvi_png(ndvi_post, p_ndvi_post):
            outputs["ndvi_post_png"] = str(p_ndvi_post)

        # 8.5 barras
        p_bar = out / "distribucion.png"
        if _generar_png_barras(stats, area_total_ha, nombre_caso, tipo_evento, p_bar):
            outputs["distribucion_png"] = str(p_bar)
            print(f"  ✅ {p_bar.name}")

        # 8.6 puntos peritaje (CSV + KML)
        df_pts = _puntos_muestreo(severidad, valid_mask, r_post.transform,
                                   n_por_categoria=n_muestreo_por_categoria)
        p_pts_csv = out / "peritaje.csv"
        df_pts.to_csv(p_pts_csv, index=False)
        outputs["peritaje_csv"] = str(p_pts_csv)
        print(f"  ✅ {p_pts_csv.name}  ({len(df_pts)} puntos)")

        p_kml = out / "peritaje.kml"
        if _generar_kml_peritaje(df_pts, nombre_caso, tipo_evento, fecha_evento, p_kml):
            outputs["peritaje_kml"] = str(p_kml)
            print(f"  ✅ {p_kml.name}")

        # 8.7 reporte HTML
        p_html = out / "reporte.html"
        anos_txt = (", ".join(str(a) for a in anos_usados)
                    if anos_usados else "sin baseline")
        ctx_html = {
            "nombre_caso": nombre_caso, "tipo_evento": tipo_evento,
            "fecha_evento": fecha_evento,
            "pre_ini": pre[0], "pre_fin": pre[1],
            "post_ini": post[0], "post_fin": post[1],
            "area_total_ha": area_total_ha,
            "area_afectada": stats["afectada"], "pct_afectado": pct_afect,
            "ndvi_pre": ndvi_pre_val, "ndvi_post": ndvi_post_val,
            "delta_obs": delta_obs_val, "baseline": baseline_val,
            "delta_adj": delta_adj_val,
            "area_leve": stats["leve"], "area_moderada": stats["moderada"],
            "area_severa": stats["severa"], "sin_dano": stats["sin_dano"],
            "pct_leve": pct_leve, "pct_mod": pct_mod,
            "pct_sev": pct_sev, "pct_sin": pct_sin,
            "confianza": confianza, "emoji_conf": emoji_conf,
            "n_pre": n_pre, "n_post": n_post,
            "n_baseline": len(anos_usados), "anos_baseline_txt": anos_txt,
            "interpretacion": interp,
            "recomendacion_severo": umbrales["recomendacion_severo"],
            "badge_color": umbrales["badge_color"],
            "generado": resultado["generado"], "resx": resx,
            "confirmacion_termica": confirmacion_termica,
            "overshooting_goes": overshooting_goes,
            "rayos_goes": rayos_goes,
            "metadata": metadata or {},
        }
        if _generar_html_reporte(ctx_html, p_html):
            outputs["reporte_html"] = str(p_html)
            print(f"  ✅ {p_html.name}")

        # 8.7-bis reporte PDF cerrado (documento oficial para el expediente)
        p_pdf = out / "reporte.pdf"
        sev_png = outputs.get("severidad_png")
        comp_png = outputs.get("comparativa_png")
        if _generar_pdf_reporte(ctx_html, p_pdf,
                                severidad_png=Path(sev_png) if sev_png else None,
                                comparativa_png=Path(comp_png) if comp_png else None):
            outputs["reporte_pdf"] = str(p_pdf)
            print(f"  ✅ {p_pdf.name}")

        # 8.8 visor Folium
        p_visor = out / "visor_campo.html"
        centro = (geom_shp.centroid.y, geom_shp.centroid.x)
        if _generar_visor_folium(centro, df_pts,
                                  outputs.get("ndvi_post_png") and Path(outputs["ndvi_post_png"]),
                                  r_post.bounds, nombre_caso, tipo_evento, p_visor):
            outputs["visor_html"] = str(p_visor)
            print(f"  ✅ {p_visor.name}")

        resultado["outputs"] = outputs

    return resultado


def _imprimir_reporte(r: dict) -> None:
    """Imprime en consola el mismo layout del notebook v2.0."""
    sep = "─" * 70
    u = r["umbrales"]
    print(f"\n{'=' * 70}")
    print(f"            🌾 INFORME AgroIA EVENTUALIDADES — {r['nombre_caso'].upper()} 🌾")
    print(f"{'=' * 70}")
    print(f"\n📍 Caso:           {r['nombre_caso']}")
    print(f"🌩️  Evento:         {r['tipo_evento'].capitalize()} — {r['fecha_evento']}")
    print(f"🛰️  Fuente:         Sentinel-2 L2A (CDSE Process API)")
    print(f"\n{sep}\n📊 MÉTRICAS DE VEGETACIÓN\n{sep}")
    print(f"  NDVI pre-evento:          {r['ndvi_pre']:.4f}")
    print(f"  NDVI post-evento:         {r['ndvi_post']:.4f}")
    print(f"  ΔNDVI observado:          {r['delta_obs']:+.4f}")
    print(f"  Baseline histórico:       {r['baseline']:+.4f}")
    print(f"  ΔNDVI ajustado:           {r['delta_adj']:+.4f}  ← indicador de daño real")
    print(f"\n{sep}\n💥 SUPERFICIE AFECTADA ({r['tipo_evento'].upper()})\n{sep}")
    a = r["areas_ha"]; p = r["pct"]
    print(f"  Área total del lote:          {r['area_total_ha']:.1f} ha")
    print(f"  🟡 Daño LEVE     (>{abs(u['leve']):.0%} ΔNDVI): {a['leve']:.1f} ha ({p['leve']:.1f}%)")
    print(f"  🟠 Daño MODERADO (>{abs(u['moderado']):.0%} ΔNDVI): {a['moderada']:.1f} ha ({p['moderada']:.1f}%)")
    print(f"  🔴 Daño SEVERO   (>{abs(u['severo']):.0%} ΔNDVI): {a['severa']:.1f} ha ({p['severa']:.1f}%)")
    print(f"{sep}")
    print(f"  TOTAL AFECTADO:               {a['afectada']:.1f} ha ({p['afectada']:.1f}%)")
    ct = r.get("confirmacion_termica")
    if ct is not None:
        titulo_verif = "CONFIRMACIÓN TÉRMICA (ERA5-Land)" if r.get("tipo_evento") == "helada" else "VERIFICACIÓN FÍSICA CLIMATOLÓGICA (ERA5-Land/Open-Meteo)"
        print(f"\n{sep}\n📊  {titulo_verif}\n{sep}")
        if ct.get("disponible"):
            marca = "✅" if ct["confirmada"] else "⚪"
            if r.get("tipo_evento") == "helada" and "t_min_c" in ct:
                print(f"  {marca} {ct['clasificacion'].capitalize()}: mínima nocturna "
                      f"{ct['t_min_c']} °C el {ct['noche_mas_fria']}"
                      f"{'  (coincide con el evento)' if ct['coincide_con_evento'] else '  (⚠️ otra fecha)'}")
                print(f"     Umbrales: meteo <{ct.get('umbral_meteo_c', 0.0):.0f} °C · "
                      f"agro <{ct.get('umbral_agro_c', 2.0):.0f} °C")
            else:
                print(f"  {marca} {ct['clasificacion'].capitalize()}")
                print(f"     Detalles: {ct.get('detalles', '')}")
        else:
            print(f"  ⚠️  No verificable: {ct.get('error')}")
    print(f"\n{sep}\n🔎 INTERPRETACIÓN AGRONÓMICA\n{sep}")
    print(f"  {r['interpretacion']}")
    print(f"\n  Confianza: {r['confianza']}  ({r['n_escenas']['pre']} esc PRE / "
          f"{r['n_escenas']['post']} esc POST)")
    print(f"{'=' * 70}\n")


# =============================================================================
# CLI
# =============================================================================
def _cli() -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="Peritaje satelital de eventualidades (CDSE, sin GEE).")
    p.add_argument("--geojson", required=True,
                   help="Ruta al GeoJSON del lote (Feature / geometry / FeatureCollection).")
    p.add_argument("--fecha", required=True,
                   help="Fecha del evento, YYYY-MM-DD.")
    p.add_argument("--evento", default="granizo",
                   choices=list(UMBRALES_POR_EVENTO), help="Tipo de evento.")
    p.add_argument("--nombre", default=None, help="Nombre del caso (para reportes).")
    p.add_argument("--ventana", type=int, default=14,
                   help="Días antes/después del evento (default: 14).")
    p.add_argument("--baseline", type=int, default=3,
                   help="Años de baseline histórico (default: 3).")
    p.add_argument("--resx", type=float, default=0.0001,
                   help="Resolución x en grados (default: 0.0001 ≈ 10 m).")
    p.add_argument("--muestras", type=int, default=5,
                   help="Puntos de peritaje por categoría (default: 5).")
    p.add_argument("--out", default=None,
                   help="Carpeta de salida (si se omite, sólo imprime métricas).")
    args = p.parse_args()

    with open(args.geojson, "r", encoding="utf-8") as f:
        geom_in = json.load(f)

    peritar_evento(
        geom=geom_in,
        fecha_evento=args.fecha,
        tipo_evento=args.evento,
        nombre_caso=args.nombre,
        ventana_dias=args.ventana,
        baseline_anos=args.baseline,
        resx=args.resx, resy=args.resx,
        output_dir=args.out,
        n_muestreo_por_categoria=args.muestras,
    )


if __name__ == "__main__":
    _cli()
