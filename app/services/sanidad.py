"""Motor de riesgo de enfermedades (sanidad fitosanitaria), portado de GEE.

`ENFERMEDADES_EXTENSIVOS` y las funciones de scoring son una transcripción fiel
del visor GEE original (`_calcRiesgoEnf`, `_nivelRiesgo`, `_matchEtapa`,
`_factores`). Diferencias respecto del original:
  * El clima sale de Open-Meteo (forecast `past_days=15`), no de ERA5; el
    adaptador `_clima_lote` entrega ya las unidades que el motor espera
    (tC en °C, hVol en %vol, pMm en mm/15d).
  * Se agregó el cultivo `mani` (Viruela, Carbón, Esclerotinia) — no estaba en
    GEE, autoría local (INTA Manfredi), marcado en `src`.
Claves de zona válidas: norte, centro, coeste, sudeste, sudoeste.
"""
import datetime as dt
import json

import requests

from app.database import db_cursor, get_conn

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
TIMEZONE = "America/Argentina/Cordoba"
PAST_DAYS = 15
ZONAS = ["norte", "centro", "coeste", "sudeste", "sudoeste"]

# === ENFERMEDADES_EXTENSIVOS (porteo fiel del GEE + maní local) ===
ENFERMEDADES_EXTENSIVOS = {
    "trigo": [
        {"id": "fhb_trigo", "n": "Fusariosis de la Espiga (FHB)", "a": "Fusarium graminearum / F. culmorum",
         "t": "hongo", "tMin": 15, "tMax": 30, "hUmbral": 22, "pUmbral": 25, "etC": ["Espigaz", "Antesis", "Grano lech"],
         "zR": {"norte": 3, "centro": 4, "coeste": 2, "sudeste": 3, "sudoeste": 2},
         "tto": "Fungicida triazol+estrobilurina en BBCH 60-65 (antesis).",
         "med": "Monitorear pronóstico de lluvia en antesis. Evitar siembra tardía post-antesis húmedo.",
         "src": "INTA Marcos Juárez / SINAVIMO 2024"},
        {"id": "roya_hoja", "n": "Roya de la Hoja (Anaranjada)", "a": "Puccinia triticina",
         "t": "hongo", "tMin": 10, "tMax": 25, "hUmbral": 20, "pUmbral": 15, "etC": ["Encañaz", "Espigaz", "Antesis", "Grano"],
         "zR": {"norte": 3, "centro": 3, "coeste": 2, "sudeste": 4, "sudoeste": 3},
         "tto": "Triazol (tebuconazole, propiconazole) o mezcla al inicio del ataque.",
         "med": "Elegir variedades con resistencia específica. Monitorear a partir de macollaje.",
         "src": "INTA Barrow / AAPRESID Red de Ensayos Trigo 2023/24"},
        {"id": "roya_amar", "n": "Roya Amarilla (de la Raya)", "a": "Puccinia striiformis f.sp. tritici",
         "t": "hongo", "tMin": 4, "tMax": 15, "hUmbral": 18, "pUmbral": 12, "etC": ["Macollaje", "Encañaz", "Espigaz"],
         "zR": {"norte": 1, "centro": 2, "coeste": 1, "sudeste": 3, "sudoeste": 2},
         "tto": "Triazol de amplio espectro en primavera fresca. Actuación preventiva.",
         "med": "Mayor riesgo en años con primaveras frescas y húmedas (La Niña debilitada).",
         "src": "INTA Barrow / SENASA Alertas Fitosanitarias"},
        {"id": "septoriosis", "n": "Septoriosis (Mancha de la Hoja)", "a": "Zymoseptoria tritici (Septoria tritici)",
         "t": "hongo", "tMin": 10, "tMax": 22, "hUmbral": 24, "pUmbral": 20, "etC": ["Macollaje", "Encañaz"],
         "zR": {"norte": 2, "centro": 3, "coeste": 2, "sudeste": 3, "sudoeste": 2},
         "tto": "Fungicida en Z31-Z39 (encañazón). Variedades con buena resistencia parcial.",
         "med": "Disemina por salpique de lluvia. Alta húmedad foliar clave.",
         "src": "INTA Marcos Juárez / FAUBA Cereales de Invierno"},
        {"id": "man_amar", "n": "Mancha Amarilla (Tan spot)", "a": "Drechslera tritici-repentis (DTR)",
         "t": "hongo", "tMin": 12, "tMax": 28, "hUmbral": 20, "pUmbral": 15, "etC": ["Encañaz", "Espigaz"],
         "zR": {"norte": 3, "centro": 3, "coeste": 3, "sudeste": 2, "sudoeste": 2},
         "tto": "Fungicida en encañazón. Rotación con no-gramíneas. Manejo de rastrojo.",
         "med": "Riesgo mayor sobre rastrojo de trigo/cebada del ciclo anterior.",
         "src": "INTA Marcos Juárez / SINAVIMO"},
        {"id": "oidio_trigo", "n": "Oídio del Trigo", "a": "Blumeria graminis f.sp. tritici",
         "t": "hongo", "tMin": 7, "tMax": 20, "hUmbral": 15, "pUmbral": 8, "etC": ["Macollaje", "Encañaz"],
         "zR": {"norte": 2, "centro": 2, "coeste": 1, "sudeste": 2, "sudoeste": 1},
         "tto": "Fungicida específico en macollaje si cobertura > 10% de la hoja bandera.",
         "med": "Densidades altas y N excesivo favorecen el oídio.",
         "src": "INTA Balcarce / SENASA"},
    ],
    "cebada": [
        {"id": "fhb_cebada", "n": "Fusariosis de la Espiga", "a": "Fusarium graminearum",
         "t": "hongo", "tMin": 15, "tMax": 30, "hUmbral": 22, "pUmbral": 25, "etC": ["Espigaz", "Antesis", "Grano lech"],
         "zR": {"norte": 3, "centro": 4, "coeste": 2, "sudeste": 4, "sudoeste": 2},
         "tto": "Fungicida triazol en BBCH 60-65. El riesgo de DON hace crítico el timing.",
         "med": "Monitorear lluvia en antesis. Clave para calidad maltera (DON < 1 ppm).",
         "src": "INTA Barrow / Asociación de Productores de Malta"},
        {"id": "escaldadura", "n": "Escaldadura de la Hoja", "a": "Rhynchosporium commune",
         "t": "hongo", "tMin": 5, "tMax": 15, "hUmbral": 22, "pUmbral": 15, "etC": ["Macollaje", "Encañaz"],
         "zR": {"norte": 2, "centro": 2, "coeste": 2, "sudeste": 3, "sudoeste": 3},
         "tto": "Fungicida en macollaje. Variedades de ciclo corto con escape fenológico.",
         "med": "Mayor presión en inviernos fríos-húmedos del sudeste bonaerense.",
         "src": "INTA Barrow / AAPRESID"},
        {"id": "man_red", "n": "Mancha en Red (NFNB)", "a": "Pyrenophora teres f.sp. teres",
         "t": "hongo", "tMin": 10, "tMax": 22, "hUmbral": 20, "pUmbral": 15, "etC": ["Encañaz", "Espigaz"],
         "zR": {"norte": 3, "centro": 3, "coeste": 2, "sudeste": 3, "sudoeste": 2},
         "tto": "Fungicida en Z31-Z39. Monitorear en años húmedos con frecuentes rocíos.",
         "med": "Transmitida por semilla (tratamiento previo obligatorio en cebada maltera).",
         "src": "INTA Marcos Juárez / SENASA"},
        {"id": "roya_ceb", "n": "Roya de la Hoja de Cebada", "a": "Puccinia hordei",
         "t": "hongo", "tMin": 10, "tMax": 22, "hUmbral": 18, "pUmbral": 12, "etC": ["Encañaz", "Espigaz", "Grano"],
         "zR": {"norte": 3, "centro": 3, "coeste": 2, "sudeste": 4, "sudoeste": 2},
         "tto": "Triazol en inicio de síntomas. Muy sensible a fungicidas.",
         "med": "Monitorear hoja bandera en primavera. Variedades con Rph genes.",
         "src": "INTA Barrow / SINAVIMO 2024"},
    ],
    "colza": [
        {"id": "esclero_col", "n": "Esclerotinia (Podredumbre Blanca)", "a": "Sclerotinia sclerotiorum",
         "t": "hongo", "tMin": 12, "tMax": 22, "hUmbral": 25, "pUmbral": 30, "etC": ["Floración", "Siliqua"],
         "zR": {"norte": 3, "centro": 3, "coeste": 2, "sudeste": 4, "sudoeste": 2},
         "tto": "Fungicida en inicio de floración (BBCH 60-65). Protetante obligatorio en SE.",
         "med": "INSPECTOR (RAGT) o Hyola 571 CL: mejor comportamiento sanitario en SE bonaerense.",
         "src": "INTA Barrow / ASAGIR 2024"},
        {"id": "altern_col", "n": "Alternariosis (Mancha Negra)", "a": "Alternaria brassicae / A. alternata",
         "t": "hongo", "tMin": 20, "tMax": 28, "hUmbral": 20, "pUmbral": 20, "etC": ["Floración", "Siliqua", "Mad"],
         "zR": {"norte": 3, "centro": 3, "coeste": 3, "sudeste": 2, "sudoeste": 2},
         "tto": "Fungicida en R. Manejo de densidad para mejorar aireación del canopeo.",
         "med": "Afecta rendimiento en aceite. Alta en años lluviosos de primavera tardía.",
         "src": "INTA Marcos Juárez / SENASA"},
        {"id": "mildiu_col", "n": "Mildiu de la Colza", "a": "Peronospora brassicae",
         "t": "hongo", "tMin": 8, "tMax": 18, "hUmbral": 25, "pUmbral": 20, "etC": ["Emergencia", "Roseta"],
         "zR": {"norte": 2, "centro": 2, "coeste": 1, "sudeste": 2, "sudoeste": 1},
         "tto": "Fungicida sistémico (metalaxil) + tratamiento de semilla preventivo.",
         "med": "Afecta principalmente plántulas. Mayor riesgo en siembras tempranas frías.",
         "src": "INTA Bordenave / SINAVIMO"},
    ],
    "maiz": [
        {"id": "mgr_maiz", "n": "Mancha Gris de Hoja (GLS)", "a": "Cercospora zeae-maydis",
         "t": "hongo", "tMin": 20, "tMax": 30, "hUmbral": 26, "pUmbral": 30, "etC": ["V12", "VT", "R1", "R2", "R3"],
         "zR": {"norte": 3, "centro": 3, "coeste": 2, "sudeste": 2, "sudoeste": 1},
         "tto": "Fungicida estrobilurina+triazol en V12-VT (priorizar alto NDVI/dosel cerrado).",
         "med": "Mayor presión en lotes con labranza directa y rastrojo de maíz. Consultar NDRE.",
         "src": "INTA Marcos Juárez / SINAVIMO 2023/24"},
        {"id": "tizon_maiz", "n": "Tizón Foliar del Norte (NCLB)", "a": "Setosphaeria turcica",
         "t": "hongo", "tMin": 18, "tMax": 27, "hUmbral": 24, "pUmbral": 25, "etC": ["Encañaz", "V12", "VT", "R1"],
         "zR": {"norte": 4, "centro": 3, "coeste": 2, "sudeste": 2, "sudoeste": 1},
         "tto": "Fungicida en V8-V12 en lotes con historia sanitaria. Híbridos con Ht1+Ht2.",
         "med": "Epidemia severa reduce rendimiento hasta 50%. Revisar NDVI en VT.",
         "src": "INTA Pergamino / Syngenta Pathology Report 2024"},
        {"id": "spiroplasma", "n": "Achaparramiento del Maíz (MSV)", "a": "Spiroplasma kunkelii (Dalbulus maidis)",
         "t": "virus", "tMin": 22, "tMax": 34, "hUmbral": 15, "pUmbral": 5, "etC": ["VE", "V3", "V6"],
         "zR": {"norte": 4, "centro": 3, "coeste": 2, "sudeste": 1, "sudoeste": 1},
         "tto": "Control de Dalbulus maidis (insecticida en V3-V5). No hay cura.",
         "med": "Siembra temprana (sep) escapa al pico de chicharrita. Variedades tolerantes DEKALB.",
         "src": "INTA Pergamino / SINAVIMO Alerta 2023/24"},
        {"id": "pud_cana", "n": "Pudrición de Caña (Giberela)", "a": "Gibberella stalkrot / Fusarium moniliforme",
         "t": "hongo", "tMin": 20, "tMax": 30, "hUmbral": 20, "pUmbral": 15, "etC": ["R4", "R5", "R6"],
         "zR": {"norte": 2, "centro": 3, "coeste": 2, "sudeste": 2, "sudoeste": 2},
         "tto": "No hay tratamiento curativo. Prevenir con buen manejo agronómico y N.",
         "med": "Estrés hídrico post-floración predispone. Cosechar antes de lodging.",
         "src": "INTA Marcos Juárez / AAPRESID"},
    ],
    "maiz_tardio": [
        {"id": "mgr_tardio", "n": "Mancha Gris de Hoja (GLS) — tardío", "a": "Cercospora zeae-maydis",
         "t": "hongo", "tMin": 20, "tMax": 30, "hUmbral": 26, "pUmbral": 30, "etC": ["V12", "VT", "R1", "R2"],
         "zR": {"norte": 4, "centro": 4, "coeste": 2, "sudeste": 2, "sudoeste": 1},
         "tto": "Fungicida preventivo antes de VT. El maíz tardío coincide con mayor presión húmeda.",
         "med": "El tardío florece en feb-mar: coincide con temperaturas y humedad óptimas para GLS.",
         "src": "INTA Pergamino / AAPRESID Maíces tardíos 2024"},
        {"id": "tizon_tardio", "n": "Tizón Foliar del Norte — tardío", "a": "Setosphaeria turcica",
         "t": "hongo", "tMin": 18, "tMax": 27, "hUmbral": 24, "pUmbral": 25, "etC": ["V12", "VT", "R1"],
         "zR": {"norte": 4, "centro": 3, "coeste": 2, "sudeste": 2, "sudoeste": 1},
         "tto": "Monitoreo semanal desde V6. Fungicida en V8-V12 si hay lesiones.",
         "med": "Floración en verano tardío puede coincidir con período óptimo del patógeno.",
         "src": "INTA Pergamino / SENASA"},
        {"id": "pud_cana_t", "n": "Pudrición de Caña + Lodging", "a": "Gibberella stalkrot + fusarium spp.",
         "t": "hongo", "tMin": 20, "tMax": 30, "hUmbral": 20, "pUmbral": 15, "etC": ["R4", "R5", "R6"],
         "zR": {"norte": 3, "centro": 3, "coeste": 2, "sudeste": 2, "sudoeste": 1},
         "tto": "Cosechar en R5 tardío para evitar lodging. Monitorear base de tallo.",
         "med": "El tardío tiene mayor exposición al estrés hídrico de fin de verano.",
         "src": "INTA Marcos Juárez / AAPRESID"},
    ],
    "girasol": [
        {"id": "esclero_giras", "n": "Esclerotinia (Podredumbre Blanca)", "a": "Sclerotinia sclerotiorum",
         "t": "hongo", "tMin": 12, "tMax": 22, "hUmbral": 25, "pUmbral": 30, "etC": ["R1", "R2", "R3", "R4", "R5"],
         "zR": {"norte": 3, "centro": 3, "coeste": 2, "sudeste": 4, "sudoeste": 3},
         "tto": "Fungicida preventivo en inicio de floración. Semilla con trichoderma.",
         "med": "Rotación mínima 4 años con cultivos no hospedantes. Evitar altas densidades.",
         "src": "INTA Balcarce / ASAGIR 2024"},
        {"id": "verticilosis", "n": "Verticilosis (Marchitamiento)", "a": "Verticillium dahliae",
         "t": "hongo", "tMin": 15, "tMax": 25, "hUmbral": 20, "pUmbral": 15, "etC": ["R3", "R4", "R5", "R6"],
         "zR": {"norte": 2, "centro": 3, "coeste": 3, "sudeste": 3, "sudoeste": 3},
         "tto": "Sin control químico efectivo post-infección. Variedades tolerantes (Syn/Advanta).",
         "med": "Suelos franco-arenosos del SO bonaerense son los de mayor riesgo histórico.",
         "src": "INTA Bordenave / ASAGIR"},
        {"id": "mildiu_giras", "n": "Mildiu del Girasol", "a": "Plasmopara halstedii",
         "t": "hongo", "tMin": 10, "tMax": 20, "hUmbral": 24, "pUmbral": 20, "etC": ["Emergencia", "V4", "V6"],
         "zR": {"norte": 2, "centro": 2, "coeste": 2, "sudeste": 2, "sudoeste": 2},
         "tto": "Tratamiento de semilla obligatorio (metalaxil + inhibidores de ergosterol).",
         "med": "Resistencia a metalaxil detectada en algunas razas. Usar mezclas con fungicidas.",
         "src": "INTA Balcarce / SENASA"},
        {"id": "roya_giras", "n": "Roya del Girasol", "a": "Puccinia helianthi",
         "t": "hongo", "tMin": 15, "tMax": 28, "hUmbral": 18, "pUmbral": 15, "etC": ["R1", "R2", "R3", "R4"],
         "zR": {"norte": 3, "centro": 2, "coeste": 2, "sudeste": 2, "sudoeste": 2},
         "tto": "Fungicida triazol en R1-R2 si incidencia > 5% hoja bandera.",
         "med": "Monitorear hoja bandera a partir de botón floral. Variedades con Pl genes.",
         "src": "INTA Balcarce / SINAVIMO"},
    ],
    "sorgo": [
        {"id": "carbon_sorgo", "n": "Carbón Cubierto del Sorgo", "a": "Sporisorium sorghi",
         "t": "hongo", "tMin": 18, "tMax": 28, "hUmbral": 20, "pUmbral": 15, "etC": ["Emergencia", "V3", "Panojamiento"],
         "zR": {"norte": 3, "centro": 3, "coeste": 3, "sudeste": 2, "sudoeste": 2},
         "tto": "Tratamiento de semilla obligatorio (carboxin + thiram).",
         "med": "El carbón cubierto puede anular el rinde completo en lotes sin semilla tratada.",
         "src": "INTA Marcos Juárez / SINAVIMO"},
        {"id": "antracnosis", "n": "Antracnosis del Sorgo", "a": "Colletotrichum sublineolum",
         "t": "hongo", "tMin": 22, "tMax": 32, "hUmbral": 24, "pUmbral": 25, "etC": ["V8", "Panojamiento", "Floración"],
         "zR": {"norte": 4, "centro": 3, "coeste": 2, "sudeste": 2, "sudoeste": 1},
         "tto": "Fungicida en panojamiento. Híbridos con resistencia específica.",
         "med": "Alta temperatura + humedad en norte bonaerense y NEA: condición óptima.",
         "src": "INTA Pergamino / SENASA"},
        {"id": "estria_bact", "n": "Estría Bacteriana", "a": "Burkholderia andropogonis",
         "t": "bacteria", "tMin": 24, "tMax": 35, "hUmbral": 20, "pUmbral": 15, "etC": ["V6", "V8", "Panojamiento"],
         "zR": {"norte": 3, "centro": 2, "coeste": 2, "sudeste": 1, "sudoeste": 1},
         "tto": "No hay control químico efectivo. Semilla certificada y variedades tolerantes.",
         "med": "Diseminada por semilla y viento. Favorecer aireación con menor densidad.",
         "src": "INTA Marcos Juárez / SINAVIMO"},
    ],
    "soja_1": [
        {"id": "roya_soja1", "n": "Roya Asiática", "a": "Phakopsora pachyrhizi",
         "t": "hongo", "tMin": 18, "tMax": 28, "hUmbral": 24, "pUmbral": 20, "etC": ["R1", "R2", "R3", "R4", "R5"],
         "zR": {"norte": 4, "centro": 3, "coeste": 1, "sudeste": 1, "sudoeste": 1},
         "tto": "Fungicida triazol+estrobilurina en R1. Monitoreo semanal en norte desde emergencia.",
         "med": "Avanza desde el norte. Verificar alertas SINAVIMO/SENASA semanalmente en nov-feb.",
         "src": "SINAVIMO / SENASA — Red Alerta Roya 2024"},
        {"id": "esclero_soja1", "n": "Esclerotinia (Podredumbre Blanca)", "a": "Sclerotinia sclerotiorum",
         "t": "hongo", "tMin": 12, "tMax": 24, "hUmbral": 26, "pUmbral": 30, "etC": ["R1", "R2", "R3"],
         "zR": {"norte": 3, "centro": 3, "coeste": 1, "sudeste": 3, "sudoeste": 2},
         "tto": "Fungicida en R1. En años con suelo saturado en floración: obligatorio SE.",
         "med": "Alta incidencia correlacionada con lluvias frecuentes en diciembre-enero.",
         "src": "INTA Marcos Juárez / SINAVIMO 2024"},
        {"id": "isariop_soja", "n": "Mancha Marrón (Septoriosis)", "a": "Septoria glycines",
         "t": "hongo", "tMin": 20, "tMax": 30, "hUmbral": 22, "pUmbral": 18, "etC": ["V6", "R1", "R2"],
         "zR": {"norte": 3, "centro": 3, "coeste": 2, "sudeste": 2, "sudoeste": 1},
         "tto": "Fungicida en R1 integrado al plan sanitario general.",
         "med": "A menudo acompaña a roya y esclerotinia. Aumenta bajo dosel cerrado.",
         "src": "INTA Marcos Juárez / AAPRESID"},
        {"id": "pud_raiz_soja", "n": "Pudrición Radicular por Phytophthora", "a": "Phytophthora sojae",
         "t": "hongo", "tMin": 15, "tMax": 28, "hUmbral": 28, "pUmbral": 30, "etC": ["VE", "V3", "V6"],
         "zR": {"norte": 3, "centro": 3, "coeste": 2, "sudeste": 2, "sudoeste": 2},
         "tto": "Tratamiento de semilla (metalaxil+sedaxane). Drenaje. Variedades tolerantes.",
         "med": "La infección ocurre en siembra fría y húmeda. Los síntomas aparecen tardíamente.",
         "src": "INTA Marcos Juárez / FAUBA"},
        {"id": "fomopsis", "n": "Cancro del Tallo / Fomopsis", "a": "Diaporthe phaseolorum var. caulivora",
         "t": "hongo", "tMin": 20, "tMax": 30, "hUmbral": 22, "pUmbral": 20, "etC": ["R3", "R4", "R5"],
         "zR": {"norte": 3, "centro": 2, "coeste": 2, "sudeste": 2, "sudoeste": 1},
         "tto": "Fungicida preventivo en R3. Tratamiento de semilla y rotación amplia.",
         "med": "Síntomas: chancros marrón en la base del tallo. Monitorear en R3-R5.",
         "src": "INTA / SINAVIMO"},
        {"id": "cj_soja", "n": "Mancha Ojo de Rana (MOR)", "a": "Cercospora sojina",
         "t": "hongo", "tMin": 22, "tMax": 32, "hUmbral": 24, "pUmbral": 20, "etC": ["R3", "R4", "R5", "R6"],
         "zR": {"norte": 3, "centro": 2, "coeste": 2, "sudeste": 1, "sudoeste": 1},
         "tto": "Fungicida triazol en R3 si incidencia supera umbral. Semilla sana.",
         "med": "Transmitida por semilla. Más intensa en años húmedos cálidos del norte.",
         "src": "INTA Marcos Juárez / AAPRESID"},
    ],
    "soja_2": [
        {"id": "esclero_soja2", "n": "Esclerotinia (Podredumbre Blanca)", "a": "Sclerotinia sclerotiorum",
         "t": "hongo", "tMin": 12, "tMax": 24, "hUmbral": 26, "pUmbral": 30, "etC": ["R1", "R2", "R3"],
         "zR": {"norte": 3, "centro": 3, "coeste": 1, "sudeste": 2, "sudoeste": 1},
         "tto": "Fungicida en R1 obligatorio en años con suelo húmedo en enero.",
         "med": "La soja 2da tiene mayor coincidencia floración-enero húmedo: más riesgo.",
         "src": "INTA Marcos Juárez / SINAVIMO 2024"},
        {"id": "roya_soja2", "n": "Roya Asiática", "a": "Phakopsora pachyrhizi",
         "t": "hongo", "tMin": 18, "tMax": 28, "hUmbral": 24, "pUmbral": 20, "etC": ["R1", "R2", "R3", "R4"],
         "zR": {"norte": 4, "centro": 3, "coeste": 1, "sudeste": 1, "sudoeste": 1},
         "tto": "Fungicida triazol+estrobilurina. Monitoreo semanal desde emergencia en norte.",
         "med": "La 2da soja florece en feb-mar: máxima presión de roya en norte bonaerense.",
         "src": "SINAVIMO / SENASA — Red Alerta Roya 2024"},
        {"id": "fomopsis_2", "n": "Cancro del Tallo + Fomopsis", "a": "Diaporthe phaseolorum",
         "t": "hongo", "tMin": 20, "tMax": 30, "hUmbral": 22, "pUmbral": 20, "etC": ["R3", "R4", "R5"],
         "zR": {"norte": 3, "centro": 2, "coeste": 1, "sudeste": 1, "sudoeste": 1},
         "tto": "Fungicida en R3. Tratamiento de semilla. La 2da soja sobre trigo tiene mayor inóculo.",
         "med": "El antecesor trigo con rastrojo puede actuar como fuente de inóculo primario.",
         "src": "INTA / AAPRESID"},
        {"id": "pudvaina_2", "n": "Tizón de la Vaina y Grano", "a": "Phomopsis longicolla",
         "t": "hongo", "tMin": 24, "tMax": 32, "hUmbral": 24, "pUmbral": 20, "etC": ["R6", "R7", "R8"],
         "zR": {"norte": 3, "centro": 2, "coeste": 1, "sudeste": 1, "sudoeste": 1},
         "tto": "Cosechar en madurez fisiológica. Fungicida en R5. Semilla tratada.",
         "med": "La 2da soja con cosecha tardía en mayo-junio húmedo eleva el riesgo.",
         "src": "INTA Marcos Juárez / SENASA"},
    ],
    # === Maní (agregado local, NO presente en GEE) ===
    "mani": [
        {"id": "viruela_mani", "n": "Viruela Tardía del Maní", "a": "Nothopassalora personata",
         "t": "hongo", "tMin": 20, "tMax": 30, "hUmbral": 24, "pUmbral": 25, "etC": ["R3", "R4", "R5", "R6"],
         "zR": {"norte": 2, "centro": 4, "coeste": 4, "sudeste": 2, "sudoeste": 1},
         "tto": "Fungicida triazol+estrobilurina desde R3; repetir según monitoreo foliar.",
         "med": "Defoliación temprana reduce rinde y calidad. Rotación y manejo de rastrojo.",
         "src": "INTA Manfredi — agregado local (no GEE)"},
        {"id": "carbon_mani", "n": "Carbón del Maní", "a": "Thecaphora frezii",
         "t": "hongo", "tMin": 22, "tMax": 30, "hUmbral": 20, "pUmbral": 20, "etC": ["Clavado", "R1", "R2"],
         "zR": {"norte": 2, "centro": 4, "coeste": 4, "sudeste": 1, "sudoeste": 1},
         "tto": "Sin cura química eficaz: semilla/genética tolerante + rotación ≥3 años.",
         "med": "Enfermedad clave en Córdoba; infecta en clavado/fructificación. Evitar monocultivo.",
         "src": "INTA Manfredi — agregado local (no GEE)"},
        {"id": "esclero_mani", "n": "Podredumbre por Esclerotinia", "a": "Sclerotinia minor",
         "t": "hongo", "tMin": 12, "tMax": 25, "hUmbral": 26, "pUmbral": 30, "etC": ["R3", "R4", "R5"],
         "zR": {"norte": 2, "centro": 3, "coeste": 3, "sudeste": 3, "sudoeste": 2},
         "tto": "Fungicida en R3 ante canopeo húmedo cerrado; evitar exceso de densidad.",
         "med": "Favorecida por humedad prolongada del canopeo. Manejar aireación y riego.",
         "src": "INTA Manfredi — agregado local (no GEE)"},
    ],
}

