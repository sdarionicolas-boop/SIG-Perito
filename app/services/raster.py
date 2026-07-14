"""Helpers de raster: clip a un polígono y coloreado a PNG para overlays Leaflet."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.mask import mask as rio_mask
from rasterio.transform import array_bounds
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform


@dataclass(slots=True)
class ClipResult:
    """Resultado de clipear un raster a un polígono."""

    band: np.ma.MaskedArray  # banda clipeada; band.mask=True => fuera del polígono
    nodata: float | int | None
    bounds_4326: tuple[float, float, float, float]  # (west, south, east, north)


def clip_raster_to_polygon(tif_path: str | Path, polygon_4326: BaseGeometry) -> ClipResult:
    """Clipea la primera banda de un raster al polígono (dado en EPSG:4326).

    Reproyecta el polígono al CRS del raster si hace falta. Devuelve la banda como
    MaskedArray (los píxeles fuera del polígono quedan enmascarados) y los bounds
    del recorte reproyectados a EPSG:4326 para el overlay en Leaflet.
    """
    with rasterio.open(tif_path) as src:
        raster_epsg = src.crs.to_epsg() if src.crs else None
        if raster_epsg is not None and raster_epsg != 4326:
            to_raster = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            geom = shapely_transform(to_raster.transform, polygon_4326)
        else:
            geom = polygon_4326

        out, out_transform = rio_mask(src, [mapping(geom)], crop=True, filled=False)
        band = out[0]  # np.ma.MaskedArray
        nodata = src.nodata

        height, width = band.shape
        left, bottom, right, top = array_bounds(height, width, out_transform)

        # Reproyectar bounds a 4326 si el raster estaba en otro CRS.
        if raster_epsg is not None and raster_epsg != 4326:
            to_4326 = Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True)
            west, south = to_4326.transform(left, bottom)
            east, north = to_4326.transform(right, top)
        else:
            west, south, east, north = left, bottom, right, top

    return ClipResult(band=band, nodata=nodata, bounds_4326=(west, south, east, north))
