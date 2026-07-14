import { useState, useEffect } from 'react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import { api } from '../api'

export default function CarbonoPanel({ loteId }) {
  const [loading, setLoading] = useState(true)
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    if (!loteId) return
    setLoading(true)
    setErr(null)
    api
      .carbono(loteId)
      .then((res) => {
        setData(res)
        setLoading(false)
      })
      .catch((e) => {
        console.error(e)
        setErr(e.message || 'Error consultando el módulo de carbono.')
        setLoading(false)
      })
  }, [loteId])

  if (err) return <Vacio>Error: {err}</Vacio>
  if (loading || !data) return <Vacio>Consultando base de carbono (INTA / SoilGrids 2.0)…</Vacio>

  // Comparación con automóviles (huella anual promedio = 4.6 t CO2)
  const autosEquivalentes = Math.round(data.alerta_emision_arado_t / 4.6)

  // Datos para el gráfico comparativo
  const chartData = [
    { name: 'Este Lote', valor: data.total_stock_0_30cm_t_c_ha, color: data.above_national_mean ? '#34d399' : '#fbbf24' },
    { name: 'Media Nac. (INTA)', valor: 51.35, color: '#94a3b8' },
    { name: 'Pastizales (Ref)', valor: 65.20, color: '#38bdf8' },
  ]

  const ELEGIBILIDAD_CLR = {
    ALTA: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
    MEDIA: 'bg-sky-500/20 text-sky-300 border-sky-500/30',
    BAJA: 'bg-rose-500/20 text-rose-300 border-rose-500/30',
  }

  return (
    <div className="h-full flex flex-col p-4 overflow-y-auto animate-fadein">
      <div className="text-xs uppercase tracking-wide text-slate-400 font-semibold mb-2">
        Carbono en Suelo (SOC) y Finanzas Verdes
      </div>

      {/* Grid superior de KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5 mb-4 shrink-0">
        
        {/* Stock COS */}
        <div className="bg-white/5 border border-white/10 rounded-xl p-3 flex flex-col">
          <span className="text-[10px] text-slate-400 font-medium uppercase">Stock COS 0-30cm</span>
          <span className="text-base font-bold text-slate-100 mt-1 tabular-nums">
            {data.total_stock_0_30cm_t_c_ha.toFixed(2)}
            <span className="text-xs font-normal text-slate-400 ml-1">t C/ha</span>
          </span>
          <span className={`text-[9px] mt-1.5 font-semibold ${data.above_national_mean ? 'text-emerald-400' : 'text-amber-400'}`}>
            {data.above_national_mean ? '✓ Sobre media nac.' : '⚠ Bajo media nac.'}
          </span>
        </div>

        {/* Equivalente CO2e por ha */}
        <div className="bg-white/5 border border-white/10 rounded-xl p-3 flex flex-col">
          <span className="text-[10px] text-slate-400 font-medium uppercase">CO₂e por ha</span>
          <span className="text-base font-bold text-sky-300 mt-1 tabular-nums">
            {data.co2e_por_ha_t.toFixed(1)}
            <span className="text-xs font-normal text-slate-400 ml-1">t CO₂e/ha</span>
          </span>
          <span className="text-[9px] text-slate-500 mt-1.5 font-semibold">Factor molecular: 3.67</span>
        </div>

        {/* CO2e Total Lote */}
        <div className="bg-white/5 border border-white/10 rounded-xl p-3 flex flex-col">
          <span className="text-[10px] text-slate-400 font-medium uppercase">Total Lote</span>
          <span className="text-base font-bold text-sky-400 mt-1 tabular-nums">
            {Math.round(data.co2e_total_t).toLocaleString('es-AR')}
            <span className="text-xs font-normal text-slate-400 ml-1">t CO₂e</span>
          </span>
          <span className="text-[9px] text-slate-500 mt-1.5 font-semibold">En todo el polígono</span>
        </div>

        {/* Pre-factibilidad (screening de línea base, NO crédito emitido) */}
        <div className="bg-white/5 border border-white/10 rounded-xl p-3 flex flex-col justify-between">
          <span className="text-[10px] text-slate-400 font-medium uppercase">Pre-factibilidad</span>
          <div className="mt-1">
            <span className={`inline-block text-[10px] font-bold border rounded-full px-2 py-0.5 uppercase tracking-wide ${ELEGIBILIDAD_CLR[data.elegibilidad]}`}>
              Línea base {data.elegibilidad}
            </span>
          </div>
          <span className="text-[9px] text-slate-500 font-semibold mt-1">Screening, no crédito emitido</span>
        </div>

      </div>

      {/* Grid Inferior: Alerta de Emisiones y Gráfico */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4 shrink-0">
        
        {/* Alerta de Pérdida por Labranza — texto según uso de suelo real */}
        <div className="bg-red-500/5 border border-red-500/20 rounded-xl p-3.5 flex flex-col justify-between">
          <div>
            <div className="flex items-center gap-1.5 font-bold text-red-400 text-[10px] uppercase tracking-wider mb-2">
              <span>⚠️</span> {data.es_pastizal ? 'Riesgo de Emisión por Conversión (IPCC)' : 'Carbono en Riesgo bajo Labranza (IPCC)'}
            </div>
            {data.es_pastizal ? (
              <p className="text-[11px] leading-relaxed text-slate-300">
                Este lote es hoy <strong className="text-slate-200">{data.pastura_pct}% pastizal/pastura</strong>. Convertirlo a agricultura de labranza liberaría aproximadamente{' '}
                <strong className="text-red-300 font-bold tabular-nums">
                  {Math.round(data.alerta_emision_arado_t).toLocaleString('es-AR')} t CO₂e
                </strong>{' '}
                por la aireación y descomposición del suelo (~20% del stock edáfico).
              </p>
            ) : (
              <p className="text-[11px] leading-relaxed text-slate-300">
                Lote ya agrícola. Bajo labranza intensiva, hasta{' '}
                <strong className="text-red-300 font-bold tabular-nums">
                  {Math.round(data.alerta_emision_arado_t).toLocaleString('es-AR')} t CO₂e
                </strong>{' '}
                del carbono edáfico están en riesgo; la <strong className="text-emerald-300">siembra directa</strong> lo protege (base para créditos de <strong className="text-slate-200">emisiones evitadas</strong>).
              </p>
            )}
          </div>
          <div className="mt-3 border-t border-red-500/10 pt-2 text-[10px] text-slate-400 leading-normal">
            🚗 Equivale a la huella de carbono anual de unos <strong className="text-slate-200">{autosEquivalentes}</strong> autos familiares en circulación.
          </div>
        </div>

        {/* Gráfico Comparativo Recharts */}
        <div className="bg-white/5 border border-white/10 rounded-xl p-3.5 flex flex-col h-[150px] lg:h-auto min-h-[140px]">
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">
            Densidad de Carbono vs Referencias (t C/ha)
          </span>
          <div className="flex-1 w-full min-h-0">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} layout="vertical" margin={{ left: -10, right: 10, top: 0, bottom: 0 }}>
                <XAxis type="number" stroke="#ffffff30" fontSize={9} />
                <YAxis dataKey="name" type="category" stroke="#ffffff30" fontSize={9} width={90} />
                <Tooltip
                  cursor={{ fill: 'rgba(255,255,255,0.03)' }}
                  contentStyle={{ background: '#0b1220', border: '1px solid #ffffff22', borderRadius: 8, fontSize: 10 }}
                  formatter={(val) => [`${val.toFixed(2)} t C/ha`, 'COS']}
                />
                <Bar dataKey="valor" radius={[0, 4, 4, 0]} barSize={12}>
                  {chartData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.color} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

      </div>

      {/* Tabla detallada de profundidades (Solo para SoilGrids fallback con múltiples capas) */}
      {data.soc_by_depth.length > 1 && (
        <div className="mb-4 shrink-0">
          <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">
            Desglose de Stock por Capas de Profundidad
          </div>
          <div className="overflow-hidden rounded-xl border border-white/10">
            <table className="w-full text-left text-xs">
              <thead className="bg-white/5 text-slate-400">
                <tr>
                  <th className="px-3 py-1.5 font-medium">Profundidad</th>
                  <th className="px-3 py-1.5 text-right font-medium">Stock Medio (t C/ha)</th>
                  <th className="px-3 py-1.5 text-right font-medium">Margen de Confianza (Q05–Q95)</th>
                </tr>
              </thead>
              <tbody>
                {data.soc_by_depth.map((d) => (
                  <tr key={d.depth} className="border-t border-white/5 text-slate-300">
                    <td className="px-3 py-1.5">{d.depth}</td>
                    <td className="px-3 py-1.5 text-right font-bold tabular-nums text-slate-200">{d.mean.toFixed(2)}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-slate-500">
                      {d.uncertainty_low.toFixed(2)} – {d.uncertainty_high.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Aclaración de adicionalidad: stock ≠ crédito */}
      <div className="text-[10px] text-amber-300/90 bg-amber-500/5 border border-amber-500/15 rounded-lg px-3 py-2 mb-2 leading-relaxed">
        <strong>Stock ≠ crédito.</strong> El valor mostrado es la <strong>línea de base</strong> de carbono ya presente en el suelo. Un crédito de carbono se emite por la captura <strong>adicional</strong> respecto a esa línea, medida en el tiempo con un proyecto certificado — no por el stock existente. Este panel es un screening de pre-factibilidad.
      </div>

      {/* Metadatos y Fuente */}
      <div className="text-[9px] text-slate-500 leading-normal border-t border-white/5 pt-2">
        * Datos estimados a partir del {data.source}.
        {data.bulk_density_used > 0 && ` Densidad aparente asumida: ${data.bulk_density_used} g/cm³.`}
        <br />
        La validación regulatoria final bajo estándares internacionales (ej. Verra) requiere calicatas de suelo y análisis químicos in situ.
      </div>
    </div>
  )
}

function Vacio({ children }) {
  return (
    <div className="h-full grid place-items-center text-slate-400 text-sm">
      {children}
    </div>
  )
}