# Alias de nombres de cultivo de la BD hacia las claves del diccionario.
ALIAS_CULTIVO = {
    "mani": "mani", "maní": "mani",
    "soja": "soja_1", "soja1": "soja_1", "soja 1ra": "soja_1",
    "soja2": "soja_2", "soja 2da": "soja_2",
    "maiz": "maiz", "maíz": "maiz", "maiz tardio": "maiz_tardio",
}


# === Motor de scoring (porteo fiel del GEE) ===
def _match_etapa(etapa, keywords) -> bool:
    if not etapa or not keywords:
        return False
    return any(k in etapa for k in keywords)


def _calc_riesgo(enf, zona, etapa, clima, espectral) -> int:
    base_zona = enf["zR"].get(zona, 0) * 15 if zona else 0
    score_clima = 0
    if clima:
        tC, hVol, pMm = clima["tC"], clima["hVol"], clima["pMm"]
        score_clima += 13 if (enf["tMin"] <= tC <= enf["tMax"]) else (
            6 if (enf["tMin"] - 4 <= tC <= enf["tMax"] + 4) else 0)
        score_clima += 12 if (hVol >= enf["hUmbral"] and pMm >= enf["pUmbral"]) else (
            6 if (hVol >= enf["hUmbral"] or pMm >= enf["pUmbral"]) else 0)
    score_feno = 20 if _match_etapa(etapa, enf["etC"]) else 0
    score_esp = 0
    if espectral:
        if espectral.get("ndwi") is not None and espectral["ndwi"] > 0.05:
            score_esp += 5
        if espectral.get("ndvi") is not None and espectral["ndvi"] > 0.65:
            score_esp += 3
    if not clima:
        score_base = base_zona * 0.6 + score_feno + score_esp
    else:
        score_base = base_zona * (0.5 + (score_clima / 25) * 0.7) + score_clima * 0.3 + score_feno + score_esp
    return min(100, max(0, round(score_base)))


