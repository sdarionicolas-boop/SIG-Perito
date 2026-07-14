import { useEffect, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { api, COLORS } from '../api'

// Combina las series NDVI de varios lotes en filas {fecha, [nombre]: valor}.
function combinar(seriesPorLote) {
  const porFecha = {}
  for (const { nombre, puntos } of seriesPorLote) {
    for (const p of puntos) {
      porFecha[p.fecha] = porFecha[p.fecha] || { fecha: p.fecha }
      porFecha[p.fecha][nombre] = p.valor
    }
  }
  return Object.values(porFecha).sort((a, b) => a.fecha.localeCompare(b.fecha))
}

export default function SerieChart({ lotes, selected }) {
  const [data, setData] = useState([])
  const [nombres, setNombres] = useState([])
  const [cargando, setCargando] = useState(false)
  const [selectedIndice, setSelectedIndice] = useState('NDVI')

  useEffect(() => {
    if (!selected.length) { setData([]); setNombres([]); return }
    let cancel = false
    setCargando(true)
    Promise.all(
      selected.map(async (id) => {
        const lote = lotes.find((l) => l.id === id)
        const nombre = (lote?.nombre || `Lote ${id}`).replace(/^Lote_\d+_/, '')
        const s = await api.serie(id, selectedIndice).catch(() => ({ puntos: [] }))
        return { nombre, puntos: s.puntos || [] }
      })
    ).then((series) => {
      if (cancel) return
      setNombres(series.map((s) => s.nombre))
      setData(combinar(series))
      setCargando(false)
    })
    return () => { cancel = true }
  }, [selected, lotes, selectedIndice])

  return (
    <div className="h-full flex flex-col p-4">
      <div className="flex items-center justify-between border-b border-white/10 pb-2 mb-3">
        <span className="text-xs uppercase tracking-wide text-slate-400 font-semibold">
          Serie temporal · comparación multi-lote
        </span>
        {selected.length > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-400">Índice:</span>
            <select
              value={selectedIndice}
              onChange={(e) => setSelectedIndice(e.target.value)}
              className="bg-black/30 border border-white/10 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-emerald-400/50"
            >
              <option value="NDVI">🌱 NDVI (Vigor)</option>
              <option value="NDWI">💧 NDWI (Agua/Humedad)</option>
              <option value="RVI">🛰️ RVI (Biomasa Radar / Cosecha)</option>
            </select>
          </div>
        )}
      </div>

      {!selected.length ? (
        <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-slate-400">
          <svg className="w-12 h-12 text-slate-500 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
          </svg>
          <h3 className="text-base font-bold text-slate-300 mb-1">Bienvenido al SIG Agrícola</h3>
          <p className="text-xs max-w-md">
            Seleccioná uno o más lotes de la barra lateral para comparar sus curvas de NDVI, NDWI y Radar instantáneamente, o bien dibujá tu propio campo de forma 100% confidencial usando el botón <b>✏️ Dibujar lote</b>.
          </p>
        </div>
      ) : (
        <div className="flex-1 min-h-0">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 8, right: 16, bottom: 28, left: -8 }}>
              <CartesianGrid stroke="#ffffff14" />
              <XAxis dataKey="fecha" tick={{ fill: '#94a3b8', fontSize: 11 }} minTickGap={32} />
              <YAxis domain={['auto', 'auto']} tick={{ fill: '#94a3b8', fontSize: 11 }} />
              <Tooltip
                contentStyle={{ background: '#0b1220', border: '1px solid #ffffff22', borderRadius: 10 }}
                labelStyle={{ color: '#e5e7eb' }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {nombres.map((n, i) => (
                <Line
                  key={n}
                  type="monotone"
                  dataKey={n}
                  stroke={COLORS[i % COLORS.length]}
                  dot={false}
                  strokeWidth={2}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      {cargando && <div className="text-xs text-slate-500 mt-1">Cargando series…</div>}
    </div>
  )
}
