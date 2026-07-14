---
name: react-gis-glassmorphism
description: Crea interfaces web en React + Vite con estilos Glassmorphism (vidrio esmerilado), mapas GIS de Leaflet y graficos de Recharts.
---

# react-gis-glassmorphism

Esta skill permite inicializar y diseñar interfaces web premium para proyectos SIG (Sistemas de Información Geográfica) y visualización de datos usando React, Vite, Tailwind CSS, Leaflet y Recharts, con un estilo visual "Glassmorphism" oscuro.

## Setup del Proyecto

### 1. Dependencias recomendadas (`package.json`)
```json
{
  "dependencies": {
    "leaflet": "^1.9.4",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-leaflet": "^4.2.1",
    "recharts": "^2.12.7"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.1",
    "autoprefixer": "^10.4.19",
    "postcss": "^8.4.39",
    "tailwindcss": "^3.4.7",
    "vite": "^5.3.4"
  }
}
```

### 2. Estructura de Archivos Base
Copia los archivos de plantilla de los recursos de esta skill al directorio de tu proyecto:
* Copia `resources/index.css` a `src/index.css` (estilos globales, fondos radiales oscuros y la clase `.glass`).
* Copia `resources/vite.config.js` a `vite.config.js` (servidor de desarrollo Vite y proxy preconfigurado para backend en puerto 8000).

---

## Directrices de Diseño (CSS / Tailwind)

1. **Fondo Global (Deep Dark Space)**:
   Usa un gradiente radial oscuro en el body para dar profundidad espacial:
   `background: radial-gradient(1200px 600px at 80% -10%, #13243f 0%, #0b1220 55%);`

2. **Contenedores de Vidrio (.glass)**:
   Aplica la clase `.glass` a los paneles principales (mapas, listados, gráficos) para darles el efecto translúcido:
   ```css
   .glass {
     background: rgba(255, 255, 255, 0.06);
     border: 1px solid rgba(255, 255, 255, 0.12);
     backdrop-filter: blur(12px);
   }
   ```
   Añade bordes redondeados (`rounded-2xl` o `rounded-xl`) y sombras sutiles (`shadow-lg`).

3. **Interactividad y Animaciones**:
   Aplica transiciones de opacidad suaves (`animate-fadein`) al cambiar entre vistas/paneles y añade efectos sutiles de brillo (`hover:brightness-110`) o escala (`hover:scale-102`) en elementos clicables.

---

## Estructura de Mapas (Leaflet)
* Utiliza la capa satelital `Esri.WorldImagery` como fondo para visualización agrícola:
  `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`
* Centra el mapa dinámicamente usando un componente helper con `useMap()` para ajustar los límites (`fitBounds`) a las geometrías de los lotes.

---

## Estructura de Gráficos (Recharts)
* Utiliza `LineChart` o `AreaChart` de la librería `recharts` para series de tiempo temporales.
* Configura la cuadrícula con un color tenue transparente (`stroke="#ffffff14"`).
* Diseña el `Tooltip` con estilo glassmorphism para mantener coherencia visual:
  `contentStyle={{ background: '#0b1220', border: '1px solid #ffffff22', borderRadius: 10 }}`
