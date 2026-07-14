# app/services/goes_glm.py
"""Detección de rayos en tiempo real desde GOES-19 GLM (AWS Open Data).

GOES-19 es el GOES-East operativo (75.2°O) desde 2025 y cubre toda Sudamérica.
El producto GLM-L2-LCFA (Lightning Cluster-Filter Algorithm) reporta 'flashes'
(descargas eléctricas) cada 20 s. La actividad eléctrica es el mejor proxy en
tiempo real de tormenta convectiva severa. Bucket público 'noaa-goes19'
(us-east-1), acceso anónimo, dominio público NOAA -> apto para uso comercial.

Estructura verificada contra el bucket (2026-07):
  GLM-L2-LCFA/{AAAA}/{DDD}/{HH}/OR_GLM-L2-LCFA_G19_sAAAADDDHHMMSSS_e..._c...nc
    DDD = día juliano; cada archivo cubre 20 s.
  Variables: flash_lat (°N), flash_lon (°E), flash_energy (J), flash_area (m²).

Nota: GOES-16 (GOES-East hasta 2025) ya NO produce datos GLM vigentes; se usa
GOES-19. Eficiencia: una ventana de 15 min son ~45 granules (~70 MB). Para un
batch sobre muchos lotes conviene barrer los granules UNA vez y filtrar todos los
lotes en memoria (comparten el mismo set de descargas).
"""
import math
import re
from datetime import datetime, timedelta, timezone

import requests

try:
    import netCDF4
except ImportError:  # entorno sin libs científicas -> degradación explícita
    netCDF4 = None

BASE_URL = "https://noaa-goes19.s3.amazonaws.com"
PRODUCT = "GLM-L2-LCFA"
LATENCIA_MIN = 3          # margen de publicación de los granules más recientes

# Sesión persistente: reutiliza la conexión TCP/TLS entre requests. Con
# `requests.get()` suelto, cada descarga paga handshake nuevo (~1-1.5 s extra
# medido en la práctica) — en un escaneo forense de cientos de granules eso
# domina el tiempo total mucho más que el tamaño del archivo en sí.
_session = requests.Session()


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def _prefix_hora(hora_dt: datetime) -> str:
    return f"{PRODUCT}/{hora_dt:%Y}/{hora_dt.timetuple().tm_yday:03d}/{hora_dt:%H}/"


def _listar_granules(hora_dt: datetime) -> list[str]:
    """Claves de granules GLM para una hora dada (REST S3 anónimo)."""
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


def _nivel(n: int) -> str:
    if n >= 20:
        return "alta"
    if n >= 5:
        return "media"
    if n > 0:
        return "baja"
    return "nula"


def _nivel_historico(descargas_totales: int, descargas_pico_hora: int) -> str:
    """Nivel calibrado para escaneos FORENSES (ventanas largas, no nowcasting).

    Los umbrales de `_nivel()` están pensados para 10 min de nowcasting (>20
    rayos = alta). En una noche entera, cualquier tormenta común pasa 20
    rayos, así que hay que subir la vara: se mira además el pico por hora
    (más discriminante para "tormenta convectiva SEVERA sostenida").
    """
    if descargas_totales >= 500 or descargas_pico_hora >= 200:
        return "alta"        # tormenta severa: cientos de rayos, pico sostenido
    if descargas_totales >= 100 or descargas_pico_hora >= 50:
        return "media"       # tormenta convectiva ordinaria
    if descargas_totales > 0:
        return "baja"        # algunos rayos aislados
    return "nula"


def rayos_cercanos(lat: float, lon: float, *, radio_km: float = 40.0,
                   ventana_min: int = 10, ahora: datetime | None = None) -> dict:
    """Cuenta descargas GLM dentro de `radio_km` del punto en los últimos
    `ventana_min` (descontando la latencia de publicación).

    Contrato de 3 estados como el resto del sistema: ante cualquier fallo,
    `disponible=False` + 'error' (nunca un falso "sin actividad").
    """
    if netCDF4 is None:
        return {"disponible": False, "fuente": "goes19-glm",
                "error": "netCDF4 no instalado"}

    ahora = ahora or datetime.now(timezone.utc)
    fin = ahora - timedelta(minutes=LATENCIA_MIN)
    inicio = fin - timedelta(minutes=ventana_min)
    # La ventana puede cruzar el borde de hora -> barremos ambas horas.
    horas = {inicio.replace(minute=0, second=0, microsecond=0),
             fin.replace(minute=0, second=0, microsecond=0)}

    total = 0
    granules_leidos = 0
    try:
        for hd in sorted(horas):
            for key in _listar_granules(hd):
                ts = _ts_de_key(key)
                if ts is None or not (inicio <= ts <= fin):
                    continue
                raw = _session.get(f"{BASE_URL}/{key}", timeout=(5, 30)).content
                ds = netCDF4.Dataset("mem", memory=raw)   # abrir desde memoria
                try:
                    fla = ds.variables["flash_lat"][:]
                    flo = ds.variables["flash_lon"][:]
                finally:
                    ds.close()
                granules_leidos += 1
                for la, lo in zip(fla, flo):
                    if _haversine_km(lat, lon, float(la), float(lo)) <= radio_km:
                        total += 1
    except Exception as e:
        return {"disponible": False, "fuente": "goes19-glm", "error": str(e)}

    return {
        "disponible": True,
        "fuente": "goes19-glm",
        "activo": total > 0,
        "descargas": total,
        "nivel": _nivel(total),
        "radio_km": radio_km,
        "ventana_min": ventana_min,
        "granules": granules_leidos,
        "hasta": fin.isoformat(timespec="seconds"),
    }