def _nivel(score) -> dict:
    if score >= 75:
        return {"lbl": "Muy Alto", "emoji": "🔴", "color": "#f87171", "ord": 4}
    if score >= 50:
        return {"lbl": "Alto", "emoji": "🟠", "color": "#fb923c", "ord": 3}
    if score >= 28:
        return {"lbl": "Moderado", "emoji": "🟡", "color": "#fbbf24", "ord": 2}
    return {"lbl": "Bajo", "emoji": "🟢", "color": "#34d399", "ord": 1}


def _factores(enf, zona, etapa, clima, espectral) -> list[dict]:
    fax = []
    if clima:
        tC, hVol, pMm = clima["tC"], clima["hVol"], clima["pMm"]
        fax.append({"ok": enf["tMin"] <= tC <= enf["tMax"],
                    "label": f"T° {tC:.1f}°C (óptimo {enf['tMin']}–{enf['tMax']}°C)"})
        fax.append({"ok": hVol >= enf["hUmbral"],
                    "label": f"Hum. suelo {hVol:.1f}%vol (umbral ≥{enf['hUmbral']})"})
        fax.append({"ok": pMm >= enf["pUmbral"],
                    "label": f"Precip. {pMm:.0f} mm/15d (umbral ≥{enf['pUmbral']})"})
    if etapa:
        fax.append({"ok": _match_etapa(etapa, enf["etC"]),
                    "label": f"Etapa crítica: {' / '.join(enf['etC'])}"})
    if espectral and espectral.get("ndwi") is not None:
        fax.append({"ok": espectral["ndwi"] > 0.05,
                    "label": f"NDWI canopeo {espectral['ndwi']:.3f} (húmedo >0.05)"})
    if espectral and espectral.get("ndvi") is not None:
        fax.append({"ok": espectral["ndvi"] > 0.65,
                    "label": f"NDVI {espectral['ndvi']:.3f} (denso >0.65)"})
    return fax


