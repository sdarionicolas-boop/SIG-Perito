"""Punto de entrada FastAPI: inicializa BD, siembra datos y monta routers.

Ejecutar en desarrollo:
    uvicorn app.main:app --reload --port 8000
"""
# Usar el almacén de certificados del SO (Windows/macOS/Linux) en lugar del bundle
# de certifi. Imprescindible cuando un antivirus/proxy con inspección TLS presenta
# su propia CA raíz (instalada en el SO): sin esto, requests hacia Open-Meteo
# resetea con ConnectionResetError/WinError 10054. Debe correr ANTES de cualquier
# conexión HTTPS, por eso está al tope del módulo.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:  # entorno sin truststore -> se cae a certifi (comportamiento previo)
    pass

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import CACHE_DIR, FRONTEND_DIST, REPORTES_DIR
from app.database import init_db
from app.metrics import TimingMiddleware
from app.routers import clima as clima_router
from app.routers import cosecha as cosecha_router
from app.routers import desvio as desvio_router
from app.routers import ia as ia_router
from app.routers import jobs as jobs_router
from app.routers import lotes as lotes_router
from app.routers import sanidad as sanidad_router
from app.routers import validacion as validacion_router
from app.routers import compliance as compliance_router
from app.routers import uso_suelo as uso_suelo_router
from app.routers import alertas_clima as alertas_clima_router
from app.routers import carbono as carbono_router
from app.routers import peritaje as peritaje_router
from app.routers import siniestros as siniestros_router
from app.services.seed import seed_if_empty




logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sig-agricola")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: crea tablas y siembra desde shapefile + CSV si está vacía."""
    init_db()
    try:
        result = seed_if_empty()
        logger.info("Seed: %s", result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Seed falló (continuo igual): %s", exc)
    yield


app = FastAPI(
    title="SIG Agrícola Bonaerense",
    description="Backend de teledetección agrícola sin GEE (CDSE / STAC / Open-Meteo).",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS abierto en desarrollo (el frontend Vite corre en otro puerto).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TimingMiddleware)

app.include_router(lotes_router.router)
app.include_router(clima_router.router)
app.include_router(ia_router.router)
app.include_router(sanidad_router.router)
app.include_router(cosecha_router.router)
app.include_router(desvio_router.router)
app.include_router(uso_suelo_router.router)
app.include_router(compliance_router.router)
app.include_router(alertas_clima_router.router)
app.include_router(validacion_router.router)
app.include_router(jobs_router.router)
app.include_router(carbono_router.router)
app.include_router(peritaje_router.router)
app.include_router(siniestros_router.router)





@app.get("/api/health", tags=["health"])
def health():
    return {"status": "ok"}


# Servir rásters/PNG de zonificación bajo /media.
app.mount("/media", StaticFiles(directory=str(CACHE_DIR)), name="media")

# Servir salidas del peritaje (PNG/HTML/CSV/KML) bajo /reportes.
REPORTES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/reportes", StaticFiles(directory=str(REPORTES_DIR)), name="reportes")


# Servir el build estático del frontend si existe (producción).
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True),
              name="frontend")