def rayos_historicos(
    lat: float, lon: float, inicio_utc: datetime, fin_utc: datetime, *,
    radio_km: float = 40.0, paso_seg: int = 120, max_granules: int = 300,
) -> dict:
    """Escaneo FORENSE: cuenta rayos GOES-19 GLM en una caja de `radio_km`
    durante una ventana temporal YA PASADA (peritaje).

    A diferencia de `rayos_cercanos()` (nowcasting), el bucket S3 de NOAA es
    archivo permanente. GLM tiene ~180 granules/hora (uno cada 20 s), así que
    10 horas serían ~1800 granules ≈ 1 GB — inaceptable para peritaje
    interactivo. **Se submuestrea leyendo 1 granule cada `paso_seg` segundos**
    (default 120 s → ~30/hora), y los conteos por hora se ESCALAN por el
    factor de submuestreo real de esa hora para estimar el total.

    La calibración de nivel usa los valores extrapolados, así que "alta"
    sigue correspondiendo a tormenta severa real (no queda sesgado por el
    muestreo). Contrato de 3 estados igual que el resto del sistema.
    """
    if netCDF4 is None:
        return {"disponible": False, "fuente": "goes19-glm",
                "error": "netCDF4 no instalado"}

    horas = []
    h = inicio_utc.replace(minute=0, second=0, microsecond=0)
    while h <= fin_utc:
        horas.append(h)
        h += timedelta(hours=1)

    granules_leidos = 0
    muestras_por_hora: dict[str, int] = {}     # rayos contados en las muestras
    granules_muestreados_por_hora: dict[str, int] = {}
    proximo_target = inicio_utc

    try:
        for hd in horas:
            keys = sorted(_listar_granules(hd))
            for key in keys:
                if granules_leidos >= max_granules:
                    break
                ts = _ts_de_key(key)
                if ts is None or not (inicio_utc <= ts <= fin_utc):
                    continue
                # Submuestreo temporal: sólo tomamos el primer granule
                # posterior a cada target y adelantamos el reloj `paso_seg`.
                if ts < proximo_target:
                    continue
                proximo_target = ts + timedelta(seconds=paso_seg)

                raw = _session.get(f"{BASE_URL}/{key}", timeout=(5, 30)).content
                ds = netCDF4.Dataset("mem", memory=raw)
                try:
                    fla = ds.variables["flash_lat"][:]
                    flo = ds.variables["flash_lon"][:]
                finally:
                    ds.close()
                granules_leidos += 1
                n_en_caja = sum(
                    1 for la, lo in zip(fla, flo)
                    if _haversine_km(lat, lon, float(la), float(lo)) <= radio_km
                )
                hkey = ts.replace(minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
                muestras_por_hora[hkey] = muestras_por_hora.get(hkey, 0) + n_en_caja
                granules_muestreados_por_hora[hkey] = (
                    granules_muestreados_por_hora.get(hkey, 0) + 1)
            if granules_leidos >= max_granules:
                break
    except Exception as e:  # noqa: BLE001
        return {"disponible": False, "fuente": "goes19-glm", "error": str(e)}

    # Escalar cada hora por el factor de submuestreo REAL de esa hora (más
    # honesto que un factor global fijo: cada hora puede tener un número
    # distinto de granules disponibles). Factor ≈ 180 / n_muestreados_esa_hora.
    GRANULES_POR_HORA_NOMINAL = 180  # GLM cada 20 s
    por_hora_estimado: dict[str, int] = {}
    for hkey, muestras in muestras_por_hora.items():
        n_muestras = granules_muestreados_por_hora[hkey]
        factor = GRANULES_POR_HORA_NOMINAL / n_muestras if n_muestras else 1
        por_hora_estimado[hkey] = int(round(muestras * factor))

    total_estimado = sum(por_hora_estimado.values())
    pico_por_hora = max(por_hora_estimado.values(), default=0)
    hora_pico = (max(por_hora_estimado, key=por_hora_estimado.get)
                 if por_hora_estimado else None)

    serie = [{"hora": h, "descargas": n}
             for h, n in sorted(por_hora_estimado.items())]

    return {
        "disponible": True,
        "fuente": "goes19-glm",
        "activo": total_estimado > 0,
        "descargas_totales": total_estimado,       # estimadas por escalado
        "descargas_pico_hora": pico_por_hora,
        "hora_pico": hora_pico,
        "nivel": _nivel_historico(total_estimado, pico_por_hora),
        "radio_km": radio_km,
        "granules_leidos": granules_leidos,
        "paso_seg": paso_seg,
        "estimacion_por_submuestreo": True,        # avisar que se extrapoló
        "ventana": [inicio_utc.isoformat(timespec="seconds"),
                    fin_utc.isoformat(timespec="seconds")],
        "serie": serie,
        "limitado_por_cota": granules_leidos >= max_granules,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(rayos_cercanos(-34.6, -60.5), indent=2, ensure_ascii=False))