# === Datos: clima (Open-Meteo) y espectral (BD) ===
def _auto_zona(lat, lon) -> str:
    """Heurística simple lat/lon → zona fitosanitaria (override manual recomendado)."""
    if lat is None or lon is None:
        return "centro"
    if lon <= -63.0:
        return "coeste"          # franja centro-oeste (Córdoba manisera)
    if lat > -34.0:
        return "norte"
    if lat < -37.0:
        return "sudoeste" if lon < -61.5 else "sudeste"
    return "centro"


def _agg_clima(hourly: dict) -> dict | None:
    """Agrega el bloque `hourly` de Open-Meteo a {tC, hVol, pMm, dias}."""
    temps = [t for t in hourly.get("temperature_2m", []) if t is not None]
    precs = [p for p in hourly.get("precipitation", []) if p is not None]
    sms = [s for s in hourly.get("soil_moisture_0_to_10cm", []) if s is not None]
    if not temps:
        return None
    return {
        "tC": round(sum(temps) / len(temps), 2),
        "hVol": round((sum(sms) / len(sms) * 100) if sms else 0.0, 2),
        "pMm": round(sum(precs), 1),
        "dias": PAST_DAYS,
    }


def _cache_clima(lote_id: int, datos: dict) -> None:
    if lote_id is None or not datos:
        return
    with db_cursor() as conn:
        conn.execute("INSERT OR REPLACE INTO clima_cache (lote_id, fecha, datos) VALUES (?, ?, ?)",
                     (lote_id, dt.date.today().isoformat(), json.dumps(datos)))


