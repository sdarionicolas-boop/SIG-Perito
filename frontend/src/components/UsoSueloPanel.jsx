// frontend/src/components/UsoSueloPanel.jsx
import { useEffect, useState } from 'react'
import {
  PieChart, Pie, Cell, ResponsiveContainer, Legend,
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip
} from 'recharts'
import { api } from '../api'
import FuenteBadge from './FuenteBadge'

const CATEGORY_COLORS = {
  "Bosque Nativo": "#059669",  // Emerald Forest Green
  "Agricultura": "#fbbf24",    // Amber Crop Gold
  "Pastura": "#10b981",        // Light Green Grassland
  "Agua": "#3b82f6",           // Royal Blue Water
  "Otros": "#64748b"           // Slate Gray
}

export default function UsoSueloPanel({ active }) {
  const [historial, setHistorial] = useState([])
  const [fuente, setFuente] = useState('simulado')
  const [selectedAnio, setSelectedAnio] = useState(2024)
  const [cargando, setCargando] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (active == null) {
      setHistorial([])
      return
    }
    setCargando(true)
    setError(null)
    api.usoSuelo(active)
      .then((res) => {
        setHistorial(res.historial || [])
        setFuente(res.fuente || 'simulado')
        setCargando(false)
      })
      .catch((e) => {
        setError(e.message)
        setCargando(false)
      })
  }, [active])

  if (active == null) {
    return (
      <div className="h-full flex items-center justify-center p-8 text-center text-slate-400">
        <p className="text-sm">Seleccioná un lote para analizar el historial de cobertura de suelo (MapBiomas).</p>
      </div>
    )
  }

  if (cargando) {
    return (
      <div className="h-full flex items-center justify-center p-8 text-slate-400">
        <p className="text-sm">Cargando datos de cobertura...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="h-full flex items-center justify-center p-8 text-rose-400">
        <p className="text-sm">Error al cargar cobertura: {error}</p>
      </div>
    )
  }

  // Obtener los datos del año seleccionado para el PieChart
  const datosAnio = historial.find(h => h.anio === selectedAnio)
  const pieData = datosAnio 
    ? Object.entries(datosAnio.cobertura).map(([name, value]) => ({ name, value }))
    : []

  // Formatear datos para el AreaChart de transición
  const areaData = historial.map(h => {
    const row = { anio: h.anio }
    Object.entries(h.cobertura).forEach(([k, v]) => {
      row[k] = v
    })
    return row
  })

  // Detectar si hubo desmonte post 2020 para alerta visual
  const bosque2020 = historial.find(h => h.anio === 2020)?.cobertura["Bosque Nativo"] || 0
  const bosque2024 = historial.find(h => h.anio === 2024)?.cobertura["Bosque Nativo"] || 0
  const deforestadoPost2020 = bosque2020 > 0 && bosque2024 < bosque2020

  return (
    <div className="h-full flex flex-col p-4 overflow-y-auto animate-fadein">
      {/* Cabecera y Marca de Agua de Simulación */}
      <div className="flex items-center justify-between border-b border-white/10 pb-2 mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-slate-400 font-semibold">
            Uso de Suelo · Historial Cobertura (MapBiomas)
          </span>
          <FuenteBadge fuente={fuente} />
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 flex-1 min-h-0">
        {/* Lado Izquierdo: Cobertura por Año Seleccionado */}
        <div className="flex flex-col bg-white/5 border border-white/10 rounded-xl p-3">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-slate-300 font-bold">Composición de Suelo: Año {selectedAnio}</span>
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-slate-400">Año:</span>
              <input 
                type="range" 
                min="2018" 
                max="2024" 
                value={selectedAnio}
                onChange={(e) => setSelectedAnio(Number(e.target.value))}
                className="w-24 accent-emerald-400 cursor-pointer"
              />
            </div>
          </div>
          
          <div className="flex-1 min-h-[140px] flex items-center justify-center">
            {pieData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={pieData}
                    cx="50%"
                    cy="50%"
                    innerRadius={30}
                    outerRadius={55}
                    paddingAngle={2}
                    dataKey="value"
                  >
                    {pieData.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={CATEGORY_COLORS[entry.name] || "#ccc"} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(value) => `${value}%`} contentStyle={{ background: '#0b1220', border: '1px solid #ffffff22', borderRadius: 10, fontSize: 11 }} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-xs text-slate-400">No hay datos disponibles.</p>
            )}
          </div>
        </div>

        {/* Lado Derecho: Línea de tiempo de transición */}
        <div className="flex flex-col bg-white/5 border border-white/10 rounded-xl p-3">
          <span className="text-xs text-slate-300 font-bold mb-2">Transición de Cobertura (Histórico 2018-2024)</span>
          <div className="flex-1 min-h-[140px]">
            {areaData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={areaData} margin={{ top: 5, right: 10, bottom: 28, left: -20 }}>
                  <CartesianGrid stroke="#ffffff14" />
                  <XAxis dataKey="anio" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                  <YAxis unit="%" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                  <Tooltip formatter={(value) => `${value}%`} contentStyle={{ background: '#0b1220', border: '1px solid #ffffff22', borderRadius: 10, fontSize: 11 }} />
                  <Area type="monotone" dataKey="Bosque Nativo" stackId="1" stroke={CATEGORY_COLORS["Bosque Nativo"]} fill={CATEGORY_COLORS["Bosque Nativo"]} fillOpacity={0.6} />
                  <Area type="monotone" dataKey="Agricultura" stackId="1" stroke={CATEGORY_COLORS["Agricultura"]} fill={CATEGORY_COLORS["Agricultura"]} fillOpacity={0.6} />
                  <Area type="monotone" dataKey="Pastura" stackId="1" stroke={CATEGORY_COLORS["Pastura"]} fill={CATEGORY_COLORS["Pastura"]} fillOpacity={0.6} />
                  <Area type="monotone" dataKey="Otros" stackId="1" stroke={CATEGORY_COLORS["Otros"]} fill={CATEGORY_COLORS["Otros"]} fillOpacity={0.6} />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-xs text-slate-400">Cargando gráfico histórico...</p>
            )}
          </div>
        </div>
      </div>

      {/* Cartel de Alerta Fuerte si hubo desmonte */}
      {deforestadoPost2020 && (
        <div className="mt-3 bg-rose-500/10 border border-rose-500/30 text-rose-300 text-[11px] rounded-lg px-3 py-2 flex items-center gap-2 animate-pulse">
          <span>⚠️</span>
          <span><b>ALERTA DE DESMONTE:</b> Pérdida de cobertura forestal nativa detectada entre 2020 y 2024. Este campo infringe el reglamento de libre deforestación (EUDR).</span>
        </div>
      )}
    </div>
  )
}
