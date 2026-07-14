import { useEffect, useState } from 'react'
import L from 'leaflet'
import {
  MapContainer, TileLayer, GeoJSON, ImageOverlay, Polygon, CircleMarker,
  useMap, useMapEvents,
} from 'react-leaflet'
import { api, COLORS } from '../api'

function FitBounds({ fc }) {
  const map = useMap()
  useEffect(() => {
    if (!fc || !fc.features?.length) return
    const layer = L.geoJSON(fc)
    map.fitBounds(layer.getBounds(), { padding: [30, 30] })
  }, [fc, map])
  return null
}

// Cambia el cursor del mapa a cruz mientras se dibuja.
function DrawCursor({ drawing }) {
  const map = useMap()
  useEffect(() => {
    const el = map.getContainer()
    el.style.cursor = drawing ? 'crosshair' : ''
    return () => { el.style.cursor = '' }
  }, [drawing, map])
  return null
}

function DrawHandler({ drawing, pts, setPts }) {
  useMapEvents({
    click(e) {
      if (drawing) setPts((p) => [...p, [e.latlng.lat, e.latlng.lng]])
    },
  })
  if (!pts.length) return null
  return (
    <>
      <Polygon positions={pts} pathOptions={{ color: '#fbbf24', weight: 2, dashArray: '5,4' }} />
      {pts.map((p, i) => (
        <CircleMarker
          key={i}
          center={p}
          radius={5}
          pathOptions={{ color: '#fbbf24', fillColor: '#fde68a', fillOpacity: 1, weight: 2 }}
        />
      ))}
    </>
  )
}

// version: cambia cuando se recalcula una zonificación -> fuerza re-fetch del overlay.
function ZonifOverlay({ active, version }) {
  const [z, setZ] = useState(null)
  useEffect(() => {
    let cancel = false
    setZ(null)
    if (active != null) {
      api.zonif(active).then((d) => !cancel && setZ(d)).catch(() => {})
    }
    return () => { cancel = true }
  }, [active, version])
  if (!z) return null
  return <ImageOverlay url={z.png_url} bounds={z.bounds} opacity={0.75} />
}

// Mapa de severidad del último peritaje del lote activo (mismo patrón que
// ZonifOverlay). `version` se bumpea al completar un peritaje nuevo -> re-fetch.
function PeritajeOverlay({ active, version }) {
  const [res, setRes] = useState(null)
  useEffect(() => {
    let cancel = false
    setRes(null)
    if (active != null) {
      api.peritaje(active).then((d) => !cancel && setRes(d)).catch(() => {})
    }
    return () => { cancel = true }
  }, [active, version])
  if (!res?.outputs?.severidad_png || !res?.bounds) return null
  return <ImageOverlay url={res.outputs.severidad_png} bounds={res.bounds} opacity={0.7} />
}

export default function MapView({ fc, active, selected, onActivar, drawing, onSaveDraw, zonifVersion, peritajeVersion }) {
  const [pts, setPts] = useState([])

  useEffect(() => { if (!drawing) setPts([]) }, [drawing])

  const styleFor = (feature) => {
    const id = feature.properties.id
    const idx = selected.indexOf(id)
    const isActive = id === active
    return {
      color: isActive ? '#fbbf24' : idx >= 0 ? COLORS[idx % COLORS.length] : '#34d399',
      weight: isActive ? 4 : 2,
      fillOpacity: isActive ? 0.28 : idx >= 0 ? 0.18 : 0.08,
    }
  }

  return (
    <div className="relative h-full">
      <MapContainer center={[-32.3, -63.4]} zoom={10} className="h-full w-full" zoomControl>
        <TileLayer
          url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
          attribution="Esri World Imagery"
          maxZoom={19}
        />
        {fc && (
          <GeoJSON
            key={`${active}-${selected.join(',')}`}
            data={fc}
            style={styleFor}
            onEachFeature={(f, layer) => {
              layer.bindTooltip(f.properties.nombre.replace(/^Lote_\d+_/, ''), { sticky: true })
              layer.on('click', () => onActivar(f.properties.id))
            }}
          />
        )}
        {active != null && <ZonifOverlay active={active} version={zonifVersion} />}
        {active != null && <PeritajeOverlay active={active} version={peritajeVersion} />}
        <FitBounds fc={fc} />
        <DrawCursor drawing={drawing} />
        <DrawHandler drawing={drawing} pts={pts} setPts={setPts} />
      </MapContainer>

      {drawing && (
        <div className="absolute top-3 left-3 z-[1000] glass rounded-xl px-3 py-2 flex items-center gap-2">
          <span className="text-xs text-slate-300">{pts.length} vértices</span>
          <button
            onClick={() => setPts((p) => p.slice(0, -1))}
            disabled={!pts.length}
            title="Deshacer último vértice"
            className="rounded-lg bg-white/10 px-2.5 py-1 text-xs disabled:opacity-40"
          >
            ↶ Deshacer
          </button>
          <button
            onClick={() => setPts([])}
            disabled={!pts.length}
            className="rounded-lg bg-white/10 px-2.5 py-1 text-xs disabled:opacity-40"
          >
            Limpiar
          </button>
          <button
            disabled={pts.length < 3}
            onClick={() => {
              const ring = pts.map(([lat, lng]) => [lng, lat])
              ring.push(ring[0]) // cerrar anillo
              onSaveDraw(ring)
            }}
            className="rounded-lg bg-emerald-400 text-emerald-950 px-2.5 py-1 text-xs font-bold disabled:opacity-40"
          >
            Guardar lote
          </button>
        </div>
      )}
    </div>
  )
}
