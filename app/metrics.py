"""Registro en memoria de latencia por ruta + middleware de timing.

Liviano y sin dependencias: acumula conteo, promedio y p95 aproximado por
(método, plantilla de ruta). Se reinicia al reiniciar el proceso.
"""
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware

# ruta -> lista de duraciones (ms), recortada para no crecer sin límite
_SAMPLES: dict[str, list[float]] = defaultdict(list)
_MAX = 500


def _registrar(clave: str, ms: float) -> None:
    buf = _SAMPLES[clave]
    buf.append(ms)
    if len(buf) > _MAX:
        del buf[0]


def resumen_latencia() -> dict:
    out = {}
    for clave, ms in _SAMPLES.items():
        if not ms:
            continue
        ordenado = sorted(ms)
        p95 = ordenado[min(len(ordenado) - 1, int(len(ordenado) * 0.95))]
        out[clave] = {
            "n": len(ms),
            "avg_ms": round(sum(ms) / len(ms), 1),
            "p95_ms": round(p95, 1),
            "max_ms": round(max(ms), 1),
        }
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["avg_ms"]))


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        inicio = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - inicio) * 1000.0
        # Usar la plantilla de ruta (p. ej. /api/lotes/{lote_id}) para agrupar.
        ruta = request.scope.get("route")
        plantilla = getattr(ruta, "path", request.url.path)
        if plantilla.startswith("/api"):
            _registrar(f"{request.method} {plantilla}", ms)
        return response
