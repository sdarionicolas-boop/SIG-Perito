"""SoilGrids 2.0 (ISRIC): carbono orgánico del suelo (SOC) para el centroide del proyecto.

Consulta la REST de propiedades para `soc` en las profundidades 0-5, 5-15 y 15-30 cm
y convierte la concentración a stock (t C/ha) con una densidad aparente supuesta.

Si están disponibles los archivos raster locales del Mapa de Carbono Orgánico del Suelo de Argentina
(INTA, Gaitán et al.) en `data/soc/`, realiza estadística zonal local (0-30 cm) y
omite la consulta externa.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx
import numpy as np
from shapely.geometry.base import BaseGeometry

from app.config import DATA_DIR, SOILGRIDS_URL, SOC_LOCAL_DIR
from app.services.raster import clip_raster_to_polygon

# SOC precalculado por lote (extractor/generar_soc_lotes.py). Permite que la app y HF
# sirvan el dato real del INTA al instante, sin los rásters de 600 MB ni SoilGrids.
SOC_CSV = DATA_DIR / "soc_lotes.csv"

# Supuesto de densidad aparente (g/cm³). Se reporta como assumption en la respuesta de SoilGrids.
BULK_DENSITY_G_CM3 = 1.3

_DEPTHS = ("0-5cm", "5-15cm", "15-30cm")
_VALUES = ("mean", "Q0.05", "Q0.95")


class SoilGridsError(RuntimeError):
    """Error consultando o parseando la respuesta de SoilGrids o leyendo rasters locales."""


@dataclass(slots=True)
class SOCDepthStock:
    """Stock de SOC de una capa, en t C/ha (mean + banda de incertidumbre)."""

    depth: str
    mean: float
    uncertainty_low: float
    uncertainty_high: float
    unit: str = "t C/ha"


@dataclass
class SOCAnalysisResult:
    """Contenedor del resultado de SOC con metadatos sobre la fuente utilizada."""

    stocks: list[SOCDepthStock]
    source: str
    bulk_density_used: float


def _stock_t_c_ha(soc_g_per_kg: float, thickness_cm: float, bulk_density: float) -> float:
    """Convierte concentración de SOC (g/kg) a stock (t C/ha) para un espesor dado."""
    return soc_g_per_kg * bulk_density * thickness_cm * 0.1


def query_soc_stocks(
    lon: float, lat: float, bulk_density: float = BULK_DENSITY_G_CM3
) -> list[SOCDepthStock]:
    """Consulta SoilGrids para (lon, lat) y devuelve el stock de SOC por profundidad."""
    url = f"{SOILGRIDS_URL}/properties/query"
    params: list[tuple[str, str | float]] = [
        ("lon", lon),
        ("lat", lat),
        ("property", "soc"),
    ]
    params += [("depth", d) for d in _DEPTHS]
    params += [("value", v) for v in _VALUES]

    try:
        resp = httpx.get(url, params=params, timeout=90.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise SoilGridsError(f"Falló la consulta a SoilGrids ({url}): {exc}") from exc

    try:
        layer = resp.json()["properties"]["layers"][0]
        d_factor = layer["unit_measure"].get("d_factor", 10) or 10
        depths = layer["depths"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SoilGridsError(f"Respuesta de SoilGrids inesperada: {exc}") from exc

    stocks: list[SOCDepthStock] = []
    for dep in depths:
        rng = dep.get("range", {})
        thickness = float(rng.get("bottom_depth", 0) - rng.get("top_depth", 0))
        values = dep.get("values", {})
        mean = values.get("mean")
        q05 = values.get("Q0.05")
        q95 = values.get("Q0.95")
        if mean is None or thickness <= 0:
            raise SoilGridsError(
                f"SoilGrids no tiene datos de SOC en {dep.get('label')} para ese punto."
            )

        # dg/kg -> g/kg (÷ d_factor), luego a stock t C/ha.
        def stock(dg_kg: float | None) -> float | None:
            if dg_kg is None:
                return None
            return round(_stock_t_c_ha(dg_kg / d_factor, thickness, bulk_density), 2)

        mean_stock = stock(mean)
        low = stock(q05)
        high = stock(q95)
        stocks.append(
            SOCDepthStock(
                depth=dep.get("label", f"{rng.get('top_depth')}-{rng.get('bottom_depth')}cm"),
                mean=mean_stock,  # type: ignore[arg-type]
                uncertainty_low=low if low is not None else mean_stock,  # type: ignore[arg-type]
                uncertainty_high=high if high is not None else mean_stock,  # type: ignore[arg-type]
            )
        )

    return stocks


def _zonal_stats_for_raster(tif_path: Path | str, polygon_geom: BaseGeometry) -> float | None:
    """Calcula el promedio zonal para la primera banda de un raster local recortado por el polígono."""
    if not Path(tif_path).exists():
        return None
    try:
        clip_res = clip_raster_to_polygon(tif_path, polygon_geom)
        band = clip_res.band
        valid_pixels = band.compressed()  # Filtra los píxeles fuera del polígono
        if clip_res.nodata is not None:
            valid_pixels = valid_pixels[valid_pixels != clip_res.nodata]
        # Quitar posibles NaN en rasters float
        valid_pixels = valid_pixels[~np.isnan(valid_pixels)]
        if len(valid_pixels) > 0:
            return float(np.mean(valid_pixels))
        return None
    except Exception:
        # Degradación silenciosa: si falla el recorte local, devolvemos None para usar SoilGrids
        return None


@lru_cache(maxsize=1)
def _soc_precalculado() -> dict[str, SOCAnalysisResult]:
    """Carga data/soc_lotes.csv -> {nombre: SOCAnalysisResult} (stock 0-30 cm).

    Indexado por nombre (no por lote_id): el id de un lote depende del orden de
    siembra de la BD y difiere entre el entorno local y un HF recién sembrado
    (los 3 demos nacen ahí con ids 1/2/3, no 124/125/126). El nombre es estable.
    """
    data: dict[str, SOCAnalysisResult] = {}
    if not SOC_CSV.exists():
        return data
    with open(SOC_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            data[r["nombre"]] = SOCAnalysisResult(
                stocks=[SOCDepthStock(
                    depth=r.get("depth", "0-30cm"),
                    mean=float(r["soc_mean"]),
                    uncertainty_low=float(r["soc_low"]),
                    uncertainty_high=float(r["soc_high"]),
                )],
                source=r["source"],
                bulk_density_used=float(r.get("bulk_density_used", 0.0)),
            )
    return data


def soc_de_lote(nombre: str) -> SOCAnalysisResult | None:
    """Devuelve el SOC precalculado del lote (por nombre) si está en el CSV; None si no."""
    return _soc_precalculado().get(nombre)


def analyze_soc_for_geom_or_coords(
    lon: float, lat: float, polygon_geom: BaseGeometry | None = None
) -> SOCAnalysisResult:
    """Intenta realizar estadística zonal de carbono usando los rasters locales de Argentina (INTA).

    Si los rasters no están disponibles o el polígono queda fuera de sus límites,
    degrada automáticamente a la API global de SoilGrids 2.0.
    """
    if polygon_geom is not None:
        local_dir = Path(SOC_LOCAL_DIR)
        r_mean = local_dir / "Mapa_COS_Nacional_Argentina_media.tif"
        r_p5 = local_dir / "Mapa_COS_Nacional_Argentina_percentil_5.tif"
        r_p95 = local_dir / "Mapa_COS_Nacional_Argentina_percentil_95.tif"

        # Verificar si existen los 3 archivos raster locales
        if r_mean.exists() and r_p5.exists() and r_p95.exists():
            mean_val = _zonal_stats_for_raster(r_mean, polygon_geom)
            p5_val = _zonal_stats_for_raster(r_p5, polygon_geom)
            p95_val = _zonal_stats_for_raster(r_p95, polygon_geom)

            if mean_val is not None:
                low_val = p5_val if p5_val is not None else mean_val
                high_val = p95_val if p95_val is not None else mean_val

                stocks = [
                    SOCDepthStock(
                        depth="0-30cm",
                        mean=round(mean_val, 2),
                        uncertainty_low=round(low_val, 2),
                        uncertainty_high=round(high_val, 2),
                    )
                ]
                return SOCAnalysisResult(
                    stocks=stocks,
                    source="Mapa de Carbono Orgánico del Suelo de Argentina — INTA (Gaitán et al.)",
                    bulk_density_used=0.0,
                )

    # Fallback a SoilGrids
    stocks_global = query_soc_stocks(lon, lat)
    return SOCAnalysisResult(
        stocks=stocks_global,
        source="SoilGrids 2.0 (ISRIC) — WCS REST API",
        bulk_density_used=BULK_DENSITY_G_CM3,
    )
