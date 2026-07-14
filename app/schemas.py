"""Modelos Pydantic para entrada/salida de la API."""
from typing import Optional

from pydantic import BaseModel, Field


class LoteOut(BaseModel):
    id: int
    nombre: str
    cultivo: Optional[str] = None
    campo: Optional[str] = None
    lote_num: Optional[str] = None
    centroide_lat: Optional[float] = None
    centroide_lon: Optional[float] = None
    area_ha: Optional[float] = None
    crs_original: Optional[str] = None
    created_at: Optional[str] = None


class SeriePunto(BaseModel):
    fecha: str
    indice: str
    valor: Optional[float] = None
    valor_min: Optional[float] = None
    valor_max: Optional[float] = None
    valor_std: Optional[float] = None
    sensor: Optional[str] = None
    orbita: Optional[str] = None
    pct_valido: Optional[float] = None


class SerieTemporalOut(BaseModel):
    lote_id: int
    indice: str
    puntos: list[SeriePunto]
    cacheado: bool = True
    job_id: Optional[str] = None
    mensaje: Optional[str] = None


class JobOut(BaseModel):
    id: str
    lote_id: Optional[int] = None
    tipo: str
    estado: str
    progreso: int
    mensaje: Optional[str] = None
    error_msg: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ForecastDia(BaseModel):
    fecha: str
    t_min: Optional[float] = None
    t_max: Optional[float] = None
    helada_meteorologica: bool = False
    helada_agrometeorologica: bool = False
    horas_estres_termico: int = 0
    estres_termico: bool = False
    estres_confianza: str = "alta"
    vpd_medio: Optional[float] = None
    vpd_max: Optional[float] = None
    secado: Optional[str] = None


class ForecastResumen(BaseModel):
    dias_con_helada: int = 0
    dias_con_estres_termico: int = 0
    proxima_helada: Optional[str] = None


class ForecastOut(BaseModel):
    lote_id: int
    modelo: str
    generado: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    elevacion: Optional[float] = None
    dias: list[ForecastDia]
    resumen: ForecastResumen


class UploadResponse(BaseModel):
    id: int
    nombre: str
    area_ha: Optional[float] = None
    mensaje: str = "Lote registrado correctamente."


class MargenInput(BaseModel):
    rinde_objetivo: float = Field(..., gt=0, description="Rinde medio esperado (kg/ha)")
    precio: float = Field(..., gt=0, description="Precio de venta ($/kg)")
    costo_base: float = Field(..., ge=0, description="Costo directo ($/ha)")


class GeoJSONUpload(BaseModel):
    """Subida de un lote como GeoJSON crudo (Feature, geometry o FeatureCollection)."""
    nombre: Optional[str] = Field(None, description="Nombre del lote (opcional)")
    cultivo: Optional[str] = None
    geojson: dict
