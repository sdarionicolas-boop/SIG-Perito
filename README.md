---
title: Peritaje Satelital de Eventualidades
emoji: 🛰️
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
---

# Peritaje Satelital de Eventualidades

Plataforma de monitoreo y peritaje satelital de eventualidades agrícolas (helada, granizo, viento, inundación, sequía) y consistencia temporal de datos. Integra datos de Sentinel-2 L2A, Sentinel-1 SAR, ERA5-Land y GOES-19 (Overshooting Tops y GLM) sin depender de Google Earth Engine.

## Despliegue en Hugging Face Spaces

Este Space está configurado como un contenedor Docker y se compila automáticamente al subir los archivos a Hugging Face.

### Variables de Entorno (Secrets) Requeridas

Para que el descargador de imágenes de Copernicus CDSE funcione correctamente, debes configurar las siguientes variables de entorno en la pestaña **Settings** (sección *Variables and secrets*) de tu Space en Hugging Face:

1. `EODAG__COP_DATASPACE__AUTH__CREDENTIALS__USERNAME`: Tu correo de registro en Copernicus DataSpace.
2. `EODAG__COP_DATASPACE__AUTH__CREDENTIALS__PASSWORD`: Tu contraseña de registro en Copernicus DataSpace.
