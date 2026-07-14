# app/services/goes_ot.py
"""Detección de topes nubosos penetrantes (Overshooting Tops) desde GOES-19 ABI.

Complementa a `goes_glm.py`: mientras GLM mide actividad eléctrica (proxy de
convección), este módulo mira la TEMPERATURA DEL TOPE NUBOSO (producto ABI-L2
ACHT, Cloud Top Temperature). Un "overshooting top" es una cúpula que penetra la
tropopausa —el tope de la tormenta empuja hacia arriba porque la corriente
ascendente es muy intensa— y se ve como un pico local ANORMALMENTE FRÍO respecto
del yunque (anvil) que lo rodea. Es el mejor indicador satelital de tormenta
severa con potencial de granizo grande, y es justo lo que miraba el contacto
("topes nubosos").

Fuente: bucket público 'noaa-goes19' (us-east-1), acceso anónimo, dominio
público NOAA. Argentina cae fuera de los sectores CONUS/Meso de GOES, así que se
usa el disco completo (Full Disk, sufijo 'F'), publicado cada 10 min.

Estructura verificada del bucket:
  ABI-L2-ACHTF/{AAAA}/{DDD}/{HH}/OR_ABI-L2-ACHTF-M6_G19_sAAAADDDHHMMSSS_e..._c...nc
    DDD = día juliano; cada archivo cubre un barrido de disco completo (~10 min).
  Variable: TEMP (temperatura del tope nuboso, K), grilla fija GOES 2 km.
  Proyección: geostationary; parámetros en la variable 'goes_imager_projection'.

Nota de eficiencia: el archivo de disco completo pesa (decenas de MB). Como los
lotes bonaerenses comparten el mismo barrido, para un batch conviene bajar el
granule UNA vez y submuestrear la caja de cada lote en memoria.
"""
import math
import re
from datetime import datetime, timedelta, timezone

import requests

try:
    import netCDF4
    import numpy as np
except ImportError:  # entorno sin libs científicas -> degradación explícita
    netCDF4 = None
    np = None

BASE_URL = "https://noaa-goes19.s3.amazonaws.com"
PRODUCT = "ABI-L2-ACHTF"          # Cloud Top Temperature, Full Disk
LATENCIA_MIN = 8                  # el barrido de disco completo tarda ~10 min

# Sesión persistente: reutiliza la conexión TCP/TLS entre requests. Con
# `requests.get()` suelto, cada descarga paga handshake nuevo — en un escaneo
# forense de ~20 archivos de ~28 MB cada uno eso se nota (medido: escaneos
# análogos en goes_glm.py bajaron ~4x al reusar sesión).
_session = requests.Session()

# --- Umbrales físicos (Cloud Top Temperature, K) -----------------------------
# Yunque de convección profunda: topes < -50 °C (223 K).
DEEP_CONV_K = 223.0
# Tropopausa de latitudes medias (verano): ~-60 °C. Un tope por debajo penetró
# hacia la baja estratosfera -> candidato a overshooting.
TROPOPAUSA_K = 213.0
# El OT debe ser un pico local: al menos 6 K más frío que el yunque circundante.
OT_DELTA_K = 6.0

RES_KM = 2.0                      # resolución nominal del producto ACHT full disk


def _c(k: float) -> float:
    """Kelvin -> Celsius, redondeado."""
    return round(k - 273.15, 1)


def _prefix_hora(hora_dt: datetime) -> str:
    return f"{PRODUCT}/{hora_dt:%Y}/{hora_dt.timetuple().tm_yday:03d}/{hora_dt:%H}/"


def _listar_granules(hora_dt: datetime) -> list[str]:
    """Claves de granules ACHT para una hora dada (REST S3 anónimo)."""
    url = f"{BASE_URL}/?list-type=2&prefix={_prefix_hora(hora_dt)}&max-keys=1000"
    r = _session.get(url, timeout=(5, 20))
    r.raise_for_status()
    return re.findall(r"<Key>([^<]+)</Key>", r.text)


