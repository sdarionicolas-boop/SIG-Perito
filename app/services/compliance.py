# app/services/compliance.py
import json
import hashlib
import sqlite3
from datetime import date
from fpdf import FPDF
from shapely.geometry import shape
from app.config import DB_PATH
from app.services.uso_suelo import (
    crop_noncrop, detectar_desmonte, fuente_lote, obtener_historial_cobertura,
)

# EUDR post-2020: tolerancia CERO real (no el umbral 50%->30% de detectar_desmonte,
# pensado para no confundir hectáreas contiguas de bosque con ruido de clasificación
# píxel a píxel). Cualquier pérdida de Bosque Nativo por encima de este margen
# técnico (redondeo de pixel/área) ya es una violación bajo EUDR.
UMBRAL_PERDIDA_HA_EUDR = 0.05

# 2BSvs: la deforestación relevante es >1 ha (así lo define el pliego). Además penaliza
# la conversión de pastizales/pasturas naturales a agricultura post-2008 (reduce el
# "área elegible"). Filtramos con un mínimo en puntos porcentuales para no confundir
# jitter de clasificación MapBiomas con una conversión real.
UMBRAL_DEFOR_2BSVS_HA = 1.0
UMBRAL_CONVERSION_PASTURA_PP = 5.0

class CertificadoPDF(FPDF):
    def header(self):
        # Título principal
        self.set_font("helvetica", "B", 14)
        self.set_text_color(16, 185, 129) # Emerald color
        self.cell(0, 10, "SIG AGRICOLA · REPOSITORIO DE CERTIFICACION", border=False, ln=True, align="C")
        self.set_font("helvetica", "B", 10)
        fuente = getattr(self, "fuente", "simulado")
        if fuente == "real":
            self.set_text_color(14, 116, 144)  # Sky: cobertura real
            txt = "[ PROTOTIPO - COBERTURA REAL MapBiomas Argentina Col.2 ]"
        elif fuente == "estimado":
            self.set_text_color(217, 119, 6)  # Ambar: estimacion por defecto
            txt = "[ PROTOTIPO - COBERTURA ESTIMADA POR DEFECTO (sin dato MapBiomas) ]"
        else:
            self.set_text_color(239, 68, 68)  # Rojo: datos simulados
            txt = "[ DEMO - DOCUMENTO CON DATOS SIMULADOS ]"
        self.cell(0, 5, txt, border=False, ln=True, align="C")
        self.ln(10)
        
    def footer(self):
        self.set_y(-15)
        self.set_font("helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Página {self.page_no()}/{{nb}} · Evidencia Digital Inmutable", align="C")

