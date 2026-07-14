# app/services/wrf_smn.py
"""Lector del WRF-DET 4 km del SMN (AWS Open Data, bucket público `smn-ar-wrf`).

Fuente OFICIAL del Servicio Meteorológico Nacional, resolución 4 km (6× mejor que
GFS/GEFS ~25 km) para precipitación, viento y temperatura sobre Argentina y
países limítrofes. Sin cuenta AWS ni convenio: el bucket es de acceso anónimo.

Complementa —no reemplaza— al ensemble GEFS de `alertas_clima.py`: GEFS aporta
CAPE y PROBABILIDAD (para granizo); este módulo aporta precipitación y ráfagas de
mucha mayor resolución espacial, con el peso legal de ser dato oficial del SMN.

Hechos verificados contra el bucket (2026-07):
  * Clave:   DATA/WRF/DET/{AAAA}/{MM}/{DD}/{CC}/WRFDETAR_01H_{AAAAMMDD}_{CC}_{LLL}.nc
             CC = ciclo UTC (00 o 12);  LLL = hora de pronóstico 000..072
  * Formato: netCDF (CF-1.8), grilla Lambert 4 km, y=1249, x=999
  * Variables: PP (mm, YA horaria — no acumulada desde el inicio),
               T2 (°C), HR2 (%), magViento10 (m/s), dirViento10 (°),
               lat/lon (grillas 2D)
  * Ciclos:  00 y 12 UTC; horizonte 72 h; publicado ~3–4 h tras la inicialización.

Nota de eficiencia: la grilla lat/lon es idéntica en todos los archivos. Para un
batch sobre muchos lotes conviene descargar cada archivo UNA vez y muestrear el
índice de cada lote sobre la grilla cacheada (todos comparten grilla).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests

from app.config import BASE_DIR

try:
    import netCDF4
    import numpy as np
except ImportError:  # entorno sin libs científicas -> degradación explícita
    netCDF4 = None
    np = None

BASE_URL = "https://smn-ar-wrf.s3.us-west-2.amazonaws.com"
PREFIX = "DATA/WRF/DET"
MAX_LEAD = 72
PUBLICACION_HORAS = 4          # margen de disponibilidad tras la hora de ciclo
CACHE_DIR = BASE_DIR / "cache" / "wrf"

# Conversión de unidades del modelo a las del resto del sistema
MS_A_KMH = 3.6


def _url(ciclo: datetime, lead: int) -> str:
    return (
        f"{BASE_URL}/{PREFIX}/{ciclo:%Y/%m/%d}/{ciclo:%H}/"
        f"WRFDETAR_01H_{ciclo:%Y%m%d}_{ciclo:%H}_{lead:03d}.nc"
    )


def _ciclos_candidatos(ahora: datetime | None = None) -> list[datetime]:
    """Ciclos 00/12 UTC recientes, del más nuevo al más viejo."""
    ahora = ahora or datetime.now(timezone.utc)
    disp = (ahora - timedelta(hours=PUBLICACION_HORAS)).replace(
        minute=0, second=0, microsecond=0)
    hora_ciclo = 12 if disp.hour >= 12 else 0
    c = disp.replace(hour=hora_ciclo)
    return [c, c - timedelta(hours=12), c - timedelta(hours=24)]


def ciclo_disponible(ahora: datetime | None = None) -> datetime | None:
    """Devuelve el ciclo publicado más reciente (probando el lead 000 con HEAD)."""
    for c in _ciclos_candidatos(ahora):
        try:
            if requests.head(_url(c, 0), timeout=10).ok:
                return c
        except requests.RequestException:
            continue
    return None


def _descargar(url: str, destino: str) -> None:
    r = requests.get(url, timeout=(5, 60), stream=True)
    r.raise_for_status()
    tmp = destino + ".part"
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)
    os.replace(tmp, destino)   # escritura atómica -> nunca deja archivos corruptos


def _abrir_dataset(ciclo: datetime, lead: int):
    """Descarga (si falta) el netCDF del lead y lo abre DESDE MEMORIA.

    Se abre vía `memory=` en vez de por ruta porque la librería HDF5/C no maneja
    bien rutas con caracteres no-ASCII en Windows (p. ej. 'Agrícola'), y de paso
    evita dejar handles/archivos a medio abrir. El disco se usa sólo como caché.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    url = _url(ciclo, lead)
    local = os.path.join(CACHE_DIR, os.path.basename(url))
    if not os.path.exists(local):
        _descargar(url, local)
    with open(local, "rb") as f:
        datos = f.read()
    return netCDF4.Dataset(os.path.basename(local), memory=datos)


def _idx_cercano(lat: float, lon: float, glat, glon) -> tuple[int, int]:
    """Índice (y, x) de la celda de grilla más cercana al punto (distancia plana,
    suficiente a escala regional)."""
    d2 = (glat - lat) ** 2 + (glon - lon) ** 2
    return tuple(int(v) for v in np.unravel_index(int(np.argmin(d2)), d2.shape))


def serie_precipitacion(lat: float, lon: float, *, leads=range(1, 25),
                        ciclo: datetime | None = None) -> dict:
    """Serie horaria de precipitación (mm/h) del WRF-DET para el punto dado.

    Descarga sólo los `leads` pedidos (cachea por archivo en disco). Devuelve el
    mismo contrato de 3 estados que el resto del sistema: ante cualquier fallo,
    `disponible=False` + 'error' (nunca un falso "sin lluvia").
    """
    if netCDF4 is None:
        return {"disponible": False, "fuente": "wrf-smn",
                "error": "netCDF4/numpy no instalados"}

    ciclo = ciclo or ciclo_disponible()
    if ciclo is None:
        return {"disponible": False, "fuente": "wrf-smn",
                "error": "sin ciclo WRF-DET publicado"}

    serie: list[dict] = []
    jj = ii = None
    try:
        for lead in leads:
            if not 0 <= lead <= MAX_LEAD:
                continue
            ds = _abrir_dataset(ciclo, lead)
            try:
                if jj is None:   # la grilla es fija -> resolvemos el índice una vez
                    jj, ii = _idx_cercano(lat, lon,
                                          ds.variables["lat"][:], ds.variables["lon"][:])
                pp = float(ds.variables["PP"][0, jj, ii])
                tvar = ds.variables["time"]
                ts = netCDF4.num2date(tvar[0], tvar.units)
            finally:
                ds.close()
            serie.append({"hora": ts.isoformat(), "pp_mm": round(pp, 1)})
    except Exception as e:
        return {"disponible": False, "fuente": "wrf-smn", "error": str(e)}

    pico = max(serie, key=lambda x: x["pp_mm"]) if serie else None
    return {
        "disponible": True,
        "fuente": "wrf-smn",
        "ciclo": ciclo.isoformat(),
        "resolucion_km": 4,
        "serie": serie,
        "pico_mm": pico["pp_mm"] if pico else 0.0,
        "hora_pico": pico["hora"] if pico else None,
    }


if __name__ == "__main__":
    # Smoke test manual: lote de ejemplo en zona núcleo bonaerense.
    import json
    print(json.dumps(serie_precipitacion(-34.6, -60.5, leads=range(1, 7)),
                     indent=2, ensure_ascii=False))