def _ts_de_key(key: str) -> datetime | None:
    """Extrae el datetime de inicio (patrón _sAAAADDDHHMMSSS) del nombre."""
    m = re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})", key)
    if not m:
        return None
    y, ddd, hh, mm, ss = map(int, m.groups())
    return (datetime(y, 1, 1, tzinfo=timezone.utc)
            + timedelta(days=ddd - 1, hours=hh, minutes=mm, seconds=ss))


def _granule_mas_reciente(fin: datetime) -> tuple[str, datetime] | None:
    """El granule cuyo inicio sea el más cercano a `fin` sin pasarse.

    La ventana puede cruzar el borde de hora -> se listan la hora de `fin` y la
    anterior, y se elige el granule más nuevo con timestamp <= fin.
    """
    horas = {fin.replace(minute=0, second=0, microsecond=0),
             (fin - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)}
    mejor: tuple[str, datetime] | None = None
    for hd in horas:
        for key in _listar_granules(hd):
            ts = _ts_de_key(key)
            if ts is None or ts > fin:
                continue
            if mejor is None or ts > mejor[1]:
                mejor = (key, ts)
    return mejor


def _latlon_a_scan(lat: float, lon: float, proj) -> tuple[float, float] | None:
    """Geodésica (lat, lon) -> ángulos de escaneo (x, y) de la grilla fija GOES.

    Implementa la transformación del GOES-R PUG (Vol. 3, §5.1.2.8) usando los
    parámetros de la variable `goes_imager_projection`. Devuelve None si el punto
    no es visible desde el satélite (queda detrás del limbo terrestre).
    """
    lon0 = math.radians(float(proj.longitude_of_projection_origin))
    H = float(proj.perspective_point_height) + float(proj.semi_major_axis)
    r_eq = float(proj.semi_major_axis)
    r_pol = float(proj.semi_minor_axis)

    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    e2 = 1.0 - (r_pol ** 2) / (r_eq ** 2)

    phi_c = math.atan((r_pol ** 2 / r_eq ** 2) * math.tan(lat_r))
    rc = r_pol / math.sqrt(1.0 - e2 * math.cos(phi_c) ** 2)

    sx = H - rc * math.cos(phi_c) * math.cos(lon_r - lon0)
    sy = -rc * math.cos(phi_c) * math.sin(lon_r - lon0)
    sz = rc * math.sin(phi_c)

    # Test de visibilidad: el punto debe estar del lado visible del geoide.
    if H * (H - sx) < (sy ** 2 + (r_eq ** 2 / r_pol ** 2) * sz ** 2):
        return None

    y = math.atan(sz / sx)
    x = math.asin(-sy / math.sqrt(sx ** 2 + sy ** 2 + sz ** 2))
    return x, y


def _leer_temp_var(ds):
    """Devuelve la variable de temperatura del tope (TEMP) o un fallback 2D."""
    if "TEMP" in ds.variables:
        return ds.variables["TEMP"]
    # Fallback defensivo: primera variable 2D sobre (y, x) que no sea DQF.
    for name, var in ds.variables.items():
        if name != "DQF" and getattr(var, "dimensions", ()) == ("y", "x"):
            return var
    raise KeyError("No se encontró la variable de Cloud Top Temperature.")


def _nivel(min_ctt_k: float, overshoot: bool) -> str:
    if overshoot:
        return "alta"
    if min_ctt_k < TROPOPAUSA_K:
        return "media"          # tope muy frío pero sin firma clara de OT
    if min_ctt_k < DEEP_CONV_K:
        return "baja"           # convección profunda (yunque) sin penetración
    return "nula"