def evaluar_compliance(lote_id: int) -> dict:
    """
    Evalúa el cumplimiento regulatorio del lote frente a normativas internacionales:
      - EUDR: Libre de deforestación post 31/12/2020.
      - RTRS: Libre de deforestación post 03/06/2016.
      - 2BSvs: Libre de deforestación post 01/01/2008.
    """
    # 1. Obtener metadatos del lote desde la BD
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT nombre, area_ha, centroide_lat, centroide_lon FROM lotes WHERE id = ?", 
        (lote_id,)
    ).fetchone()
    conn.close()
    
    if not row:
        return {"error": "Lote no encontrado"}
        
    nombre = row["nombre"]
    area_ha = row["area_ha"] if row["area_ha"] else 0.0
    lat = row["centroide_lat"] if row["centroide_lat"] else 0.0
    lon = row["centroide_lon"] if row["centroide_lon"] else 0.0
    
    # 2. Consultar historial de cobertura y DERIVAR el año de desmonte del dato.
    #    Single source of truth: el mismo historial que grafica el paso "Uso de Suelo".
    #    Ventana ampliada a 2007 para captar transiciones anteriores al rango del visor.
    historial = obtener_historial_cobertura(nombre, anio_desde=2007, anio_hasta=2024)
    anio_desmonte = detectar_desmonte(historial)

    # Un desmonte posterior a la fecha de corte de cada norma implica incumplimiento.
    deforestado_post_2008 = anio_desmonte is not None and anio_desmonte > 2008
    deforestado_post_2016 = anio_desmonte is not None and anio_desmonte > 2016

    fuente = fuente_lote(nombre)  # 'real' | 'estimado' | 'simulado'

    # Cobertura del año más reciente disponible (referencia común a EUDR y 2BSvs).
    cobertura_actual = historial[-1]["cobertura"]
    bosque_actual = cobertura_actual.get("Bosque Nativo", 0.0)

    # --- Profundización 2BSvs: ÁREA ELEGIBLE (corte 2008) ---
    # 2BSvs no solo objeta bosque->agricultura, también penaliza la conversión de
    # pastizales/pasturas naturales a agricultura post-2008. El "área elegible" es la
    # superficie agrícola menos lo convertido desde bosque o pastura. Las capas de
    # biodiversidad/áreas protegidas (RAMSAR, AICAS, IUCN) que el pliego también resta
    # no las tenemos, así que quedan como salvedad explícita.
    cobertura_2008 = next((p["cobertura"] for p in historial if p["anio"] == 2008),
                          historial[0]["cobertura"])
    bosque_2008 = cobertura_2008.get("Bosque Nativo", 0.0)
    pastura_2008 = cobertura_2008.get("Pastura", 0.0)
    agri_actual_pct = cobertura_actual.get("Agricultura", 0.0)
    pastura_actual = cobertura_actual.get("Pastura", 0.0)

    agri_actual_ha = round(area_ha * agri_actual_pct / 100, 2)
    defor_2008_ha = round(area_ha * max(0.0, bosque_2008 - bosque_actual) / 100, 2)
    drop_pastura_pp = max(0.0, pastura_2008 - pastura_actual)
    conversion_pastura_ha = round(
        area_ha * drop_pastura_pp / 100, 2) if drop_pastura_pp > UMBRAL_CONVERSION_PASTURA_PP else 0.0
    area_elegible_ha = round(max(0.0, agri_actual_ha - defor_2008_ha - conversion_pastura_ha), 2)
    pct_elegible = round(area_elegible_ha / agri_actual_ha * 100, 1) if agri_actual_ha > 0 else 0.0

    if defor_2008_ha > UMBRAL_DEFOR_2BSVS_HA:
        veredicto_2bsvs = "Rechazado"
    elif conversion_pastura_ha > 0:
        veredicto_2bsvs = "Aprobado con salvedad (conversión pastizal/pastura)"
    else:
        veredicto_2bsvs = "Aprobado"

    # --- Profundización EUDR: discriminación crop/noncrop + análisis de DOS períodos ---
    # El pliego real exige tratar EUDR distinto a las demás: pre-2020 se evalúa por
    # categoría OTBN (Ley 26.331, Argentina) -- Cat. I/II no elegible, Cat. III podría
    # serlo con un PCUS -- y post-2020 es tolerancia CERO sobre el polígono total, sin
    # importar la categoría. No tenemos la capa OTBN provincial (no hay fuente pública
    # centralizada), así que un desmonte pre-2020 se marca "con salvedad" en lugar de
    # aprobarlo u objetarlo a ciegas.
    uso_suelo_actual = crop_noncrop(cobertura_actual)
    crop_ha = round(area_ha * uso_suelo_actual["crop_pct"] / 100, 2)
    noncrop_ha = round(area_ha * uso_suelo_actual["noncrop_pct"] / 100, 2)

    historial_pre_2020 = [p for p in historial if p["anio"] <= 2020]
    anio_desmonte_pre_2020 = detectar_desmonte(historial_pre_2020)

    bosque_2020 = next((p["cobertura"].get("Bosque Nativo", 0.0)
                        for p in historial if p["anio"] == 2020), 0.0)
    perdida_post_2020_ha = round(area_ha * max(0.0, bosque_2020 - bosque_actual) / 100, 2)
    desmonte_post_2020 = perdida_post_2020_ha > UMBRAL_PERDIDA_HA_EUDR

    if desmonte_post_2020:
        veredicto_eudr = "Rechazado"
    elif anio_desmonte_pre_2020 is not None:
        veredicto_eudr = "Aprobado con salvedad (OTBN no verificado)"
    else:
        veredicto_eudr = "Aprobado"

    # --- RFS2 (EPA, corte 19/12/2007, tolerancia CERO a deforestación) ---
    # Solo mira deforestación de bosque (no conversión de pastizal). Nuestro ráster
    # más antiguo es 2008, que usamos como línea base (~1 año del corte real, sin
    # efecto en campos agrícolas estables). RFS2 exige además firma de Ing. Agrónomo
    # matriculado -> el certificado 100% automático no alcanza (queda como nota).
    area_elegible_rfs2_ha = round(max(0.0, agri_actual_ha - defor_2008_ha), 2)
    veredicto_rfs2 = "Rechazado" if defor_2008_ha > UMBRAL_PERDIDA_HA_EUDR else "Aprobado"

    # --- CFR (Canadá, corte 01/07/2020, criterios LUB) ---
    # Como EUDR post-2020 (deforestación) + conversión de pastizal/pastura post-2020
    # (Excluded Lands). Riparias/RVT/wetlands requieren capas que no tenemos -> salvedad.
    pastura_2020 = next((p["cobertura"].get("Pastura", 0.0)
                         for p in historial if p["anio"] == 2020), 0.0)
    drop_pastura_post_2020 = max(0.0, pastura_2020 - pastura_actual)
    conv_pastura_post_2020_ha = round(
        area_ha * drop_pastura_post_2020 / 100, 2) if drop_pastura_post_2020 > UMBRAL_CONVERSION_PASTURA_PP else 0.0
    if perdida_post_2020_ha > UMBRAL_DEFOR_2BSVS_HA:
        veredicto_cfr = "Rechazado"
    elif conv_pastura_post_2020_ha > 0:
        veredicto_cfr = "Aprobado con salvedad (conversión pastizal/pastura post-2020)"
    else:
        veredicto_cfr = "Aprobado"

    # 3. Construir datos canónicos determinísticos (independiente de PDF)
    # NOTA CRÍTICA: La fecha_emision es fija para que el hash sea 100% reproducible.
    canonical_data = {
        "lote_id": int(lote_id),
        "lote_nombre": nombre,
        "area_hectareas": float(area_ha),
        "coordenadas_centroide": [round(float(lat), 6), round(float(lon), 6)],
        "checks_compliance": {
            "RFS2_2007": veredicto_rfs2,
            "2BSvs_2008": veredicto_2bsvs,
            "RTRS_2016": "Aprobado" if not deforestado_post_2016 else "Rechazado",
            "EUDR_2020": veredicto_eudr,
            "CFR_2020": veredicto_cfr,
        },
        "eudr_detalle": {
            "uso_suelo_actual": {**uso_suelo_actual, "crop_ha": crop_ha, "noncrop_ha": noncrop_ha},
            "periodo_pre_2020": {
                "rango": "2008-2020",
                "desmonte_detectado": anio_desmonte_pre_2020 is not None,
                "anio_estimado": anio_desmonte_pre_2020,
                "categoria_otbn": "No verificado (requiere capa OTBN provincial, Ley 26.331)",
            },
            "periodo_post_2020": {
                "rango": "2021-2024",
                "tolerancia": "cero",
                "perdida_bosque_ha": perdida_post_2020_ha,
                "cumple": not desmonte_post_2020,
            },
        },
        "dosbsvs_detalle": {
            "area_agricola_ha": agri_actual_ha,
            "deforestacion_post_2008_ha": defor_2008_ha,
            "conversion_pastura_post_2008_ha": conversion_pastura_ha,
            "area_elegible_ha": area_elegible_ha,
            "pct_elegible": pct_elegible,
            "biodiversidad": "No verificado (requiere capas RAMSAR/AICAS/IUCN/áreas protegidas)",
            "nota": "Conversión estimada de pastura/pastizal; distinguir pastizal natural (clase MapBiomas 12) de pastura manejada (15) requiere desagregar la reclasificación de 5 categorías.",
        },
        "rfs2_detalle": {
            "corte": "19/12/2007 (base ráster 2008)",
            "tolerancia": "cero",
            "deforestacion_ha": defor_2008_ha,
            "area_elegible_ha": area_elegible_rfs2_ha,
            "requiere_firma_agronomo": True,
            "nota": "RFS2 exige firma de Ing. Agrónomo matriculado; el certificado automático no sustituye ese aval.",
        },
        "cfr_detalle": {
            "corte": "01/07/2020",
            "perdida_bosque_ha": perdida_post_2020_ha,
            "conversion_pastura_post_2020_ha": conv_pastura_post_2020_ha,
            "biodiversidad": "No verificado (riparias 30m/IGN, especies RVT/IUCN, wetlands)",
        },
        "deforestacion_detectada": {
            "ocurrio": deforestado_post_2008,
            "anio_estimado": anio_desmonte
        },
        "fecha_certificacion_canonico": "2026-07-02",
        "version_algoritmo": {
            "real": "1.3.0-mapbiomas-col2",
            "estimado": "1.3.0-estimado-default",
            "simulado": "1.3.0-mock-demo",
        }[fuente],
    }

    # 4. Calcular el Hash SHA-256 del JSON canónico ordenado
    canonical_str = json.dumps(canonical_data, sort_keys=True)
    sha_hash = hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

    return {
        "canonical": canonical_data,
        "hash": sha_hash,
        "fuente": fuente,
    }


