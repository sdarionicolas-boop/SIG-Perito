"""Núcleo compartido de detección de cosecha escalonada (Sentinel-1 RVI).

Reemplaza el umbral absoluto (RVI < 0.60) por una métrica defendible:

1. Baseline por píxel: cada píxel se compara contra SU PROPIO pico de vigor
   (percentil alto de la fase vegetativa), no contra un valor global. Esto
   elimina los falsos positivos de suelo desnudo pre-siembra.
2. Ventana de cosecha: no se reporta avance antes del pico de vigor del lote
   (fecha en que el RVI medio del campo es máximo).
3. Monotonía acumulativa: una vez que un píxel se marca cosechado, queda
   cosechado. El avance no puede retroceder (running-max en el tiempo).

Tanto analizar_raster_cosecha.py como generar_mascaras_qgis.py usan este
módulo, de modo que el CSV de progreso y las máscaras binarias siempre
coinciden.
"""
import warnings
import numpy as np
import rasterio
from rasterio.features import geometry_mask


def cargar_stack(records):
    """Apila los RVI GeoTIFFs (misma grilla) en un array (N, H, W).

    records: lista de (fecha_str, filepath) ordenada por fecha.
    Devuelve: (fechas, stack float32, ref_transform, ref_meta).
    """
    fechas = [d for d, _ in records]
    with rasterio.open(records[0][1]) as src0:
        ref_transform = src0.transform
        ref_meta = src0.meta.copy()
        h, w = src0.height, src0.width

    stack = np.full((len(records), h, w), np.nan, dtype="float32")
    for k, (_, fp) in enumerate(records):
        with rasterio.open(fp) as src:
            stack[k] = src.read(1).astype("float32")
    return fechas, stack, ref_transform, ref_meta


def mascara_poligono(geom, transform, shape):
    """Máscara booleana (True = dentro del polígono) sobre la grilla de ref."""
    return geometry_mask([geom], out_shape=shape, transform=transform, invert=True)


def detectar_cosecha(stack, poly_mask, frac=0.55, vigor_pct=90, min_vigor=0.20):
    """Detecta el avance de cosecha acumulado por píxel.

    frac       : un píxel está cosechado cuando su RVI cae por debajo de
                 frac * (su pico de vigor). Ej: 0.55 = cayó bajo el 55% de
                 su propio máximo.
    vigor_pct  : percentil usado como pico de vigor por píxel (robusto a
                 speckle SAR frente al máximo crudo).
    min_vigor  : pico mínimo para considerar que el píxel tuvo vegetación
                 (descarta caminos/agua/suelo permanente).

    Devuelve dict con:
      t_peak         : índice de fecha del pico de vigor del lote
      peak           : (H, W) pico de vigor por píxel
      valid_pixel    : (H, W) píxeles válidos (dentro del polígono y con vigor)
      harvested_cum  : (N, H, W) bool, cosechado acumulado (monótono)
      field_mean     : (N,) RVI medio del lote por fecha
    """
    n, h, w = stack.shape

    # Solo valores válidos (dentro de rango físico, sin nodata/ceros de fondo).
    s = np.where(np.isfinite(stack) & (stack > 0), stack, np.nan)

    # RVI medio del lote por fecha -> el pico define el inicio de la cosecha.
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)  # slices all-NaN (fuera del lote)
        campo = np.where(poly_mask[None, :, :], s, np.nan)
        field_mean = np.nanmean(campo.reshape(n, -1), axis=1)
        t_peak = int(np.nanargmax(field_mean))

        # Pico de vigor POR PÍXEL sobre la fase de crecimiento [0 .. t_peak].
        hi = max(t_peak + 1, 3)
        peak = np.nanpercentile(s[:hi], vigor_pct, axis=0)

    valid_pixel = poly_mask & np.isfinite(peak) & (peak > min_vigor)
    thr = frac * peak

    # Cosechado instantáneo: solo desde el pico de vigor en adelante.
    harvested_inst = np.zeros((n, h, w), dtype=bool)
    for t in range(t_peak, n):
        cur = s[t]
        harvested_inst[t] = valid_pixel & np.isfinite(cur) & (cur < thr)

    # Monotonía: una vez cosechado, siempre cosechado.
    harvested_cum = np.logical_or.accumulate(harvested_inst, axis=0)

    return {
        "t_peak": t_peak,
        "peak": peak,
        "valid_pixel": valid_pixel,
        "harvested_cum": harvested_cum,
        "field_mean": field_mean,
    }