def procesar_clima_data(raw: dict, lote_id: int | None = None) -> dict | None:
    """Agrega el JSON crudo de Open-Meteo (provisto por el frontend) y lo cachea.

    Permite evitar el rate-limit 429 de Open-Meteo en HF: el navegador consulta
    con la IP del usuario y el backend solo procesa.
    """
    datos = _agg_clima(raw.get("hourly", {}))
    _cache_clima(lote_id, datos)
    return datos


def _clima_lote(lote_id: int, lat: float, lon: float) -> dict | None:
    """Clima de los últimos 15 días (Open-Meteo forecast past_days), cacheado por día.

    Devuelve {tC (°C, media), hVol (%vol, media), pMm (mm, suma 15d), dias}.
    Camino del lado servidor (fallback); en HF preferir el POST con datos del cliente.
    """
    hoy = dt.date.today().isoformat()
    conn = get_conn()
    try:
        row = conn.execute("SELECT datos FROM clima_cache WHERE lote_id=? AND fecha=?",
                           (lote_id, hoy)).fetchone()
    finally:
        conn.close()
    if row:
        return json.loads(row["datos"])

    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,precipitation,soil_moisture_0_to_10cm",
        "past_days": PAST_DAYS, "forecast_days": 0, "timezone": TIMEZONE,
    }
    resp = requests.get(OPEN_METEO, params=params, timeout=30)
    resp.raise_for_status()
    datos = _agg_clima(resp.json().get("hourly", {}))
    _cache_clima(lote_id, datos)
    return datos