_UNICODE_MAP = str.maketrans({
    "—": "-", "–": "-", "−": "-", "‑": "-",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "…": "...", "•": "*", "·": "-", "→": "->", "←": "<-", "↔": "<->",
    "≥": ">=", "≤": "<=", "±": "+/-", "×": "x", "÷": "/",
    "²": "2", "³": "3", "°": "o",
    "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "ü": "u",
    "Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U", "Ñ": "N", "Ü": "U",
    "ç": "c", "Ç": "C", "€": "EUR", "£": "GBP", "©": "(c)", "®": "(R)", "™": "TM",
})


def _safe(texto: str) -> str:
    """Reemplaza caracteres unicode no soportados por helvetica (latin-1) por equivalentes ASCII."""
    if not texto:
        return ""
    return str(texto).translate(_UNICODE_MAP).encode("latin-1", "replace").decode("latin-1")


def _seccion_titulo(pdf, numero: int, titulo: str) -> None:
    """Encabezado uniforme de sección (numeración + título en bold)."""
    pdf.set_font("helvetica", "B", 10)
    pdf.set_text_color(40, 40, 40)
    pdf.cell(0, 6, _safe(f"{numero}. {titulo}"), ln=True)


def _linea(pdf, texto: str, indent: bool = False) -> None:
    """Escribe una línea de contenido normal."""
    pdf.set_font("helvetica", "", 9)
    pdf.set_text_color(40, 40, 40)
    if indent:
        pdf.cell(4, 5, "", ln=False)
    pdf.cell(0, 5, _safe(texto), ln=True)


def _nota(pdf, texto: str) -> None:
    """Escribe una nota en itálica gris."""
    pdf.set_font("helvetica", "I", 8)
    pdf.set_text_color(150, 150, 150)
    pdf.multi_cell(0, 4, _safe(texto))
    pdf.set_text_color(40, 40, 40)


def _fila_tabla(pdf, celdas: list[tuple[float, str]], header: bool = False) -> None:
    """Dibuja una fila de tabla con anchos custom por celda."""
    if header:
        pdf.set_font("helvetica", "B", 9)
        pdf.set_fill_color(240, 240, 240)
    else:
        pdf.set_font("helvetica", "", 8)
    for i, (ancho, texto) in enumerate(celdas):
        pdf.cell(ancho, 5.5, _safe(texto), border=1, fill=header, ln=(i == len(celdas) - 1))
    if header:
        pdf.set_font("helvetica", "", 9)


# --- Datos del sistema (biodiversidad + config de motores) ---

_CAPAS_BIODIVERSIDAD = [
    ("RAMSAR (humedales internacionales)", "www.ramsar.org", "2BSvs, EUDR", "No verificado"),
    ("WDPA · Protected Planet (areas protegidas)", "protectedplanet.net", "2BSvs, EUDR, CFR", "No verificado"),
    ("KBA / AICAS (aves - HCV1)", "keybiodiversityareas.org / Aves Argentinas", "2BSvs, RTRS", "No verificado"),
    ("IUCN Lista Verde de Areas Protegidas", "iucn.org", "2BSvs, EUDR", "No verificado"),
    ("IUCN Red List (especies RVT)", "iucnredlist.org", "CFR", "No verificado"),
    ("OTBN Cat. I/II/III (Ley 26.331 Argentina)", "Autoridad de Aplicacion provincial", "EUDR pre-2020", "No verificado"),
    ("Inventario Nacional de Humedales", "MAyDS Argentina", "2BSvs, CFR, RTRS", "No verificado"),
    ("Buffer ripario 30 m (hidrografia IGN)", "ign.gob.ar", "CFR", "No verificado"),
    ("SIB / SNAP / SIFAP (areas protegidas AR)", "sib.gob.ar / SNAP / SIFAP", "2BSvs, EUDR", "No verificado"),
    ("Cercania <2 km a areas de conservacion", "Buffer sobre WDPA + capas nacionales", "2BSvs", "No verificado"),
]

_METODOLOGIA = [
    ("Deforestacion / uso de suelo", "MapBiomas Argentina Coleccion 2", "30 m/pixel", "2007-2024 (anual)"),
    ("Serie temporal NDVI del lote", "Sentinel-2 L2A via Copernicus DataSpace", "10 m/pixel", "campana en curso"),
    ("Cohorte regional NDVI 5267", "Lotes propios del mismo cultivo en radio operativo", "10 m/pixel", "campana en curso"),
    ("Carbono Organico del Suelo (SOC)", "INTA (Gaitan et al.) + fallback SoilGrids v2.0 ISRIC", "500 m nacional / 250 m global", "estatico"),
    ("Deteccion automatica de cultivos", "INTA SEPA - Mapa Nacional de Cultivo (verano + invierno)", "30 m/pixel", "campana 2024-25"),
    ("Geometria del poligono", "KML/KMZ importado del productor", "vectorial", "fecha de alta"),
    ("Comparativa multi-norma", "Motor propio contra pliegos oficiales (RFS2, 2BSvs, RTRS, EUDR, CFR)", "N/A", "actualizado 2026-07"),
]


