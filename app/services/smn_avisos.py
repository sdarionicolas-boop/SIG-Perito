# app/services/smn_avisos.py
"""Avisos oficiales del SMN vía el Alert Hub de la WMO (SWIC 3.0), en formato CAP.

El API interno del SMN (ws.smn.gob.ar) quedó detrás de Cloudflare y no se puede
consumir server-side. En cambio la WMO agrega los avisos CAP oficiales de 130+
servicios nacionales —incluido el SMN— y los publica como JSON abierto, sin
Cloudflare, sin convenio. Es el canal machine-readable estándar y estable, y
mantiene el **peso legal**: cada aviso viene firmado por el 'Servicio
Meteorologico Nacional' (senderName), no inferido por nosotros.

Flujo (verificado 2026-07):
  1. Lista:  https://severeweather.wmo.int/json/wmo_all.json  -> {items:[...]}
     Cada item trae id, event, sent, expires y `url` (ruta al CAP XML).
     Los del SMN tienen `url` que empieza con 'ar-smn'.
  2. CAP:    https://severeweather.wmo.int/v2/cap-alerts/{url}  -> XML CAP 1.2
     De ahí salen event/severity/urgency/certainty, onset/expires (tz-aware),
     senderName y el <polygon> (lat,lon ...) para el point-in-polygon.

Todos los lotes comparten el mismo set nacional de avisos, así que se cachea
en memoria (TTL corto) y cada lote solo hace el point-in-polygon.
"""
import re
import time
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import requests

LIST_URL = "https://severeweather.wmo.int/json/wmo_all.json"
CAP_BASE = "https://severeweather.wmo.int/v2/cap-alerts/"
SOURCE_PREFIX = "ar-smn"          # avisos del SMN Argentina en el hub
CACHE_TTL_S = 300                 # 5 min: los avisos no cambian minuto a minuto
MAX_AVISOS = 250                  # cota de seguridad de fetches por refresco
_UA = {"User-Agent": "SIG-Agricola-Bonaerense/0.1 (+avisos-smn)"}
_CAP_NS = "{urn:oasis:names:tc:emergency:cap:1.2}"

_session = requests.Session()
_session.headers.update(_UA)
_cache = {"ts": 0.0, "avisos": None}   # avisos = lista de dicts ya parseados


def _texto(info, tag: str) -> str:
    el = info.find(f"{_CAP_NS}{tag}")
    return el.text.strip() if el is not None and el.text else ""


def _parse_cap(xml: str) -> dict | None:
    """Extrae los campos y el/los polígono(s) de un CAP XML del SMN."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    sender = root.find(f"{_CAP_NS}sender")
    sender_name_el = root.find(f".//{_CAP_NS}senderName")
    info = root.find(f"{_CAP_NS}info")
    if info is None:
        return None

    poligonos = []
    for area in info.findall(f"{_CAP_NS}area"):
        for poly in area.findall(f"{_CAP_NS}polygon"):
            if not poly.text:
                continue
            verts = []
            for par in poly.text.split():
                try:
                    la, lo = par.split(",")
                    verts.append((float(la), float(lo)))
                except ValueError:
                    continue
            if len(verts) >= 3:
                poligonos.append(verts)

    return {
        "event": _texto(info, "event"),
        "headline": _texto(info, "headline"),
        "severity": _texto(info, "severity"),
        "urgency": _texto(info, "urgency"),
        "certainty": _texto(info, "certainty"),
        "onset": _texto(info, "onset"),
        "expires": _texto(info, "expires"),
        "descripcion": _texto(info, "description"),
        "sender_name": (sender_name_el.text.strip()
                        if sender_name_el is not None and sender_name_el.text
                        else "Servicio Meteorológico Nacional"),
        "poligonos": poligonos,
    }


def _vigente(cap: dict) -> bool:
    """True si el aviso no expiró (usa el expires tz-aware del CAP)."""
    exp = cap.get("expires", "")
    if not exp:
        return True
    try:
        dt = datetime.fromisoformat(exp)
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= datetime.now(timezone.utc)


def _cargar_avisos_ar() -> list[dict]:
    """Devuelve los avisos vigentes del SMN (cacheados). Cada uno con polígonos."""
    ahora = time.time()
    if _cache["avisos"] is not None and ahora - _cache["ts"] < CACHE_TTL_S:
        return _cache["avisos"]

    r = _session.get(LIST_URL, timeout=(5, 20))
    r.raise_for_status()
    items = r.json().get("items", [])

    avisos, vistos = [], set()
    for it in items:
        url = it.get("url", "")
        if not url.startswith(SOURCE_PREFIX) or url in vistos:
            continue
        vistos.add(url)
        if len(vistos) > MAX_AVISOS:
            break
        try:
            xr = _session.get(CAP_BASE + url, timeout=(5, 20))
            if not xr.ok:
                continue
            cap = _parse_cap(xr.text)
        except requests.RequestException:
            continue
        if cap and cap["poligonos"] and _vigente(cap):
            avisos.append(cap)

    _cache.update(ts=ahora, avisos=avisos)
    return avisos


def _punto_en_poligono(lat: float, lon: float, verts: list[tuple]) -> bool:
    """Ray casting (PNPOLY). verts = [(lat, lon), ...]."""
    x, y = lon, lat
    dentro = False
    n = len(verts)
    j = n - 1
    for i in range(n):
        yi, xi = verts[i]
        yj, xj = verts[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            dentro = not dentro
        j = i
    return dentro


def avisos_para_lote(lat: float, lon: float) -> dict:
    """Avisos oficiales del SMN cuyo polígono cubre el punto del lote.

    Contrato de 3 estados: ante fallo de red, `disponible=False` + 'error'
    (nunca un falso "sin avisos").
    """
    try:
        avisos = _cargar_avisos_ar()
    except Exception as e:
        return {"disponible": False, "fuente": "smn-wmo-cap", "error": str(e)}

    activos = []
    for cap in avisos:
        if any(_punto_en_poligono(lat, lon, p) for p in cap["poligonos"]):
            activos.append({
                "event": cap["event"],
                "headline": cap["headline"] or cap["event"],
                "severity": cap["severity"],
                "urgency": cap["urgency"],
                "certainty": cap["certainty"],
                "onset": cap["onset"],
                "expires": cap["expires"],
                "fuente_oficial": cap["sender_name"],
            })

    return {
        "disponible": True,
        "fuente": "smn-wmo-cap",
        "vigente": len(activos) > 0,
        "avisos": activos,
        "total_pais": len(avisos),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(avisos_para_lote(-34.6, -60.5), indent=2, ensure_ascii=False))
