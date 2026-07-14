"""Configuración central: rutas y parámetros leídos del entorno (.env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Raíz del proyecto (un nivel por encima de app/)
BASE_DIR = Path(__file__).resolve().parent.parent

# Cargar .env desde la raíz del proyecto si existe.
load_dotenv(BASE_DIR / ".env")

# Directorios principales (en disco persistente)
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = BASE_DIR / "cache" / "tifs"
EXTRACTOR_DIR = BASE_DIR / "extractor"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

# Archivos clave
DB_PATH = DATA_DIR / "agri.db"
SHAPEFILE_PATH = DATA_DIR / "lotes_mani.shp"
LOTES_GEOJSON = DATA_DIR / "lotes.geojson"   # snapshot completo (preferido para seeding)
CSV_NDVI = DATA_DIR / "serie_temporal_lotes.csv"
CSV_SAR = DATA_DIR / "serie_temporal_sar_lotes.csv"
CSV_NDWI = DATA_DIR / "serie_temporal_ndwi.csv"
REPORTES_DIR = DATA_DIR / "reportes"   # salidas del pipeline de cosecha (local)

# Parámetros de extracción (configurables por entorno)
FECHA_INICIO = os.environ.get("EXTRACCION_FECHA_INICIO", "2025-10-01")
FECHA_FIN = os.environ.get("EXTRACCION_FECHA_FIN", "2026-05-31")
MIN_VALID_PCT = float(os.environ.get("MIN_VALID_PCT", "70"))

# Servicios externos de edafología
SOILGRIDS_URL = os.environ.get("SOILGRIDS_URL", "https://rest.isric.org/soilgrids/v2.0")
SOC_LOCAL_DIR = DATA_DIR / "soc"

# Asegurar que los directorios necesarios existan
for _d in (DATA_DIR, CACHE_DIR, SOC_LOCAL_DIR):
    _d.mkdir(parents=True, exist_ok=True)

