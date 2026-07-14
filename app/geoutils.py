import math
from shapely.geometry import shape
import geopandas as gpd

def get_utm_epsg(lon: float, lat: float) -> int:
    """
    Calculate the EPSG code for the UTM zone corresponding to a given longitude and latitude.
    For Argentina (Buenos Aires / Córdoba):
    - UTM 20S: EPSG 32720 (mainly Cordoba and Western Buenos Aires, longitudes -66 to -60)
    - UTM 21S: EPSG 32721 (mainly Central/Eastern Buenos Aires, longitudes -60 to -54)
    """
    # Calculate zone number (1 to 60)
    zone_number = math.floor((lon + 180) / 6) + 1
    
    # 32600 + zone for Northern Hemisphere, 32700 + zone for Southern Hemisphere
    if lat < 0:
        epsg = 32700 + zone_number
    else:
        epsg = 32600 + zone_number
        
    return epsg

def project_to_utm(geom, lon: float, lat: float):
    """
    Projects a shapely geometry (defined in EPSG:4326) to its corresponding local UTM zone.
    Returns the projected geometry.
    """
    epsg = get_utm_epsg(lon, lat)
    # Create a temporary GeoSeries to do the projection easily
    gs = gpd.GeoSeries([geom], crs="EPSG:4326")
    gs_projected = gs.to_crs(f"EPSG:{epsg}")
    return gs_projected.iloc[0], epsg

def calculate_area_hectares(geom, lon: float, lat: float) -> float:
    """
    Projects the geometry to UTM and returns its area in hectares (1 ha = 10,000 m^2).
    """
    projected_geom, _ = project_to_utm(geom, lon, lat)
    area_sq_meters = projected_geom.area
    return area_sq_meters / 10000.0