def _obtener_datos_extendidos(lote_id: int) -> dict:
    """Reune datos adicionales del lote para el reporte specimen (no afectan el hash)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT nombre, cultivo, campo, geom_geojson, centroide_lat, centroide_lon, "
        "area_ha, created_at FROM lotes WHERE id=?", (lote_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {}

    data = {
        "nombre": row["nombre"],
        "cultivo": row["cultivo"] or "no declarado",
        "campo": row["campo"] or "-",
        "lat": row["centroide_lat"],
        "lon": row["centroide_lon"],
        "area_ha": row["area_ha"],
        "fecha_alta": row["created_at"] or "-",
        "vertices": [],
        "historial": [],
        "ndvi": None,
        "soc": None,
    }

    # Vertices del poligono
    if row["geom_geojson"]:
        try:
            geom = json.loads(row["geom_geojson"])
            coords = geom["coordinates"][0]
            # Evitar duplicado del vertice de cierre
            if len(coords) > 1 and coords[0] == coords[-1]:
                coords = coords[:-1]
            data["vertices"] = coords
            data["shapely_geom"] = shape(geom)
        except Exception:
            pass

    # Historial de cobertura MapBiomas
    try:
        hist = obtener_historial_cobertura(row["nombre"], 2007, 2024)
        # Muestreamos anios clave para el reporte
        anios_clave = [2007, 2008, 2016, 2020, 2024]
        data["historial"] = [h for h in hist if h["anio"] in anios_clave]
    except Exception:
        pass

    # NDVI 5267
    try:
        from app.services.desvio import evaluar_desvio, UMBRAL_PCT, VENTANA_DIAS, MIN_COHORTE, MIN_ESPERADO
        d = evaluar_desvio(lote_id)
        data["ndvi"] = {
            "resultado": d,
            "config": {
                "umbral_pct": UMBRAL_PCT,
                "ventana_dias": VENTANA_DIAS,
                "min_cohorte": MIN_COHORTE,
                "min_esperado": MIN_ESPERADO,
            },
        }
    except Exception:
        pass

    # SOC (Carbono)
    try:
        from app.services import soilgrids
        res = soilgrids.soc_de_lote(row["nombre"])
        if res is None and data.get("shapely_geom") is not None:
            res = soilgrids.analyze_soc_for_geom_or_coords(
                data["lon"], data["lat"], data["shapely_geom"])
        if res:
            data["soc"] = res
    except Exception:
        pass

    # Deteccion de cultivos INTA SEPA (opcional - solo si los TIFFs estan disponibles
    # y la leyenda esta confirmada). Falla silenciosa para no romper el PDF si el
    # modulo o los TIFFs no estan.
    try:
        from app.services import cultivos_inta
        if cultivos_inta.TIFF_VERANO.exists():
            data["cultivos_verano"] = cultivos_inta.detectar_cultivos(lote_id, "verano")
        if cultivos_inta.TIFF_INVIERNO.exists():
            data["cultivos_invierno"] = cultivos_inta.detectar_cultivos(lote_id, "invierno")
    except Exception:
        pass

    return data


def generar_pdf_certificado(lote_id: int) -> bytes:
    """Genera el REPORTE SPECIMEN del lote: identificacion, cobertura historica,
    veredictos por norma (RFS2/2BSvs/RTRS/EUDR/CFR), NDVI 5267, Carbono SOC,
    biodiversidad, metodologia y hash SHA-256 canonico de auditoria.

    El hash NO cambia respecto de la version anterior: sigue derivado del
    diccionario canonico devuelto por evaluar_compliance(). Las secciones
    extendidas solo enriquecen la presentacion.
    """
    eval_res = evaluar_compliance(lote_id)
    if "error" in eval_res:
        raise ValueError("Lote no encontrado")

    canon = eval_res["canonical"]
    sha_hash = eval_res["hash"]
    fuente = eval_res.get("fuente", "simulado")
    ext = _obtener_datos_extendidos(lote_id)

    pdf = CertificadoPDF()
    pdf.fuente = fuente
    pdf.add_page()

    # Titulo del reporte
    pdf.set_font("helvetica", "B", 12)
    pdf.set_text_color(40, 40, 40)
    pdf.cell(0, 8, "REPORTE DE VERIFICACION SATELITAL Y CUMPLIMIENTO", ln=True, align="L")
    pdf.set_font("helvetica", "", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 5, f"Codigo de Verificacion Unico (Hash): {sha_hash[:16]}...", ln=True, align="L")
    pdf.cell(0, 5, f"Fecha de emision del reporte: {date.today().isoformat()} - "
                    f"Fecha de certificacion canonica: {canon.get('fecha_certificacion_canonico', '-')} - "
                    f"Motor: v{canon.get('version_algoritmo', '-')}", ln=True, align="L")
    pdf.ln(5)

    # -------- SECCION 1: Informacion general del lote --------
    _seccion_titulo(pdf, 1, "IDENTIFICACION DE LA UNIDAD PRODUCTIVA")
    _linea(pdf, f"Nombre del establecimiento: {canon['lote_nombre']}")
    _linea(pdf, f"Campo: {ext.get('campo', '-')}")
    _linea(pdf, f"Cultivo declarado: {ext.get('cultivo', '-')}")
    _linea(pdf, f"Superficie declarada: {canon['area_hectareas']:.2f} ha")
    lat = canon['coordenadas_centroide'][0]
    lon = canon['coordenadas_centroide'][1]
    _linea(pdf, f"Centroide (EPSG:4326, 5 decimales): Lat {lat:.5f} - Lon {lon:.5f}")
    _linea(pdf, f"Ubicacion geografica: Departamento Union, Provincia de Cordoba, Argentina"
           if -33.5 < lat < -32.5 and -63.5 < lon < -62.0 else
           f"Ubicacion geografica: coordenadas del centroide (ver mapa)")
    _linea(pdf, f"Fecha de alta del poligono: {ext.get('fecha_alta', '-')}")
    _linea(pdf, f"Fecha de analisis satelital: {date.today().isoformat()}")
    pdf.ln(4)

    # -------- SECCION 2: Datos espaciales - vertices --------
    _seccion_titulo(pdf, 2, "DATOS ESPACIALES DEL POLIGONO GEORREFERENCIADO")
    if ext.get("vertices"):
        _linea(pdf, f"Vertices del poligono (EPSG:4326, 5 decimales): {len(ext['vertices'])} puntos")
        # Tabla de vertices en dos columnas
        pdf.ln(1)
        _fila_tabla(pdf, [(15, "Vertice"), (35, "Longitud"), (35, "Latitud"),
                          (15, "Vertice"), (35, "Longitud"), (35, "Latitud")], header=True)
        verts = ext["vertices"]
        mitad = (len(verts) + 1) // 2
        for i in range(mitad):
            v1 = verts[i]
            v2 = verts[i + mitad] if (i + mitad) < len(verts) else None
            fila = [
                (15, f"V{i+1}"),
                (35, f"{v1[0]:.5f}"),
                (35, f"{v1[1]:.5f}"),
            ]
            if v2 is not None:
                fila.extend([
                    (15, f"V{i+mitad+1}"),
                    (35, f"{v2[0]:.5f}"),
                    (35, f"{v2[1]:.5f}"),
                ])
            else:
                fila.extend([(15, ""), (35, ""), (35, "")])
            _fila_tabla(pdf, fila)
        pdf.ln(2)
    else:
        _nota(pdf, "Geometria no disponible en base de datos.")
        pdf.ln(2)

    # -------- SECCION 3: Cobertura historica MapBiomas --------
    _seccion_titulo(pdf, 3, "COBERTURA HISTORICA DEL SUELO (MapBiomas Argentina Col.2)")
    if ext.get("historial"):
        _linea(pdf, "Serie temporal en anios clave para las fechas de corte normativas:")
        pdf.ln(1)
        _fila_tabla(pdf, [
            (18, "Anio"), (30, "Agricultura %"), (28, "Pastura %"),
            (32, "Bosque Nativo %"), (22, "Agua %"), (22, "Otros %"),
        ], header=True)
        for h in ext["historial"]:
            cob = h["cobertura"]
            _fila_tabla(pdf, [
                (18, str(h["anio"])),
                (30, f"{cob.get('Agricultura', 0):.2f}"),
                (28, f"{cob.get('Pastura', 0):.2f}"),
                (32, f"{cob.get('Bosque Nativo', 0):.2f}"),
                (22, f"{cob.get('Agua', 0):.2f}"),
                (22, f"{cob.get('Otros', 0):.2f}"),
            ])
        _nota(pdf, "Fuente: MapBiomas Argentina, Coleccion 2 (30 m/pixel, licencia CC-BY-SA). "
              "Anios seleccionados: 2007 (corte RFS2), 2008 (corte 2BSvs), 2016 (corte RTRS), "
              "2020 (corte EUDR y CFR), 2024 (imagen actual).")
        pdf.ln(3)
    else:
        _nota(pdf, "Historial de cobertura no disponible para este lote.")
        pdf.ln(2)

    # -------- SECCION 4: Evaluacion resumen de normativas --------
    _seccion_titulo(pdf, 4, "EVALUACION RESUMEN - CUMPLIMIENTO POR NORMATIVA")
    _fila_tabla(pdf, [
        (55, "Norma Regulada"),
        (35, "Fecha de Corte"),
        (55, "Veredicto Satelital"),
        (37, "Estado"),
    ], header=True)
    checks = canon["checks_compliance"]
    filas_normas = [
        ("RFS2 (EPA, EE.UU.)", "19/12/2007", checks["RFS2_2007"]),
        ("2BSvs (2BS-STD-01, UE)", "01/01/2008", checks["2BSvs_2008"]),
        ("RTRS (Soja Responsable)", "03/06/2016", checks["RTRS_2016"]),
        ("EUDR (Reg. 1115/23, UE)", "31/12/2020", checks["EUDR_2020"]),
        ("CFR (Clean Fuel Regs., Canada)", "01/07/2020", checks["CFR_2020"]),
    ]
    for nombre_norma, corte, estado in filas_normas:
        if estado == "Aprobado":
            etiqueta = "CONFORME"
            color = (16, 185, 129)
        elif estado == "Rechazado":
            etiqueta = "NO CONFORME"
            color = (239, 68, 68)
        else:
            etiqueta = "CON SALVEDAD"
            color = (217, 119, 6)
        pdf.set_font("helvetica", "", 8)
        pdf.set_text_color(40, 40, 40)
        pdf.cell(55, 5.5, nombre_norma, border=1)
        pdf.cell(35, 5.5, corte, border=1)
        pdf.cell(55, 5.5, estado[:32], border=1)
        pdf.set_text_color(*color)
        pdf.cell(37, 5.5, etiqueta, border=1, ln=True)
        pdf.set_text_color(40, 40, 40)
    pdf.ln(3)

    # -------- SECCION 5: Detalle 2BSvs --------
    d2 = canon["dosbsvs_detalle"]
    _seccion_titulo(pdf, 5, "DETALLE 2BSvs - AREA ELEGIBLE (corte 01/01/2008)")
    _linea(pdf, f"Area agricola (crop): {d2['area_agricola_ha']:.2f} ha")
    _linea(pdf, f"(-) Deforestacion post-2008: {d2['deforestacion_post_2008_ha']:.2f} ha")
    _linea(pdf, f"(-) Conversion pastizal/pastura: {d2['conversion_pastura_post_2008_ha']:.2f} ha")
    pdf.set_font("helvetica", "B", 9)
    pdf.set_text_color(40, 40, 40)
    pdf.cell(0, 5, f"(=) Area elegible: {d2['area_elegible_ha']:.2f} ha "
                    f"({d2['pct_elegible']:.2f}% del area agricola)", ln=True)
    area_total = canon['area_hectareas']
    if area_total > 0:
        _linea(pdf, f"    Area elegible sobre area TOTAL declarada: "
                    f"{d2['area_elegible_ha'] / area_total * 100:.2f}%")
    _nota(pdf, f"Biodiversidad: {d2['biodiversidad']}. "
          f"Nota metodologica: {d2['nota']}")
    pdf.ln(2)

    # -------- SECCION 6: Detalle EUDR --------
    det = canon["eudr_detalle"]
    uso = det["uso_suelo_actual"]
    pre = det["periodo_pre_2020"]
    post = det["periodo_post_2020"]
    _seccion_titulo(pdf, 6, "DETALLE EUDR - ANALISIS DOBLE PERIODO (corte 31/12/2020)")
    _linea(pdf, f"Uso de suelo actual: {uso['crop_ha']:.2f} ha agricola (crop) / "
                f"{uso['noncrop_ha']:.2f} ha no agricola (noncrop)")
    _linea(pdf, f"Periodo pre-2020 ({pre['rango']}): "
                f"{'desmonte detectado en ' + str(pre['anio_estimado']) if pre['desmonte_detectado'] else 'sin desmonte detectado'}")
    _linea(pdf, f"Categoria OTBN (Ley 26.331): {pre['categoria_otbn']}", indent=True)
    _linea(pdf, f"Periodo post-2020 ({post['rango']}, tolerancia {post['tolerancia']}): "
                f"perdida de bosque nativo {post['perdida_bosque_ha']:.2f} ha - "
                f"{'CUMPLE' if post['cumple'] else 'NO CUMPLE'}")
    _nota(pdf, "EUDR requiere geoJSON con centroide + vertices (Protocolo VISEC Anexo 1). "
          "Los vertices al 5 decimal se listan en la Seccion 2 de este reporte y son "
          "exportables en formato compatible con MRV VISEC.")
    pdf.ln(2)

    # -------- SECCION 7: Detalle RFS2 --------
    rfs2 = canon["rfs2_detalle"]
    _seccion_titulo(pdf, 7, "DETALLE RFS2 (EPA, EE.UU.) - corte 19/12/2007")
    _linea(pdf, f"Tolerancia: {rfs2['tolerancia']} (sin minimo de superficie)")
    _linea(pdf, f"Deforestacion detectada: {rfs2['deforestacion_ha']:.2f} ha")
    pdf.set_font("helvetica", "B", 9)
    pdf.cell(0, 5, f"Area elegible RFS2: {rfs2['area_elegible_ha']:.2f} ha", ln=True)
    _nota(pdf, rfs2["nota"])
    pdf.ln(2)

    # -------- SECCION 8: Detalle CFR --------
    cfr = canon["cfr_detalle"]
    _seccion_titulo(pdf, 8, "DETALLE CFR (Clean Fuel Regulations, Canada) - corte 01/07/2020")
    _linea(pdf, f"Perdida de bosque post-2020: {cfr['perdida_bosque_ha']:.2f} ha")
    _linea(pdf, f"Conversion pastizal/pastura post-2020 (Excluded Lands): "
                f"{cfr['conversion_pastura_post_2020_ha']:.2f} ha")
    _nota(pdf, f"Biodiversidad CFR: {cfr['biodiversidad']}. "
          f"CFR exige criterios LUB (Land Use & Biodiversity) e identificacion de especies RVT.")
    pdf.ln(2)

    # -------- SECCION 9: Detalle RTRS --------
    _seccion_titulo(pdf, 9, "DETALLE RTRS (Round Table on Responsible Soy) - corte 03/06/2016")
    _linea(pdf, f"Veredicto satelital: {checks['RTRS_2016']}")
    _nota(pdf, "RTRS requiere ademas verificacion de Alto Valor de Conservacion (HCV 1-6): "
          "biodiversidad, servicios ecosistemicos, valores culturales y comunidades. "
          "Las capas HCV1 (KBA/AICAS) y HCV3 (ecosistemas raros - IUCN Red List of Ecosystems) "
          "quedan marcadas como pendientes en la Seccion 12 de este reporte.")
    pdf.ln(2)

    # -------- SECCION 10: NDVI 5267 BCB --------
    _seccion_titulo(pdf, 10, "MONITOREO CONTINUO NDVI - Normativa 5267 (BCB Brasil)")
    if ext.get("ndvi") and ext["ndvi"]["resultado"].get("disponible"):
        r = ext["ndvi"]["resultado"]
        cfg = ext["ndvi"]["config"]
        _linea(pdf, f"Configuracion del motor: umbral de alerta {cfg['umbral_pct']:.0f}% "
                    f"(rango 5267 BCB: 15-30%)")
        _linea(pdf, f"Ventana temporal para asociacion cohorte: +/- {cfg['ventana_dias']} dias", indent=True)
        _linea(pdf, f"Minimo de vecinos por fecha para cohorte confiable: {cfg['min_cohorte']}", indent=True)
        _linea(pdf, f"NDVI minimo para 'ventana productiva' (evita falsas alarmas en rastrojo): "
                    f"{cfg['min_esperado']:.2f}", indent=True)
        _linea(pdf, f"Cultivo evaluado: {r.get('cultivo', '-')}")
        _linea(pdf, f"Cohorte regional efectiva: {r.get('n_cohorte_lotes', 0)} lotes del mismo cultivo")
        _linea(pdf, f"Serie temporal del lote: {len(r.get('serie', []))} observaciones satelitales")
        _linea(pdf, f"Alertas historicas del ciclo (desvio menor a -{cfg['umbral_pct']:.0f}% "
                    f"en ventana productiva): {r.get('n_alertas', 0)}")
        _linea(pdf, f"Peor desvio observado: {r.get('desvio_peor', 0):.1f}%")
        actual = r.get("actual") or {}
        if actual:
            _linea(pdf, f"Estado en la observacion mas reciente ({actual.get('fecha', '-')}): "
                        f"{r.get('estado', '-')}")
            _linea(pdf, f"NDVI actual: {actual.get('ndvi', 0):.3f} "
                        f"(esperado cohorte: {actual.get('esperado', 0):.3f}, "
                        f"desvio: {actual.get('desvio_pct', 0):.1f}%)", indent=True)
            _linea(pdf, f"Ventana productiva activa: "
                        f"{'SI' if actual.get('productiva') else 'NO (fenologia fuera de ciclo)'}", indent=True)
        _nota(pdf, "Cada observacion del lote conserva el valor NDVI CRUDO y el SUAVIZADO por despike "
              "(rechazo de puntos aislados por nube/sombra). El campo 'ndvi_crudo' de cada punto es "
              "auditable en la respuesta JSON del endpoint /api/desvio/{lote_id}. "
              "Metodo: comparativa contra mediana movil del cohorte regional, robusta a outliers.")
    else:
        _nota(pdf, "Serie NDVI insuficiente para este lote o cohorte de cultivo no disponible.")
    pdf.ln(3)

    # -------- SECCION 11: Carbono Organico del Suelo --------
    _seccion_titulo(pdf, 11, "CARBONO ORGANICO DEL SUELO (SOC) - Finanzas Verdes")
    soc = ext.get("soc")
    if soc:
        _linea(pdf, f"Fuente utilizada: {soc.source}")
        _linea(pdf, f"Densidad aparente asumida (BD): {soc.bulk_density_used} g/cm3"
               if soc.bulk_density_used > 0 else
               "Metodo: estadistica zonal sobre raster con stock ya integrado (BD implicita)")
        pdf.ln(1)
        _fila_tabla(pdf, [(30, "Profundidad"), (30, "Stock medio"),
                          (35, "IC inferior"), (35, "IC superior"), (22, "Unidad")], header=True)
        for s in soc.stocks:
            _fila_tabla(pdf, [
                (30, s.depth),
                (30, f"{s.mean:.2f}"),
                (35, f"{s.uncertainty_low:.2f}"),
                (35, f"{s.uncertainty_high:.2f}"),
                (22, s.unit),
            ])
        stock_total = sum(s.mean for s in soc.stocks)
        stock_lote = stock_total * canon['area_hectareas']
        co2e_ha = stock_total * 3.67
        co2e_total = co2e_ha * canon['area_hectareas']
        alerta_arado = co2e_total * 0.20
        pdf.ln(1)
        pdf.set_font("helvetica", "B", 9)
        pdf.cell(0, 5, f"Stock total 0-30 cm: {stock_total:.2f} t C/ha - "
                        f"{stock_lote:.2f} t C total del lote", ln=True)
        pdf.set_font("helvetica", "", 9)
        _linea(pdf, f"CO2e equivalente (factor IPCC 3.67): {co2e_ha:.2f} t CO2e/ha - "
                    f"{co2e_total:.2f} t CO2e total")
        _linea(pdf, f"Alerta de emision potencial por labranza (IPCC 20%): {alerta_arado:.2f} t CO2e")
        # Elegibilidad finanzas verdes
        if stock_total > 60.0:
            elegib = "ALTA"
            color = (16, 185, 129)
        elif stock_total >= 45.0:
            elegib = "MEDIA"
            color = (217, 119, 6)
        else:
            elegib = "BAJA"
            color = (239, 68, 68)
        pdf.set_text_color(*color)
        pdf.set_font("helvetica", "B", 9)
        pdf.cell(0, 5, f"Elegibilidad para finanzas verdes / bonos de conservacion: {elegib}", ln=True)
        pdf.set_text_color(40, 40, 40)
        _linea(pdf, f"Comparacion vs media nacional Argentina (INTA: 51.35 t C/ha): "
                    f"{'POR ENCIMA' if stock_total > 51.35 else 'POR DEBAJO'}")
        _nota(pdf, "Umbrales de elegibilidad: ALTA > 60 t C/ha, MEDIA 45-60 t C/ha, BAJA < 45 t C/ha. "
              "Alerta de arado: por labranza convencional se libera aprox. el 20% del stock como CO2 "
              "(Guidelines IPCC 2019, Vol.4 Cap.5). El dato SOC es estatico (no simulado); para MRV "
              "de creditos de carbono se requiere validacion in-situ y muestreo periodico.")
    else:
        _nota(pdf, "SOC no disponible en las fuentes locales ni SoilGrids para este lote.")
    pdf.ln(3)

    # -------- SECCION 12: Biodiversidad detallada --------
    _seccion_titulo(pdf, 12, "ANALISIS DE BIODIVERSIDAD Y AREAS DE CONSERVACION")
    _linea(pdf, "Estado de verificacion por capa (segun pliegos 2BSvs, EUDR, CFR y RTRS):")
    pdf.ln(1)
    _fila_tabla(pdf, [
        (65, "Capa / Criterio"),
        (55, "Fuente oficial"),
        (37, "Normas que la exigen"),
        (25, "Estado"),
    ], header=True)
    for capa, fuente_capa, normas, estado in _CAPAS_BIODIVERSIDAD:
        pdf.set_font("helvetica", "", 7)
        pdf.set_text_color(40, 40, 40)
        pdf.cell(65, 5, capa[:60], border=1)
        pdf.cell(55, 5, fuente_capa[:52], border=1)
        pdf.cell(37, 5, normas[:35], border=1)
        pdf.set_text_color(217, 119, 6)
        pdf.cell(25, 5, estado, border=1, ln=True)
        pdf.set_text_color(40, 40, 40)
    _nota(pdf, "Todas las capas de biodiversidad son incrementales y no bloquean la evaluacion de "
          "deforestacion satelital. La integracion completa esta prevista para la Fase 2 del PoC "
          "(estimado 30 dias de trabajo). Sin estas capas, la conformidad final ante certificadora "
          "queda con salvedad explicita en 2BSvs, EUDR (salvo criterio deforestacion puro), CFR y RTRS.")
    pdf.ln(3)

    # -------- SECCION 13: Deteccion automatica de cultivos (INTA SEPA) --------
    cv = ext.get("cultivos_verano")
    ci = ext.get("cultivos_invierno")
    if cv or ci:
        _seccion_titulo(pdf, 13, "DETECCION AUTOMATICA DE CULTIVOS (INTA SEPA - Mapa Nacional)")
        _linea(pdf, f"Fuente: INTA SEPA - Mapa Nacional de Cultivo (30 m/pixel, EPSG:4326)")
        _linea(pdf, "Metodo: mascara del poligono sobre raster de clasificacion, conteo por clase.")
        pdf.ln(1)
        for etiqueta, r in [("VERANO 2024-2025", cv), ("INVIERNO 2024-2025", ci)]:
            if not r:
                continue
            pdf.set_font("helvetica", "B", 9)
            pdf.set_text_color(40, 40, 40)
            pdf.cell(0, 5, _safe(f"Campana {etiqueta} - cobertura del poligono: "
                                 f"{r['area_detectada_ha']:.2f} ha de {r['area_declarada_ha']:.2f} ha "
                                 f"({r['cobertura_pct']:.2f}%)"), ln=True)
            _fila_tabla(pdf, [
                (18, "Codigo"),
                (72, "Cultivo / Cobertura"),
                (32, "Hectareas"),
                (28, "% del lote"),
                (32, "Confirmado"),
            ], header=True)
            for c in r["por_clase"]:
                confirmado = "Si" if r.get("leyenda_confirmada") else "Hipotesis"
                _fila_tabla(pdf, [
                    (18, str(c["codigo"])),
                    (72, c["nombre"]),
                    (32, f"{c['hectareas']:.2f}"),
                    (28, f"{c['pct']:.2f}%"),
                    (32, confirmado),
                ])
            pdf.ln(2)
        if not (cv and cv.get("leyenda_confirmada")) or not (ci and ci.get("leyenda_confirmada")):
            _nota(pdf, "Leyenda del raster INTA en proceso de confirmacion con SEPA/INTA. "
                  "Los codigos numericos y sus superficies son deterministicos; el mapeo a nombres "
                  "de cultivo se ajustara cuando se valide contra la ficha oficial del producto. "
                  "Esta seccion cierra el requisito de 'Area sembrada por cultivo' del pliego.")
        pdf.ln(2)

    # -------- SECCION 14: Metodologia --------
    _seccion_titulo(pdf, 14, "METODOLOGIA Y FUENTES DE DATOS")
    pdf.ln(1)
    _fila_tabla(pdf, [
        (55, "Componente"),
        (65, "Fuente"),
        (35, "Resolucion"),
        (27, "Periodo"),
    ], header=True)
    for componente, fuente_c, resolucion, periodo in _METODOLOGIA:
        pdf.set_font("helvetica", "", 7)
        pdf.cell(55, 5, componente[:52], border=1)
        pdf.cell(65, 5, fuente_c[:62], border=1)
        pdf.cell(35, 5, resolucion, border=1)
        pdf.cell(27, 5, periodo[:22], border=1, ln=True)
    pdf.ln(3)

    # -------- SECCION 15: Evidencia criptografica --------
    _seccion_titulo(pdf, 15, "EVIDENCIA CRIPTOGRAFICA DE AUDITORIA")
    pdf.set_font("helvetica", "", 8)
    pdf.set_text_color(40, 40, 40)
    pdf.multi_cell(0, 4,
        "Este reporte contiene un hash SHA-256 canonico e inmutable generado a partir de la "
        "estructura de datos deterministica del lote (poligono, superficie, clasificacion del suelo "
        "y veredictos por norma). Es reproducible: dado el mismo poligono y version de motor, "
        "cualquier corrida genera exactamente el mismo hash. Sirve como registro de no-alteracion "
        "y es apto para stamping en blockchain publica (Ethereum, Polygon) o registros distribuidos.")
    pdf.ln(1)
    pdf.set_font("courier", "B", 8)
    pdf.set_fill_color(245, 245, 245)
    pdf.cell(0, 8, f" SHA-256: {sha_hash}", border=1, fill=True, ln=True, align="C")
    pdf.ln(3)

    # Descargo de responsabilidad
    pdf.set_font("helvetica", "I", 8)
    pdf.set_text_color(150, 150, 150)
    if fuente == "real":
        pdf.multi_cell(0, 4,
            "NOTA: La composicion de cobertura de suelo proviene de MapBiomas Argentina "
            "(Coleccion 2, resolucion 30 m), dato publico y real (licencia CC-BY-SA). "
            "Este documento es un PROTOTIPO tecnico que demuestra la trazabilidad y la firma "
            "de auditoria; no constituye una certificacion oficial ante la Union Europea, EPA, "
            "gobierno de Canada, RTRS ni entidades certificadoras. Los veredictos surgen de "
            "comparar el ano de desmonte detectado contra las fechas de corte de cada norma. "
            "Las capas de biodiversidad (Seccion 12) son incrementales; su integracion completa "
            "esta prevista para una fase posterior.")
    elif fuente == "estimado":
        pdf.multi_cell(0, 4,
            "NOTA: Este lote todavia no tiene cobertura MapBiomas precalculada; la composicion "
            "utilizada es una ESTIMACION POR DEFECTO (campo agricola pampeano estable). Para un "
            "veredicto definitivo, recalcular la cobertura real del poligono contra el raster "
            "MapBiomas. Documento PROTOTIPO sin validez legal.")
    else:
        pdf.multi_cell(0, 4,
            "DESCARGO: Este reporte corresponde a un lote DEMO con datos de cobertura simulados. "
            "Las fechas de desmonte y veredictos son ficticios, con proposito exclusivamente "
            "ilustrativo. No posee validez legal ni representa certificaciones ambientales reales.")

    return bytes(pdf.output())