def _procesar_granule(raw: bytes, lat: float, lon: float, radio_km: float) -> dict | None:
    """Abre un granule ACHT desde memoria y extrae min/anvil CTT en la caja del punto.

    Devuelve None si el punto no es visible desde el satélite. Si no hay datos
    válidos en la caja (cielo despejado / relleno), devuelve n_valid=0.
    Factorizado para que lo usen tanto el nowcasting como el escaneo forense.
    """
    ds = netCDF4.Dataset("mem", memory=raw)
    try:
        proj = ds.variables["goes_imager_projection"]
        scan = _latlon_a_scan(lat, lon, proj)
        if scan is None:
            return None
        x_t, y_t = scan

        xs = ds.variables["x"][:]
        ys = ds.variables["y"][:]
        ix = int(np.argmin(np.abs(np.asarray(xs) - x_t)))
        iy = int(np.argmin(np.abs(np.asarray(ys) - y_t)))

        half = max(1, int(round(radio_km / RES_KM)))
        y0, y1 = max(0, iy - half), min(len(ys), iy + half + 1)
        x0, x1 = max(0, ix - half), min(len(xs), ix + half + 1)

        temp_var = _leer_temp_var(ds)
        caja = temp_var[y0:y1, x0:x1]
    finally:
        ds.close()

    arr = np.ma.masked_invalid(np.ma.asarray(caja, dtype="float64"))
    validos = arr.compressed()
    if validos.size == 0:
        return {"min_k": None, "anvil_k": None, "n_valid": 0}
    return {"min_k": float(validos.min()), "anvil_k": float(validos.mean()),
            "n_valid": int(validos.size)}


def _armar_resultado(min_k: float, anvil_k: float, extra: dict) -> dict:
    """Clasifica un par (min_k, anvil_k) y arma el dict de salida común."""
    delta_k = anvil_k - min_k
    deep = min_k < DEEP_CONV_K
    overshoot = (min_k < TROPOPAUSA_K) and (delta_k >= OT_DELTA_K)
    nivel = _nivel(min_k, overshoot)
    return {
        "disponible": True, "fuente": "goes19-abi-acht",
        "activo": deep, "overshooting": overshoot, "nivel": nivel,
        "ctt_min_c": _c(min_k), "ctt_anvil_c": _c(anvil_k),
        "delta_k": round(delta_k, 1),
        "umbral_tropopausa_c": _c(TROPOPAUSA_K),
        **extra,
    }


def overshooting_cercano(lat: float, lon: float, *, radio_km: float = 30.0,
                         ahora: datetime | None = None) -> dict:
    """Busca topes penetrantes (OT) en una caja de `radio_km` alrededor del punto.

    Baja el granule ACHT de disco completo más reciente, submuestrea la caja del
    lote sobre la grilla fija GOES y compara el tope más frío contra el yunque
    circundante. Contrato de 3 estados como el resto del sistema: ante cualquier
    fallo, `disponible=False` + 'error' (nunca un falso "sin convección").
    """
    if netCDF4 is None:
        return {"disponible": False, "fuente": "goes19-abi-acht",
                "error": "netCDF4/numpy no instalado"}

    ahora = ahora or datetime.now(timezone.utc)
    fin = ahora - timedelta(minutes=LATENCIA_MIN)

    try:
        elegido = _granule_mas_reciente(fin)
        if elegido is None:
            return {"disponible": False, "fuente": "goes19-abi-acht",
                    "error": "sin granule ACHT reciente"}
        key, ts = elegido
        raw = _session.get(f"{BASE_URL}/{key}", timeout=(5, 60)).content
        res = _procesar_granule(raw, lat, lon, radio_km)
        if res is None:
            return {"disponible": False, "fuente": "goes19-abi-acht",
                    "error": "punto no visible desde GOES-19"}
        if res["n_valid"] == 0:
            return {
                "disponible": True, "fuente": "goes19-abi-acht",
                "activo": False, "overshooting": False, "nivel": "nula",
                "radio_km": radio_km, "px_muestreados": 0,
                "hora": ts.isoformat(timespec="seconds"),
                "mensaje": "Cielo despejado o sin dato de tope nuboso en la caja.",
            }
    except Exception as e:  # noqa: BLE001
        return {"disponible": False, "fuente": "goes19-abi-acht", "error": str(e)}

    return _armar_resultado(res["min_k"], res["anvil_k"], {
        "radio_km": radio_km, "px_muestreados": res["n_valid"],
        "hora": ts.isoformat(timespec="seconds"),
    })