def _espectral_lote(lote_id: int) -> dict:
    """NDVI y NDWI más recientes del lote (de las series en BD)."""
    conn = get_conn()
    try:
        def ultimo(indice):
            r = conn.execute(
                "SELECT valor FROM series_temporales WHERE lote_id=? AND indice=? "
                "ORDER BY fecha DESC LIMIT 1", (lote_id, indice)).fetchone()
            return r["valor"] if r else None
        return {"ndvi": ultimo("NDVI"), "ndwi": ultimo("NDWI")}
    finally:
        conn.close()


def evaluar_riesgo_lote(lote_id: int, cultivo: str | None = None,
                        zona: str | None = None, etapa: str | None = None,
                        clima: dict | None = None) -> dict:
    """Evalúa el riesgo de enfermedades de un lote. Lanza ValueError si no existe.

    Si `clima` viene provisto (POST con datos del navegador), se usa directamente
    y se evita la consulta server-side a Open-Meteo (bypass del 429 en HF).
    """
    conn = get_conn()
    try:
        lote = conn.execute(
            "SELECT nombre, cultivo, centroide_lat, centroide_lon FROM lotes WHERE id=?",
            (lote_id,)).fetchone()
    finally:
        conn.close()
    if not lote:
        raise ValueError("Lote no encontrado.")

    cultivo_in = (cultivo or lote["cultivo"] or "mani").strip().lower()
    cultivo_key = ALIAS_CULTIVO.get(cultivo_in, cultivo_in)
    patogenos = ENFERMEDADES_EXTENSIVOS.get(cultivo_key)

    lat, lon = lote["centroide_lat"], lote["centroide_lon"]
    zona = zona or _auto_zona(lat, lon)

    clima_err = None
    if clima is None:  # camino server-side (fallback local); en HF llega vía POST
        try:
            clima = _clima_lote(lote_id, lat, lon)
        except Exception as exc:  # noqa: BLE001 — degradar a scoring sin clima
            clima_err = str(exc)[:200]

    espectral = _espectral_lote(lote_id)

    enfermedades = []
    if patogenos:
        for enf in patogenos:
            score = _calc_riesgo(enf, zona, etapa, clima, espectral)
            niv = _nivel(score)
            enfermedades.append({
                "id": enf["id"], "nombre": enf["n"], "agente": enf["a"], "tipo": enf["t"],
                "score": score, "nivel": niv["lbl"], "emoji": niv["emoji"],
                "color": niv["color"], "orden": niv["ord"],
                "factores": _factores(enf, zona, etapa, clima, espectral),
                "tratamiento": enf["tto"], "medida": enf["med"], "fuente": enf["src"],
            })
        enfermedades.sort(key=lambda e: e["score"], reverse=True)

    alerta = enfermedades[0] if enfermedades else None
    return {
        "lote_id": lote_id, "nombre": lote["nombre"],
        "cultivo": cultivo_key, "cultivo_disponible": patogenos is not None,
        "zona": zona, "etapa": etapa,
        "clima": clima, "clima_error": clima_err, "espectral": espectral,
        "alerta_global": {"nivel": alerta["nivel"], "emoji": alerta["emoji"],
                          "score": alerta["score"], "color": alerta["color"],
                          "enfermedad": alerta["nombre"]} if alerta else None,
        "enfermedades": enfermedades,
        "cultivos_disponibles": sorted(ENFERMEDADES_EXTENSIVOS.keys()),
        "zonas": ZONAS,
    }