def overshooting_historico(
    lat: float, lon: float, inicio_utc: datetime, fin_utc: datetime, *,
    radio_km: float = 30.0, paso_min: int = 30, max_granules: int = 40,
) -> dict:
    """Escaneo FORENSE: busca el momento de mayor severidad de tope nuboso (OT)
    dentro de una ventana temporal YA PASADA (peritaje), a diferencia de
    `overshooting_cercano` que mira "ahora" (nowcasting).

    El bucket de NOAA GOES en AWS Open Data es un archivo permanente (no un
    buffer rolling), así que cualquier fecha desde que GOES-19 opera como
    GOES-East (2025) es consultable. Se muestrea cada `paso_min` minutos
    (cada granule pesa ~28 MB; con el paso por defecto, una ventana de una
    noche entera son ~15-20 descargas en vez de las ~90 que tendría a
    resolución nativa de 10 min) y se acota a `max_granules` como cota de
    seguridad de ancho de banda/tiempo.

    Devuelve el punto MÁS SEVERO (tope más frío) de la ventana + la serie
    completa muestreada, para graficar la evolución de la tormenta. Contrato
    de 3 estados igual que el resto del sistema.
    """
    if netCDF4 is None:
        return {"disponible": False, "fuente": "goes19-abi-acht",
                "error": "netCDF4/numpy no instalado"}

    objetivos = []
    t = inicio_utc
    while t <= fin_utc and len(objetivos) < max_granules:
        objetivos.append(t)
        t += timedelta(minutes=paso_min)

    cache_horas: dict[datetime, list[str]] = {}
    serie: list[dict] = []
    peor: dict | None = None
    usados: set[str] = set()
    granules_leidos = 0

    try:
        for obj in objetivos:
            hora_key = obj.replace(minute=0, second=0, microsecond=0)
            if hora_key not in cache_horas:
                cache_horas[hora_key] = _listar_granules(hora_key)
            candidatos = []
            for k in cache_horas[hora_key]:
                if k in usados:
                    continue
                ts = _ts_de_key(k)
                if ts is None:
                    continue
                candidatos.append((abs((ts - obj).total_seconds()), k, ts))
            if not candidatos:
                continue
            candidatos.sort(key=lambda c: c[0])
            _, key, ts = candidatos[0]
            usados.add(key)

            raw = _session.get(f"{BASE_URL}/{key}", timeout=(5, 60)).content
            granules_leidos += 1
            res = _procesar_granule(raw, lat, lon, radio_km)
            if res is None or res["n_valid"] == 0:
                continue
            punto = {"hora": ts.isoformat(timespec="seconds"),
                     "ctt_min_c": _c(res["min_k"]), "ctt_anvil_c": _c(res["anvil_k"])}
            serie.append(punto)
            if peor is None or res["min_k"] < peor["min_k"]:
                peor = {"min_k": res["min_k"], "anvil_k": res["anvil_k"],
                       "hora": ts.isoformat(timespec="seconds")}
    except Exception as e:  # noqa: BLE001
        return {"disponible": False, "fuente": "goes19-abi-acht", "error": str(e)}

    if peor is None:
        return {
            "disponible": True, "fuente": "goes19-abi-acht",
            "activo": False, "overshooting": False, "nivel": "nula",
            "radio_km": radio_km, "granules_leidos": granules_leidos,
            "granules_con_dato": 0, "serie": serie,
            "ventana": [inicio_utc.isoformat(timespec="seconds"),
                       fin_utc.isoformat(timespec="seconds")],
            "mensaje": "Sin datos de tope nuboso en la ventana (cielo despejado "
                      "en todos los granules muestreados, o punto fuera de disco).",
        }

    return _armar_resultado(peor["min_k"], peor["anvil_k"], {
        "radio_km": radio_km, "hora_pico": peor["hora"],
        "granules_leidos": granules_leidos, "granules_con_dato": len(serie),
        "ventana": [inicio_utc.isoformat(timespec="seconds"),
                   fin_utc.isoformat(timespec="seconds")],
        "serie": serie,
    })


if __name__ == "__main__":
    import json
    print(json.dumps(overshooting_cercano(-34.6, -60.5), indent=2, ensure_ascii=False))
